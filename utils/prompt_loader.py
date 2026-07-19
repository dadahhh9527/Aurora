from utils.config_handler import prompts_conf
from utils.path_tool import get_abs_path
from utils.logger_handler import logger


def _load_prompt(config_key: str) -> str:
    try:
        prompt_path = get_abs_path(prompts_conf[config_key])
    except KeyError:
        logger.error("[prompt] missing configuration key: %s", config_key)
        raise

    try:
        with open(prompt_path, "r", encoding="utf-8") as prompt_file:
            return prompt_file.read()
    except OSError:
        logger.error("[prompt] failed to read: %s", prompt_path, exc_info=True)
        raise


def load_system_prompts() -> str:
    return _load_prompt("main_prompt_path")


def load_report_prompts() -> str:
    return _load_prompt("report_prompt_path")

