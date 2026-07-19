"""
YAML configuration supports ${VAR} and ${VAR:-default} placeholders.
Keep secrets in the root .env file instead of hard-coding them in YAML.
"""
import os
import re
import yaml
from utils.path_tool import get_abs_path

# Match ${VAR} or ${VAR:-default}.
_ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")


def _load_dotenv(env_path: str = get_abs_path(".env")) -> None:
    """Load a minimal .env file without overriding existing environment variables."""
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def _resolve_env(value):
    """Recursively resolve environment placeholders in configuration values."""
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    if isinstance(value, str):
        def _repl(m: "re.Match") -> str:
            var, default = m.group(1), m.group(2)
            env_value = os.environ.get(var)
            if env_value is not None:
                return env_value
            if default is not None:
                return default
            # Preserve unresolved required placeholders for startup validation.
            return m.group(0)

        return _ENV_PATTERN.sub(_repl, value)
    return value


# Load .env before parsing configuration modules.
_load_dotenv()


def _load_yaml(config_path: str, encoding: str = "utf-8"):
    with open(config_path, "r", encoding=encoding) as f:
        return _resolve_env(yaml.safe_load(f))


def load_rag_config(config_path: str=get_abs_path("config/rag.yml"), encoding: str="utf-8"):
    return _load_yaml(config_path, encoding)


def load_chroma_config(config_path: str=get_abs_path("config/chroma.yml"), encoding: str="utf-8"):
    return _load_yaml(config_path, encoding)


def load_prompts_config(config_path: str=get_abs_path("config/prompts.yml"), encoding: str="utf-8"):
    return _load_yaml(config_path, encoding)


def load_agent_config(config_path: str=get_abs_path("config/agent.yml"), encoding: str="utf-8"):
    return _load_yaml(config_path, encoding)


rag_conf = load_rag_config()
chroma_conf = load_chroma_config()
prompts_conf = load_prompts_config()
agent_conf = load_agent_config()
