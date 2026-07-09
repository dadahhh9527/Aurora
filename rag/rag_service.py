"""
RAG retrieval service: recall relevant chunks from the vector store for a user question,
apply a relevance-score threshold (with optional reranking), and return trimmed reference text.

Note: this no longer runs a separate LLM summarization pass. The reference material is handed
straight to the outer ReactAgent to summarize, avoiding a "summary of a summary" (double LLM
call, extra latency, information loss).
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
        """Keep only the source file name, so local absolute paths / noise never leak into context."""
        metadata = metadata or {}
        source = metadata.get("source", "")
        name = os.path.basename(source) if source else "knowledge base"
        page = metadata.get("page")
        return f"{name} p.{page}" if page is not None else name

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
                api_key=os.environ.get("RERANK_API_KEY"),
            )

            if resp.status_code != 200 or not getattr(resp, "output", None):
                logger.warning(f"[rerank] call failed code={resp.status_code}, falling back to similarity order")
                return sorted(scored, key=lambda x: x[1], reverse=True)

            return [(docs[r.index], float(r.relevance_score)) for r in resp.output.results]
        except Exception as e:
            logger.warning(f"[rerank] error: {str(e)}, falling back to similarity order")
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
            logger.info(f"[rag] no relevant material found for query: {query}")
            return ("(No relevant material was found in the knowledge base for this question. "
                    "Answer carefully using general knowledge, or tell the user no reference is available.)")

        blocks = []
        for i, (doc, _score) in enumerate(results, start=1):
            src = self._source_name(doc.metadata)
            blocks.append(f"[Reference {i} | source: {src}]\n{doc.page_content.strip()}")

        return "\n\n".join(blocks)


if __name__ == '__main__':
    rag = RagSummarizeService()
    print(rag.rag_summarize("which robot vacuum suits a small apartment"))
    print("=" * 30)
    print(rag.rag_summarize("how are the stock markets today"))
