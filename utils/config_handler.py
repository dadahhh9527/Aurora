"""
yaml
k: v

配置项支持 ${VAR} / ${VAR:-默认值} 形式的环境变量占位符，
敏感信息（如 api_key）请放到项目根目录的 .env 中，不要写死在 yml 里。
"""
import os
import re
import yaml
from utils.path_tool import get_abs_path

# 匹配 ${VAR} 或 ${VAR:-default}
_ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")


def _load_dotenv(env_path: str = get_abs_path(".env")) -> None:
    """极简 .env 加载器（无第三方依赖），已存在的环境变量不会被覆盖。"""
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
    """递归地把配置里的 ${VAR} 占位符替换为环境变量的值。"""
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
            # 未配置且无默认值：保留原样并告警，便于排查
            return m.group(0)

        return _ENV_PATTERN.sub(_repl, value)
    return value


# 模块导入时先加载 .env
_load_dotenv()


def _load_yaml(config_path: str, encoding: str = "utf-8"):
    with open(config_path, "r", encoding=encoding) as f:
        return _resolve_env(yaml.load(f, Loader=yaml.FullLoader))


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


if __name__ == '__main__':
    print(rag_conf["chat_model_name"])
