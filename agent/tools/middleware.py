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
    logger.info(f"[tool monitor] args: {request.tool_call['args']}")

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
    content_text = content.strip() if isinstance(content, str) else str(content)
    logger.debug(f"[log_before_model]{type(last_message).__name__} | {content_text}")

    return None


@dynamic_prompt
def report_prompt_switch(request: ModelRequest):
    # Dynamically switch the system prompt for report-generation turns
    is_report = request.runtime.context.get("report", False)
    if is_report:
        return load_report_prompts()

    return load_system_prompts()
