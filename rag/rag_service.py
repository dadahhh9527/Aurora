"""
RAG 检索服务：根据用户问题，从向量库召回相关片段，做相关性阈值过滤（可选重排），
返回精简后的参考资料文本。

注意：这里不再单独调用一次 LLM 做总结，参考资料直接交由上层 ReactAgent 统一总结，
避免“总结的总结”带来的双重 LLM 调用、额外延迟与信息损耗。
"""
import os

from langchain_core.documents import Document

from rag.vector_store import VectorStoreService
from utils.config_handler import chroma_conf
from utils.logger_handler import logger


class RagSummarizeService(object):
    def __init__(self):
        self.vector_store = VectorStoreService()
        self.k = int(chroma_conf.get("k", 3))
        self.candidate_k = int(chroma_conf.get("candidate_k", max(self.k * 5, 20)))
        self.score_threshold = float(chroma_conf.get("score_threshold", 0.3))
        self.rerank_enabled = bool(chroma_conf.get("rerank_enabled", False))
        self.rerank_model = chroma_conf.get("rerank_model", "gte-rerank")

    @staticmethod
    def _source_name(metadata: dict) -> str:
        """只保留来源文件名，避免把本地绝对路径等噪音/隐私塞进上下文。"""
        metadata = metadata or {}
        source = metadata.get("source", "")
        name = os.path.basename(source) if source else "知识库"
        page = metadata.get("page")
        return f"{name} 第{page}页" if page is not None else name

    def _rerank(self, query: str, scored: list[tuple[Document, float]]) -> list[tuple[Document, float]]:
        """用 DashScope gte-rerank 对候选做精排；任何异常都回退到相似度排序。"""
        try:
            from dashscope import TextReRank

            docs = [d for d, _ in scored]
            resp = TextReRank.call(
                model=self.rerank_model,
                query=query,
                documents=[d.page_content for d in docs],
                top_n=min(self.k, len(docs)),
                return_documents=False,
                api_key=os.environ.get("DASHSCOPE_API_KEY"),
            )

            if resp.status_code != 200 or not getattr(resp, "output", None):
                logger.warning(f"[rerank]调用失败 code={resp.status_code}，回退相似度排序")
                return sorted(scored, key=lambda x: x[1], reverse=True)

            return [(docs[r.index], float(r.relevance_score)) for r in resp.output.results]
        except Exception as e:
            logger.warning(f"[rerank]异常：{str(e)}，回退相似度排序")
            return sorted(scored, key=lambda x: x[1], reverse=True)

    def retrieve(self, query: str) -> list[tuple[Document, float]]:
        scored = self.vector_store.search_with_scores(query, k=self.candidate_k)

        # 相关性阈值过滤：低于阈值的直接丢弃，避免“答非所问也硬塞资料”
        filtered = [(d, s) for d, s in scored if s is not None and s >= self.score_threshold]

        if not filtered:
            return []

        if self.rerank_enabled and len(filtered) > 1:
            return self._rerank(query, filtered)[:self.k]

        return sorted(filtered, key=lambda x: x[1], reverse=True)[:self.k]

    def rag_summarize(self, query: str) -> str:
        results = self.retrieve(query)

        if not results:
            logger.info(f"[rag]未检索到与问题相关的资料：{query}")
            return "（知识库中未检索到与该问题相关的资料，请结合通用常识谨慎回答或告知用户暂无相关资料）"

        blocks = []
        for i, (doc, _score) in enumerate(results, start=1):
            src = self._source_name(doc.metadata)
            blocks.append(f"【参考资料{i}｜来源：{src}】\n{doc.page_content.strip()}")

        return "\n\n".join(blocks)


if __name__ == '__main__':
    rag = RagSummarizeService()
    print(rag.rag_summarize("小户型适合哪些扫地机器人"))
    print("=" * 30)
    print(rag.rag_summarize("今天股票怎么样"))
