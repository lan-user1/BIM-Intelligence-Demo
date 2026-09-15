"""集中管理后端运行时配置、目录和环境变量。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


# 所有相对路径都以项目根目录为基准，避免从不同工作目录启动服务时偏移。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"

# 后端专属 .env 优先，项目根目录 .env 作为补充。
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(PROJECT_ROOT / ".env")


# 读取路径类环境变量；相对路径统一解析到 backend 目录下。
def _path_from_env(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    if not raw:
        return default.resolve()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path.resolve()


# 按 JSON 配置、环境变量、默认值的顺序读取字符串配置。
def _first_value(
    file_config: dict[str, object],
    file_keys: tuple[str, ...],
    env_keys: tuple[str, ...],
    default: str = "",
) -> str:
    for key in file_keys:
        value = file_config.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    for key in env_keys:
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    return default


# 读取并兼容旧版配置格式；deepseek 子对象会覆盖顶层同名字段。
def _load_json_config() -> tuple[dict[str, object], Path]:
    path = _path_from_env(
        "DEEPSEEK_CONFIG_FILE",
        BACKEND_DIR / "config.json",
    )
    if not path.exists():
        return {}, path
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to read AI config file: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"AI config file must contain a JSON object: {path}")
    deepseek = payload.get("deepseek")
    if isinstance(deepseek, dict):
        return {**payload, **deepseek}, path
    return payload, path


@dataclass(frozen=True)
class Settings:
    """应用启动时解析完成且运行期间保持不变的配置快照。"""

    app_name: str
    api_key: str
    api_base: str
    model_name: str
    temperature: float
    max_upload_mb: int
    frontend_origins: tuple[str, ...]
    source_dir: Path
    upload_dir: Path
    cache_dir: Path
    knowledge_dir: Path
    max_context_chars: int
    config_file: Path

    @property
    def ai_configured(self) -> bool:
        """只有配置了 API Key 才认为在线问答服务可用。"""
        return bool(self.api_key.strip())


# 读取全部配置并确保模型、缓存和知识库目录存在。
def get_settings() -> Settings:
    file_config, config_file = _load_json_config()
    origins = os.getenv("FRONTEND_ORIGINS", "http://localhost:5173")
    settings = Settings(
        app_name="BIM Intelligence API",
        api_key=_first_value(
            file_config,
            ("api_key", "apiKey", "key"),
            ("DEEPSEEK_API_KEY", "OPENAI_API_KEY"),
        ),
        api_base=_first_value(
            file_config,
            ("api_base", "apiBase", "base_url"),
            ("DEEPSEEK_API_BASE", "OPENAI_API_BASE"),
            "https://api.deepseek.com/v1",
        ),
        model_name=_first_value(
            file_config,
            ("model", "model_name"),
            ("DEEPSEEK_MODEL", "MODEL_NAME"),
            "deepseek-chat",
        ),
        temperature=float(
            _first_value(
                file_config,
                ("temperature",),
                ("AI_TEMPERATURE",),
                "0.2",
            )
        ),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "100")),
        frontend_origins=tuple(
            origin.strip() for origin in origins.split(",") if origin.strip()
        ),
        source_dir=_path_from_env("SOURCE_DIR", PROJECT_ROOT / "MySource"),
        upload_dir=_path_from_env(
            "MODEL_UPLOAD_DIR", BACKEND_DIR / "storage" / "models"
        ),
        cache_dir=_path_from_env(
            "MODEL_CACHE_DIR", BACKEND_DIR / "storage" / "cache"
        ),
        knowledge_dir=_path_from_env(
            "KNOWLEDGE_DIR", BACKEND_DIR / "documents"
        ),
        max_context_chars=int(os.getenv("MAX_CONTEXT_CHARS", "18000")),
        config_file=config_file,
    )
    for directory in (
        settings.source_dir,
        settings.upload_dir,
        settings.cache_dir,
        settings.knowledge_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return settings


# 模块级单例，供 FastAPI 路由和各服务模块共享。
settings = get_settings()
