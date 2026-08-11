"""全局配置。

约定：所有可变项一律从环境变量 / `.env` 读取，代码中不出现硬编码的连接串与密钥。
**唯一的运行方式切换点是 `DATABASE_URL`** —— 上线切 PG 只改环境变量，不改代码。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> parents: [core, app, backend, <项目根>]
_THIS_FILE = Path(__file__).resolve()
BACKEND_DIR: Path = _THIS_FILE.parents[2]
ROOT_DIR: Path = _THIS_FILE.parents[3]


class Settings(BaseSettings):
    """应用配置。

    字段名用小写，`case_sensitive=False` 使其自动匹配大写环境变量
    （例如字段 ``database_url`` 对应环境变量 ``DATABASE_URL``）。
    """

    model_config = SettingsConfigDict(
        env_file=(str(ROOT_DIR / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- 应用 ----------
    app_name: str = "ai-math-coach"
    app_version: str = "4.1.0"
    app_env: str = "dev"
    debug: bool = True
    api_prefix: str = "/api"
    cors_origins: str = "http://localhost:5173,http://localhost:5174,http://localhost:5175"

    # ---------- 数据库 ----------
    # 生产：postgresql+psycopg://coach:coach@localhost:5433/coach
    # 开发：sqlite:///./dev.db
    database_url: str = "sqlite:///./dev.db"
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # ---------- 鉴权 ----------
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120          # 2h
    refresh_token_expire_days: int = 30             # 30d
    jwt_issuer: str = "ai-math-coach"

    # ---------- 配额（无 Redis，走 DB 计数）----------
    free_daily_ai: int = 20

    # ---------- 模型网关（服务端持 Key）----------
    ark_api_key: str = ""
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    ark_model: str = "doubao-seed-1-6-250615"
    ark_timeout_sec: int = 60

    # ---------- 存储 ----------
    storage_driver: str = "local"
    storage_dir: str = "./storage"

    # ---------- 家长周报分享页（Epic6）----------
    # 家长免登录访问的公开页部署地址；生成接口会把 token 拼成
    # ``{public_base_url}/r/{token}`` 返回给前端。生产环境务必改成真实域名。
    public_base_url: str = "http://localhost:5173"

    # ---------- 短信 ----------
    sms_provider: str = "mock"
    dev_sms_code: str = "000000"

    # ---------------------------------------------------------------- 派生属性
    @property
    def cors_origin_list(self) -> List[str]:
        """把逗号分隔的 CORS 白名单拆成列表；``*`` 表示放开。"""
        raw = (self.cors_origins or "").strip()
        if raw == "*":
            return ["*"]
        return [item.strip() for item in raw.split(",") if item.strip()]

    @property
    def is_dev(self) -> bool:
        return self.app_env.lower() in {"dev", "development", "local", "test"}

    @property
    def is_prod(self) -> bool:
        return self.app_env.lower() in {"prod", "production"}

    @property
    def storage_path(self) -> Path:
        """存储目录绝对路径（不存在则创建）。"""
        path = Path(self.storage_dir)
        if not path.is_absolute():
            path = BACKEND_DIR / path
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局单例配置（带缓存，进程内只解析一次 .env）。"""
    return Settings()


settings: Settings = get_settings()

__all__ = ["Settings", "get_settings", "settings", "BACKEND_DIR", "ROOT_DIR"]
