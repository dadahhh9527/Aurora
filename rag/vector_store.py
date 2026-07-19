import json
import hashlib
import os
import threading
import time
import warnings

from langchain_chroma import Chroma
from langchain_core.documents import Document
from utils.config_handler import chroma_conf
from model.factory import embed_model
from langchain_text_splitters import RecursiveCharacterTextSplitter
from utils.path_tool import get_abs_path
from utils.file_handler import pdf_loader, txt_loader, listdir_with_allowed_type, get_file_md5_hex
from utils.logger_handler import logger


class VectorStoreService:
    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        # Reuse one Chroma client throughout the process.
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if VectorStoreService._initialized:
            return
        VectorStoreService._initialized = True

        self.vector_store = Chroma(
            collection_name=chroma_conf["collection_name"],
            embedding_function=embed_model,
            persist_directory=get_abs_path(chroma_conf["persist_directory"]),
        )

        self.spliter = RecursiveCharacterTextSplitter(
            chunk_size=chroma_conf["chunk_size"],
            chunk_overlap=chroma_conf["chunk_overlap"],
            separators=chroma_conf["separators"],
            length_function=len,
        )
        # Queries wait during updates so they never observe a partially updated store.
        self._operation_lock = threading.RLock()
        self._status_lock = threading.Lock()
        self._status = {
            "running": False,
            "last_started_at": None,
            "last_finished_at": None,
            "last_result": None,
            "last_error": None,
        }

    def search_with_scores(self, query: str, k: int) -> list[tuple[Document, float]]:
        """
        Return a list of (document, relevance_score); higher is more relevant.
        Used upstream for threshold filtering / reranking.

        Chroma emits a noisy UserWarning when irrelevant docs get a relevance score
        slightly outside [0, 1]; the value is still usable for ordering/thresholding,
        so we silence just that warning to keep the console clean.
        """
        with self._operation_lock:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Relevance scores must be between 0 and 1")
                return self.vector_store.similarity_search_with_relevance_scores(query, k=k)

    def _md5_store_path(self) -> str:
        return get_abs_path(chroma_conf["md5_hex_store"])

    def _load_md5_map(self) -> dict:
        """Load the source-to-MD5 manifest and flag legacy data for rebuilding."""
        self._manifest_requires_rebuild = False
        path = self._md5_store_path()
        if not os.path.exists(path):
            self._manifest_requires_rebuild = True
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            self._manifest_requires_rebuild = True
            logger.warning("[load KB] legacy md5 list detected; rebuilding collection")
            return {}
        except (json.JSONDecodeError, OSError):
            logger.warning("[load KB] legacy md5 record detected; re-validating by source to clean stale data")
            self._manifest_requires_rebuild = True
            return {}

    def _save_md5_map(self, md5_map: dict) -> None:
        path = self._md5_store_path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(md5_map, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)

    @staticmethod
    def _source_id(path: str, data_root: str) -> str:
        return os.path.relpath(path, data_root).replace("\\", "/")

    def status(self) -> dict:
        with self._status_lock:
            return dict(self._status)

    def delete_by_source(self, source: str) -> None:
        """Delete all vector chunks associated with a source file."""
        try:
            self.vector_store._collection.delete(where={"source": source})
        except Exception as e:
            logger.warning(f"[load KB] failed to delete old vectors source={source} err={str(e)}")

    def load_document(self, *, trigger: str = "manual", wait: bool = True) -> dict:
        """
        Synchronize local knowledge files to Chroma.

        Source IDs and MD5 hashes make the operation incremental. Unchanged files
        are skipped, while additions, updates, and deletions are applied safely.
        """

        acquired = self._operation_lock.acquire(blocking=wait)
        if not acquired:
            return {"status": "busy", "trigger": trigger}

        started_at = time.time()
        with self._status_lock:
            self._status.update(
                running=True,
                last_started_at=started_at,
                last_error=None,
            )

        result = {
            "status": "ok",
            "trigger": trigger,
            "added": 0,
            "updated": 0,
            "deleted": 0,
            "unchanged": 0,
            "failed": 0,
        }

        def get_file_documents(read_path: str):
            suffix = os.path.splitext(read_path)[1].lower()
            if suffix == ".txt":
                return txt_loader(read_path)
            if suffix == ".pdf":
                return pdf_loader(read_path)
            return []

        try:
            data_root = get_abs_path(chroma_conf["data_path"])
            allowed_files_path: tuple[...] = listdir_with_allowed_type(
                data_root,
                tuple(chroma_conf["allow_knowledge_file_type"]),
            )
            old_map = self._load_md5_map()
            new_map: dict[str, str] = {}
            current_sources: set[str] = set()
            current_legacy_paths = set(allowed_files_path)
            rebuilt_ids: set[str] = set()

            for path in allowed_files_path:
                source = self._source_id(path, data_root)
                current_sources.add(source)
                md5_hex = get_file_md5_hex(path)
                legacy_md5 = old_map.get(path)
                previous_md5 = old_map.get(source)

                if not md5_hex:
                    logger.warning(f"[load KB] {source} MD5 computation failed, skipping")
                    result["failed"] += 1
                    if previous_md5:
                        new_map[source] = previous_md5
                    continue

                # Migrate legacy absolute source paths so host paths never leak.
                if previous_md5 == md5_hex and legacy_md5 is None:
                    new_map[source] = md5_hex
                    result["unchanged"] += 1
                    continue

                try:
                    documents: list[Document] = get_file_documents(path)
                    for document in documents:
                        document.metadata["source"] = source
                        document.metadata["source_md5"] = md5_hex

                    if not documents:
                        raise ValueError("file has no valid text content")

                    split_document: list[Document] = self.spliter.split_documents(documents)
                    if not split_document:
                        raise ValueError("file produced no chunks after splitting")

                    # Add the new version before deleting old IDs so failures preserve data.
                    old_ids = self.vector_store._collection.get(
                        where={"source": source}, include=[]
                    ).get("ids", [])
                    if legacy_md5 is not None or self._manifest_requires_rebuild:
                        legacy_ids = self.vector_store._collection.get(
                            where={"source": path}, include=[]
                        ).get("ids", [])
                    else:
                        legacy_ids = []
                    ids = [
                        hashlib.sha256(
                            f"{source}:{md5_hex}:{index}".encode("utf-8")
                        ).hexdigest()
                        for index in range(len(split_document))
                    ]
                    self.vector_store.add_documents(split_document, ids=ids)
                    rebuilt_ids.update(ids)
                    stale_ids = [item for item in old_ids + legacy_ids if item not in ids]
                    if stale_ids:
                        self.vector_store._collection.delete(ids=stale_ids)

                    new_map[source] = md5_hex
                    if previous_md5 is not None or legacy_md5 is not None:
                        result["updated"] += 1
                    else:
                        result["added"] += 1
                    logger.info(
                        "[load KB] %s loaded successfully (%s chunks)",
                        source,
                        len(split_document),
                    )
                except Exception as e:
                    result["failed"] += 1
                    logger.error(
                        f"[load KB] {source} failed to load: {str(e)}",
                        exc_info=True,
                    )
                    if previous_md5:
                        new_map[source] = previous_md5
                    elif legacy_md5:
                        new_map[path] = legacy_md5

            # Remove sources that no longer exist, including legacy absolute paths.
            for stored_source in set(old_map) - current_sources - current_legacy_paths:
                self.delete_by_source(stored_source)
                result["deleted"] += 1

            # A legacy or missing manifest may contain unknown stale sources. Remove
            # them only after every current source has rebuilt successfully.
            if self._manifest_requires_rebuild and result["failed"] == 0:
                all_ids = self.vector_store._collection.get(include=[]).get("ids", [])
                orphan_ids = [item for item in all_ids if item not in rebuilt_ids]
                if orphan_ids:
                    self.vector_store._collection.delete(ids=orphan_ids)

            self._save_md5_map(new_map)
            if result["failed"]:
                result["status"] = "partial"
                with self._status_lock:
                    self._status["last_error"] = (
                        f"{result['failed']} knowledge file(s) failed to update"
                    )
            logger.info("[load KB] scan complete trigger=%s result=%s", trigger, result)
            return result
        except Exception as exc:
            result["status"] = "error"
            with self._status_lock:
                self._status["last_error"] = str(exc)
            logger.error("[load KB] scan failed trigger=%s err=%s", trigger, exc, exc_info=True)
            return result
        finally:
            finished_at = time.time()
            with self._status_lock:
                self._status.update(
                    running=False,
                    last_finished_at=finished_at,
                    last_result=result,
                )
            self._operation_lock.release()


if __name__ == "__main__":
    VectorStoreService().load_document(trigger="cli")


