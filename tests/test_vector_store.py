import json
import threading

from langchain_core.documents import Document

import rag.vector_store as vector_module
from rag.vector_store import VectorStoreService


class _Collection:
    def __init__(self):
        self.items = {}

    def get(self, where=None, include=None):
        source = (where or {}).get("source")
        return {
            "ids": [
                item_id
                for item_id, document in self.items.items()
                if source is None or document.metadata.get("source") == source
            ]
        }

    def delete(self, ids=None, where=None):
        if ids is not None:
            for item_id in ids:
                self.items.pop(item_id, None)
            return
        source = (where or {}).get("source")
        for item_id in list(self.items):
            if self.items[item_id].metadata.get("source") == source:
                self.items.pop(item_id)


class _VectorStore:
    def __init__(self):
        self._collection = _Collection()

    def add_documents(self, documents, ids):
        for item_id, document in zip(ids, documents):
            self._collection.items[item_id] = document


class _Splitter:
    def split_documents(self, documents):
        return documents


def _service():
    service = object.__new__(VectorStoreService)
    service.vector_store = _VectorStore()
    service.spliter = _Splitter()
    service._operation_lock = threading.RLock()
    service._status_lock = threading.Lock()
    service._status = {
        "running": False,
        "last_started_at": None,
        "last_finished_at": None,
        "last_result": None,
        "last_error": None,
    }
    return service


def test_incremental_scan_add_update_skip_and_delete(tmp_path, monkeypatch):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    manifest = tmp_path / "manifest.json"
    source_file = knowledge / "guide.txt"
    source_file.write_text("version one", encoding="utf-8")

    monkeypatch.setitem(vector_module.chroma_conf, "data_path", str(knowledge))
    monkeypatch.setitem(vector_module.chroma_conf, "md5_hex_store", str(manifest))
    monkeypatch.setitem(
        vector_module.chroma_conf, "allow_knowledge_file_type", ["txt", "pdf"]
    )
    monkeypatch.setattr(
        vector_module,
        "txt_loader",
        lambda path: [Document(page_content=open(path, encoding="utf-8").read())],
    )
    service = _service()

    first = service.load_document(trigger="test")
    assert first["added"] == 1
    assert len(service.vector_store._collection.items) == 1

    second = service.load_document(trigger="test")
    assert second["unchanged"] == 1
    assert len(service.vector_store._collection.items) == 1

    source_file.write_text("version two", encoding="utf-8")
    third = service.load_document(trigger="test")
    assert third["updated"] == 1
    assert len(service.vector_store._collection.items) == 1
    assert next(iter(service.vector_store._collection.items.values())).page_content == "version two"

    source_file.unlink()
    fourth = service.load_document(trigger="test")
    assert fourth["deleted"] == 1
    assert service.vector_store._collection.items == {}
    assert json.loads(manifest.read_text(encoding="utf-8")) == {}


def test_failed_legacy_rebuild_preserves_existing_vectors(tmp_path, monkeypatch):
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text("[]", encoding="utf-8")
    (knowledge / "guide.txt").write_text("new content", encoding="utf-8")

    monkeypatch.setitem(vector_module.chroma_conf, "data_path", str(knowledge))
    monkeypatch.setitem(vector_module.chroma_conf, "md5_hex_store", str(manifest))
    monkeypatch.setitem(
        vector_module.chroma_conf, "allow_knowledge_file_type", ["txt", "pdf"]
    )
    monkeypatch.setattr(
        vector_module,
        "txt_loader",
        lambda _path: (_ for _ in ()).throw(RuntimeError("embedding input failed")),
    )
    service = _service()
    service.vector_store._collection.items["old-id"] = Document(
        page_content="existing knowledge",
        metadata={"source": "legacy-deleted-source.txt"},
    )

    result = service.load_document(trigger="test")
    assert result["status"] == "partial"
    assert "old-id" in service.vector_store._collection.items
