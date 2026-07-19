from typing import Callable
from utils.prompt_loader import load_system_prompts, load_report_prompts
from langchain.agents import AgentState
from langchain.agents.middleware import wrap_tool_call, before_model, dynamic_prompt, ModelRequest
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command
from utils.logger_handler import logger


@wrap_tool_call
def monitor_tool(
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
) -> ToolMessage | Command:
    # Monitor tool execution
    logger.info(f"[tool monitor] running tool: {request.tool_call['name']}")
    logger.debug(
        "[tool monitor] argument names: %s",
        sorted((request.tool_call.get("args") or {}).keys()),
    )

    try:
        result = handler(request)
        logger.info(f"[tool monitor] tool {request.tool_call['name']} succeeded")

        if request.tool_call['name'] == "fill_context_for_report":
            request.runtime.context["report"] = True

        return result
    except Exception as e:
        logger.error(f"tool {request.tool_call['name']} failed, reason: {str(e)}")
        raise e


@before_model
def log_before_model(
        state: AgentState,
        runtime: Runtime,
):
    # Log before each model call
    logger.info(f"[log_before_model] calling model with {len(state['messages'])} message(s)")

    last_message = state["messages"][-1]
    # content may be a str or a list (multimodal / tool calls); coerce to str for logging
    content = last_message.content
    content_size = len(content) if isinstance(content, (str, list)) else 0
    logger.debug(
        "[log_before_model] %s content_size=%s",
        type(last_message).__name__,
        content_size,
    )

    return None


@dynamic_prompt
def report_prompt_switch(request: ModelRequest):
    # Dynamically switch the system prompt for report-generation turns
    is_report = request.runtime.context.get("report", False)
    prompt = load_report_prompts() if is_report else load_system_prompts()
    long_term_memory = request.runtime.context.get("long_term_memory", "")
    if long_term_memory:
        prompt = f"{prompt}\n\n{long_term_memory}"

    return prompt
