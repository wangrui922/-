"""全局配置。

约定：所有可变项一律从环境变量 / `.env` 读取，代码中不出现硬编码的连接串与密钥。
**唯一的运行方式切换点是 `DATABASE_URL`** —— 上线切 PG 只改环境变量，不改代码。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, List

from pydantic import Field, field_validator
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

    # ---------- 公网临时试用（本机穿透）----------
    # 仅 dev 使用：穿透后前端源是随机公网域名（如 *.cpolar.cn），
    # 设 CORS_ALLOW_ALL=1 放开所有源（配合 allow_credentials=False，安全）。
    # 更稳妥：把穿透后的前端域名填进 CORS_EXTRA_ORIGINS（逗号分隔）精确放行。
    cors_allow_all: bool = False
    cors_extra_origins: str = ""

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


    # ---------- 讲解质量闸门（#67 接入生产流）----------
    # DEMO 讲解（学生点「不会做，给我讲」）自动评一次分，低于阈值重讲一次。
    # 默认开启：让每个孩子听到的讲解都先过一遍质量闸（引导性/准确性/清晰度/
    # 针对性/鼓励性），家长更放心。闸门是 best-effort——方舟 Key 缺失或调用
    # 异常时自动降级为直接用原讲解，绝不阻塞主流程（详见 explanation_quality）。
    # 临时关闭用 EXPLANATION_QUALITY_GATE_ENABLED=0。
    explanation_quality_gate_enabled: bool = True
    explanation_quality_gate_threshold: int = Field(default=3, ge=1, le=5)

    # ---------- 判题层（L3 数学等价判定）----------
    # L3 = math_verify 数学等价判定层：把 ``1/2``≡``0.5``、``x+1``≡``1+x``、
    # ``\frac{1}{2}``≡``0.5`` 这类「等价但字面不同」的答案救回判对
    # （详见 docs/judge-l3-math-verify.md）。
    # ``on``（默认）启用；``off`` 时**完全走原来的 L0 SymPy + L1 规则两层路径**。
    judge_l3: str = "on"
    # 单题 L3 判定的超时（毫秒），超时强制降级回 L0/L1，防止第三方库卡住判题主流程
    judge_l3_timeout_ms: int = Field(default=500, ge=50, le=5000)

    @field_validator("judge_l3", mode="before")
    @classmethod
    def _normalize_judge_l3(cls, value: Any) -> str:
        """把开关归一成 ``"on"`` / ``"off"``，兼容 1/0/true/false/yes/no 写法。

        未识别的写法一律按 ``"on"`` 处理（与字段默认值保持一致，避免误关判题增强层）。
        """
        text = str(value).strip().lower()
        if text in {"off", "0", "false", "no", "n", "disabled", "关闭"}:
            return "off"
        if text in {"on", "1", "true", "yes", "y", "enabled", "开启"}:
            return "on"
        # 未识别的写法：保持默认开（与字段默认值一致），避免误关判题增强层
        return "on"
    # ---------- 复习调度内核（双版本裁决：py-fsrs / SM-2）----------
    # 背景：仓库历史上一度存在两套复习调度（fsrs_scheduler 自研 FSRS 语义适配 +
    # review_scheduler SM-2），排程口径不一致。现统一入口收在
    # app/services/fsrs_scheduler.py，内核可在两者之间切换：
    #   * scheduler=sm2      —— 全量走既有 SM-2（默认，灰度基线，零风险）；
    #   * scheduler=fsrs_py  —— 走 py-fsrs（社区验证算法，MIT 许可）；
    #                           包未安装时自动降级 SM-2，绝不阻断应用启动；
    #   * scheduler=fsrs_py + scheduler_fsrs_rollout_ratio=r（0<r<1）
    #                        —— 按 student_id 稳定哈希分批放量：
    #                           hash(student_id) < r 的学生走 py-fsrs，其余走 SM-2，
    #                           同一学生哈希恒定 → 体验始终一致。
    # 回滚：把 SCHEDULER 改回 sm2（或 ratio 调成 0）即可，无需改代码、无需改数据。
    scheduler: str = "sm2"
    scheduler_fsrs_rollout_ratio: float = Field(default=0.0, ge=0.0, le=1.0)

    # ---------- 短信 ----------
    sms_provider: str = "mock"
    # dev_sms_code 已废弃：验证码改为随机生成，不再使用固定万能码
    dev_sms_code: str = ""

    # ---------------------------------------------------------------- 派生属性
    @property
    def cors_origin_list(self) -> List[str]:
        """把逗号分隔的 CORS 白名单拆成列表；``*`` 表示放开。"""
        raw = (self.cors_origins or "").strip()
        if raw == "*":
            return ["*"]
        return [item.strip() for item in raw.split(",") if item.strip()]

    @property
    def cors_extra_origin_list(self) -> List[str]:
        """穿透后追加的精确源（逗号分隔），避免 CORS_ALLOW_ALL 全放开。"""
        return [
            item.strip()
            for item in (self.cors_extra_origins or "").split(",")
            if item.strip()
        ]

    @property
    def is_dev(self) -> bool:
        return self.app_env.lower() in {"dev", "development", "local", "test"}

    @property
    def is_prod(self) -> bool:
        return self.app_env.lower() in {"prod", "production"}

    @property
    def jwt_secret_safe(self) -> bool:
        """生产环境检查 JWT secret 是否安全（非默认值）。"""
        if not self.is_prod:
            return True
        return self.jwt_secret != "dev-secret-change-me" and len(self.jwt_secret) >= 16

    @property
    def judge_l3_enabled(self) -> bool:
        """L3 数学等价判定层是否启用（``JUDGE_L3=off`` 关闭）。"""
        return str(self.judge_l3).strip().lower() == "on"

    @property
    def scheduler_mode(self) -> str:
        """归一化后的复习调度内核：``fsrs_py`` | ``sm2``（未知值一律回落 ``sm2``）。"""
        raw = (self.scheduler or "").strip().lower()
        if raw in {"fsrs_py", "fsrs-py", "fsrs", "pyfsrs", "py-fsrs"}:
            return "fsrs_py"
        return "sm2"

    @property
    def scheduler_fsrs_rollout_ratio_safe(self) -> float:
        """灰度比例钳制到 [0.0, 1.0]（防御 .env 手改越界导致放量失控）。"""
        try:
            ratio = float(self.scheduler_fsrs_rollout_ratio)
        except (TypeError, ValueError):
            return 0.0
        return min(1.0, max(0.0, ratio))

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
