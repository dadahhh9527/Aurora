import os
from abc import ABC, abstractmethod
from typing import Optional
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from utils.config_handler import rag_conf

# Model-call timeout and retries prevent upstream failures from stalling requests.
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", 60))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", 2))
# Keep embedding batches small for provider compatibility; official OpenAI
# deployments can raise this value for higher throughput.
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
            # Some compatible endpoints accept strings but reject token arrays.
            check_embedding_ctx_length=False,
            # Respect provider-specific embedding batch limits.
            chunk_size=EMBEDDING_BATCH_SIZE,
        )


chat_model = ChatModelFactory().generator()
embed_model = EmbeddingsFactory().generator()
