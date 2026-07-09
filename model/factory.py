import os
from abc import ABC, abstractmethod
from typing import Optional
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from utils.config_handler import rag_conf

# 大模型调用超时与重试（防止上游卡死拖垮整条请求）
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", 60))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", 2))
# 每批 embedding 的文本条数。默认 10 以兼容对 batch 有硬限制的端点；
# 使用 OpenAI 官方接口可调大（如 512）以提速。
EMBEDDING_BATCH_SIZE = int(os.environ.get("EMBEDDING_BATCH_SIZE", 10))


class BaseModelFactory(ABC):
    @abstractmethod
    def generator(self) -> Optional[Embeddings | BaseChatModel]:
        pass


class ChatModelFactory(BaseModelFactory):
    def generator(self) -> Optional[Embeddings | BaseChatModel]:
        return ChatOpenAI(
            model=rag_conf["chat_model_name"],
            api_key=rag_conf["api_key"],
            base_url=rag_conf["base_url"],
            timeout=LLM_TIMEOUT,
            max_retries=LLM_MAX_RETRIES,
        )


class EmbeddingsFactory(BaseModelFactory):
    def generator(self) -> Optional[Embeddings | BaseChatModel]:
        return OpenAIEmbeddings(
            model=rag_conf["embedding_model_name"],
            api_key=rag_conf["api_key"],
            base_url=rag_conf["base_url"],
            # 部分 OpenAI 兼容端点只接受字符串输入，禁用 tiktoken 分词避免发送 token 数组
            check_embedding_ctx_length=False,
            # 控制单次请求的文本条数，兼容对 batch 有上限的端点
            chunk_size=EMBEDDING_BATCH_SIZE,
        )


chat_model = ChatModelFactory().generator()
embed_model = EmbeddingsFactory().generator()
