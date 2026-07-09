import json
import sqlite3
import uuid

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware, ModelCallLimitMiddleware
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from model.factory import chat_model
from utils.prompt_loader import load_system_prompts
from utils.path_tool import get_abs_path
from utils import settings
from agent.tools.agent_tools import (rag_summarize, get_weather, get_user_location, get_user_id,
                                     get_current_month, fetch_external_data, fill_context_for_report)
from agent.tools.middleware import monitor_tool, log_before_model, report_prompt_switch


# 工具名 -> 面向用户的友好状态文案
TOOL_LABELS = {
    "rag_summarize": "正在查阅知识库",
    "get_weather": "正在查询天气",
    "get_user_location": "正在定位所在城市",
    "get_user_id": "正在核对账户信息",
    "get_current_month": "正在获取当前月份",
    "fetch_external_data": "正在读取使用记录",
    "fill_context_for_report": "正在准备使用报告",
}


class _MetadataSerde:
    """
    兼容适配器：langgraph-checkpoint-sqlite 用 jsonplus_serde.dumps/loads 存 checkpoint 元数据，
    而新版核心的 JsonPlusSerializer 只有 dumps_typed/loads_typed。元数据是纯 JSON 结构，
    这里用标准 json 提供 dumps/loads，行为与旧版一致。
    """

    def dumps(self, obj) -> bytes:
        return json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")

    def loads(self, data):
        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8")
        return json.loads(data)


class ReactAgent:
    def __init__(self):
        # 持久化记忆：存 SQLite，进程重启也不丢；配合前端“刷新换新 thread_id”实现会话隔离。
        # check_same_thread=False：FastAPI 在线程池中处理请求，需允许跨线程复用连接。
        conn = sqlite3.connect(get_abs_path(settings.MEMORY_DB_PATH), check_same_thread=False)
        self.checkpointer = SqliteSaver(conn)
        # 修正新旧版本序列化器接口不一致导致的元数据写入失败
        self.checkpointer.jsonplus_serde = _MetadataSerde()
        self.checkpointer.setup()

        self.agent = create_agent(
            model=chat_model,
            system_prompt=load_system_prompts(),
            tools=[rag_summarize, get_weather, get_user_location, get_user_id,
                   get_current_month, fetch_external_data, fill_context_for_report],
            middleware=[
                # 超出阈值时自动摘要旧消息，控制 token 成本与上下文长度
                SummarizationMiddleware(
                    model=chat_model,
                    trigger=("messages", settings.SUMMARY_TRIGGER_MESSAGES),
                    keep=("messages", settings.SUMMARY_KEEP_MESSAGES),
                ),
                # 单次对话内限制模型调用轮数，防止工具死循环烧钱
                ModelCallLimitMiddleware(run_limit=settings.MODEL_RUN_LIMIT, exit_behavior="end"),
                monitor_tool,
                log_before_model,
                report_prompt_switch,
            ],
            checkpointer=self.checkpointer,
        )

    @staticmethod
    def _content_to_text(content) -> str:
        """content 可能是 str，也可能是 list（多模态/分段），统一转成纯文本。"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(item.get("text", ""))
                else:
                    parts.append(str(item))
            return "".join(parts)
        return str(content) if content is not None else ""

    @staticmethod
    def _config(thread_id: str | None):
        # 没有传入 thread_id 时，退化为一次性会话（随机 id，不复用历史）
        return {"configurable": {"thread_id": thread_id or f"anon-{uuid.uuid4().hex}"}}

    def execute_events(self, query: str, thread_id: str | None = None):
        """
        （消息级）结构化事件流：thinking / tool / assistant。
        保留作为不支持流式时的降级方案。
        """
        input_dict = {"messages": [{"role": "user", "content": query}]}
        config = self._config(thread_id)
        baseline = None

        for chunk in self.agent.stream(input_dict, config=config, stream_mode="values", context={"report": False}):
            messages = chunk["messages"]

            if baseline is None:
                baseline = len(messages)
                continue

            for msg in messages[baseline:]:
                if isinstance(msg, HumanMessage):
                    continue
                if isinstance(msg, AIMessage):
                    text = self._content_to_text(msg.content).strip()
                    tool_calls = getattr(msg, "tool_calls", None) or []
                    if tool_calls:
                        if text:
                            yield {"type": "thinking", "content": text}
                        for call in tool_calls:
                            name = call.get("name", "")
                            yield {"type": "tool", "name": name, "label": TOOL_LABELS.get(name, "正在处理")}
                    elif text:
                        yield {"type": "assistant", "content": text}

            baseline = len(messages)

    def execute_token_events(self, query: str, thread_id: str | None = None):
        """
        （token 级）真流式事件流，供前端逐字渲染：
          - {"type": "token", "mid": 消息id, "content": 增量文本}
          - {"type": "tool",  "mid": 消息id, "name": ..., "label": ...}
        前端根据 mid 分组：若某条消息随后出现 tool 事件，则它是“思考”，否则是最终回答。
        """
        input_dict = {"messages": [{"role": "user", "content": query}]}
        config = self._config(thread_id)
        emitted_tools: set = set()

        for chunk, meta in self.agent.stream(
            input_dict, config=config, stream_mode="messages", context={"report": False}
        ):
            # 只取主 Agent 模型节点的输出，过滤掉摘要中间件等其它节点产生的 token
            node = (meta or {}).get("langgraph_node")
            if node not in (None, "model", "agent"):
                continue

            if not isinstance(chunk, AIMessageChunk):
                continue

            mid = getattr(chunk, "id", None) or "cur"

            delta = self._content_to_text(chunk.content)
            if delta:
                yield {"type": "token", "mid": mid, "content": delta}

            for tcc in (getattr(chunk, "tool_call_chunks", None) or []):
                name = tcc.get("name")
                index = tcc.get("index")
                key = (mid, index)
                if name and key not in emitted_tools:
                    emitted_tools.add(key)
                    yield {"type": "tool", "mid": mid, "name": name,
                           "label": TOOL_LABELS.get(name, "正在处理")}


if __name__ == '__main__':
    agent = ReactAgent()
    tid = "demo-session"
    for event in agent.execute_token_events("我家是小户型，适合哪些扫地机器人", thread_id=tid):
        print(event)
