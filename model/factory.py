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
            # 阿里云兼容端点只接受字符串输入，禁用tiktoken分词避免发送token数组
            check_embedding_ctx_length=False,
            # 阿里云兼容端点单次 embedding 请求最多 10 条文本，超过会报 400，控制每批发送数量
            chunk_size=10,
        )


chat_model = ChatModelFactory().generator()
embed_model = EmbeddingsFactory().generator()
