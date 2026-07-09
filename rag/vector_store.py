import json
import os
import sys
import warnings

# 把项目根目录加入 sys.path，保证直接运行本脚本时也能 import utils / model 等顶层包
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
        # 单例：全局共用一个向量库连接，避免重复创建 Chroma 客户端
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

    def get_retriever(self):
        return self.vector_store.as_retriever(search_kwargs={"k": chroma_conf["k"]})

    def search_with_scores(self, query: str, k: int) -> list[tuple[Document, float]]:
        """
        Return a list of (document, relevance_score); higher is more relevant.
        Used upstream for threshold filtering / reranking.

        Chroma emits a noisy UserWarning when irrelevant docs get a relevance score
        slightly outside [0, 1]; the value is still usable for ordering/thresholding,
        so we silence just that warning to keep the console clean.
        """
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Relevance scores must be between 0 and 1")
            return self.vector_store.similarity_search_with_relevance_scores(query, k=k)

    def _md5_store_path(self) -> str:
        return get_abs_path(chroma_conf["md5_hex_store"])

    def _load_md5_map(self) -> dict:
        """读取 {来源文件路径: md5} 映射。兼容旧的纯 md5 列表（读不出就当空，触发一次重建）。"""
        path = self._md5_store_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            logger.warning("[load KB] legacy md5 record detected; re-validating by source to clean stale data")
            return {}

    def _save_md5_map(self, md5_map: dict) -> None:
        with open(self._md5_store_path(), "w", encoding="utf-8") as f:
            json.dump(md5_map, f, ensure_ascii=False, indent=2)

    def delete_by_source(self, source: str) -> None:
        """按来源文件删除向量库中已有的分片，避免文件更新后旧内容残留。"""
        try:
            self.vector_store._collection.delete(where={"source": source})
        except Exception as e:
            logger.warning(f"[load KB] failed to delete old vectors source={source} err={str(e)}")

    def load_document(self):
        """
        从数据文件夹读取知识文件 -> 切分 -> 向量入库。
        以「来源文件 + MD5」做增量：未变的跳过；变更/新增的先删旧向量再写入新的。
        """

        def get_file_documents(read_path: str):
            if read_path.endswith("txt"):
                return txt_loader(read_path)
            if read_path.endswith("pdf"):
                return pdf_loader(read_path)
            return []

        allowed_files_path: tuple[...] = listdir_with_allowed_type(
            get_abs_path(chroma_conf["data_path"]),
            tuple(chroma_conf["allow_knowledge_file_type"]),
        )

        md5_map = self._load_md5_map()

        for path in allowed_files_path:
            md5_hex = get_file_md5_hex(path)

            if not md5_hex:
                logger.warning(f"[load KB] {path} MD5 computation failed, skipping")
                continue

            if md5_map.get(path) == md5_hex:
                logger.info(f"[load KB] {path} unchanged, skipping")
                continue

            try:
                documents: list[Document] = get_file_documents(path)

                if not documents:
                    logger.warning(f"[load KB] {path} has no valid text content, skipping")
                    continue

                split_document: list[Document] = self.spliter.split_documents(documents)

                if not split_document:
                    logger.warning(f"[load KB] {path} produced no chunks after splitting, skipping")
                    continue

                # On update, delete this source's old vectors before writing the new ones (avoid duplicates)
                if path in md5_map:
                    self.delete_by_source(path)

                self.vector_store.add_documents(split_document)

                # Record the source and its latest md5
                md5_map[path] = md5_hex
                self._save_md5_map(md5_map)

                logger.info(f"[load KB] {path} loaded successfully ({len(split_document)} chunks)")
            except Exception as e:
                logger.error(f"[load KB] {path} failed to load: {str(e)}", exc_info=True)
                continue


if __name__ == '__main__':
    vs = VectorStoreService()

    vs.load_document()

    retriever = vs.get_retriever()

    res = retriever.invoke("robot gets lost while navigating")
    for r in res:
        print(r.page_content)
        print("-"*20)


