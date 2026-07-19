import json
import sqlite3
import uuid

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware, ModelCallLimitMiddleware
from langchain_core.messages import AIMessageChunk
from langgraph.checkpoint.sqlite import SqliteSaver

from model.factory import chat_model
from utils.prompt_loader import load_system_prompts
from utils.path_tool import get_abs_path
from utils import settings
from agent.tools.agent_tools import (rag_summarize, get_weather, get_user_location, get_user_id,
                                     get_current_month, fetch_external_data, fill_context_for_report)
from agent.tools.middleware import monitor_tool, log_before_model, report_prompt_switch
from services.runtime_context import reset_current_user_id, set_current_user_id
from utils.content import content_to_text


# Tool name -> user-facing status label
TOOL_LABELS = {
    "rag_summarize": "Searching the knowledge base",
    "get_weather": "Checking the weather",
    "get_user_location": "Detecting your city",
    "get_user_id": "Verifying account details",
    "get_current_month": "Getting the current month",
    "fetch_external_data": "Reading usage records",
    "fill_context_for_report": "Preparing your usage report",
}


class _MetadataSerde:
    """
    Compatibility adapter for checkpoint metadata.

    langgraph-checkpoint-sqlite expects jsonplus_serde.dumps/loads, while newer
    core serializers expose only typed methods. Metadata is plain JSON, so the
    standard library provides the legacy interface safely.
    """

    def dumps(self, obj) -> bytes:
        return json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")

    def loads(self, data):
        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8")
        return json.loads(data)


class ReactAgent:
    def __init__(self):
        # SQLite checkpoints survive restarts. FastAPI sync routes use a thread pool,
        # so the connection allows cross-thread use; SqliteSaver serializes every
        # cursor operation with its internal reentrant lock.
        self._checkpoint_connection = sqlite3.connect(
            get_abs_path(settings.MEMORY_DB_PATH), check_same_thread=False
        )
        self.checkpointer = SqliteSaver(self._checkpoint_connection)
        # Bridge serializer interface differences across dependency versions.
        self.checkpointer.jsonplus_serde = _MetadataSerde()
        self.checkpointer.setup()

        self.agent = create_agent(
            model=chat_model,
            system_prompt=load_system_prompts(),
            tools=[rag_summarize, get_weather, get_user_location, get_user_id,
                   get_current_month, fetch_external_data, fill_context_for_report],
            middleware=[
                # Summarize old messages to bound context size and token cost.
                SummarizationMiddleware(
                    model=chat_model,
                    trigger=("messages", settings.SUMMARY_TRIGGER_MESSAGES),
                    keep=("messages", settings.SUMMARY_KEEP_MESSAGES),
                ),
                # Bound model calls per turn to prevent runaway tool loops.
                ModelCallLimitMiddleware(run_limit=settings.MODEL_RUN_LIMIT, exit_behavior="end"),
                monitor_tool,
                log_before_model,
                report_prompt_switch,
            ],
            checkpointer=self.checkpointer,
        )

    @staticmethod
    def _config(thread_id: str | None):
        # Missing thread IDs create one-off conversations that do not reuse history.
        return {"configurable": {"thread_id": thread_id or f"anon-{uuid.uuid4().hex}"}}

    @staticmethod
    def _runtime_context(user_id: str, long_term_memory: str) -> dict:
        return {
            "report": False,
            "user_id": user_id,
            "long_term_memory": long_term_memory,
        }

    def execute_token_events(
        self,
        query: str,
        thread_id: str | None = None,
        *,
        user_id: str,
        long_term_memory: str = "",
    ):
        """
        Stream token and tool events for the web client.

        The client groups events by message ID. Messages followed by a tool event
        are internal model work; messages without a tool event are final output.
        """
        input_dict = {"messages": [{"role": "user", "content": query}]}
        config = self._config(thread_id)
        emitted_tools: set = set()

        token = set_current_user_id(user_id)
        try:
            stream = self.agent.stream(
                input_dict,
                config=config,
                stream_mode="messages",
                context=self._runtime_context(user_id, long_term_memory),
            )
            for chunk, meta in stream:
                # Keep only the main model node and ignore summarization middleware tokens.
                node = (meta or {}).get("langgraph_node")
                if node not in (None, "model", "agent"):
                    continue

                if not isinstance(chunk, AIMessageChunk):
                    continue

                mid = getattr(chunk, "id", None) or "cur"

                delta = content_to_text(chunk.content)
                if delta:
                    yield {"type": "token", "mid": mid, "content": delta}

                for tcc in (getattr(chunk, "tool_call_chunks", None) or []):
                    name = tcc.get("name")
                    index = tcc.get("index")
                    key = (mid, index)
                    if name and key not in emitted_tools:
                        emitted_tools.add(key)
                        yield {
                            "type": "tool",
                            "mid": mid,
                            "name": name,
                            "label": TOOL_LABELS.get(name, "Working"),
                        }
        finally:
            reset_current_user_id(token)

    def close(self) -> None:
        self._checkpoint_connection.close()
