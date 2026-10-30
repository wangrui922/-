"""系统配置读取（带默认值 + 缓存）。

所有阈值、开关等运营配置统一从这里读取；
DB 无记录时自动回退到代码默认值。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from sqlalchemy import select

logger = logging.getLogger("app.core.system_config")


def _to_bool(raw: Any) -> bool:
    """把配置字符串转成 bool（``1/true/yes/on`` 为真，忽略大小写与空白）。

    为什么不能直接用内置 ``bool``：``bool("false")`` / ``bool("0")`` 都是
    ``True``——字符串配置一旦交给内置 bool，「关闭」会被误读成「开启」。
    开关类配置（如 ``CONSENT_GATE_ENABLED``）必须走本函数。
    """
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


#: 默认值表（key -> (default_value, cast_func)）
DEFAULTS: Dict[str, tuple] = {
    "REPEAT_SIMILARITY": ("0.7", float),
    "STUCK_TO_L2": ("1", int),
    "STUCK_TO_L3": ("2", int),
    "STUCK_TO_L4": ("3", int),
    "REPEAT_TO_L2": ("1", int),
    "REPEAT_TO_L3": ("2", int),
    "NOPROGRESS_TO_L3": ("2", int),
    "NOPROGRESS_TO_L4": ("3", int),
    "TURNS_TO_L4": ("5", int),
    "HAS_SUBSTANCE_MIN_LEN": ("12", int),
    # V6.5 合规闸门（监护人同意门控）：
    #   * CONSENT_GATE_ENABLED —— 门控总开关，默认关（灰度放量，见 deps.require_guardian_consent）；
    #   * CONSENT_VERSION      —— 当前规则版本号，变更后旧版本同意记录一律失效需重新同意。
    # 开关的 cast 用 _to_bool 而非 bool，原因见 _to_bool 文档字符串。
    "CONSENT_GATE_ENABLED": ("false", _to_bool),
    "CONSENT_VERSION": ("v1", str),
    # V6.5 低置信度重拍（灰度开关，默认关闭）：
    #   * CONFIDENCE_RETHRESHOLD —— 拍题分析自评置信度低于该阈值时，引导重新拍照
    #     而不硬答（orchestrator._confidence_gate）；取值 0~1，-1 表示关闭。
    # 默认关闭的理由：模型自评置信度的可靠性尚未在内测中验证（模型可能对没把握的
    # 题也给高置信度），按「不确定性默认不挡用户」灰度——运营/内测验证后按需开启
    # （建议 0.6）。与 CONSENT_GATE_ENABLED 同一灰度思路。
    "CONFIDENCE_RETHRESHOLD": ("-1", float),
    # A-1 轻量 RAG「接地气讲解」（灰度开关，默认关）：
    #   * RAG_GROUNDING_ENABLED —— 打开后，tutor 在 **DEMO / JUDGE** 模式下
    #     追加一段接地气素材（生活类比 + 知识点释义）作为 system 提示的
    #     第 5 段后缀；GUIDE / HINT 一律零注入。
    # 默认关的理由（灰度口径）：开关打开前，线上输出与改造前**逐字一致**
    # （空素材 = 不追加任何后缀），这是验收硬要求；先小流量验证「讲解更像
    # 接地气的好老师」再放量。
    # cast 用 _to_bool 而非 bool，原因见 _to_bool 文档字符串。
    "RAG_GROUNDING_ENABLED": ("false", _to_bool),
}

# 进程内缓存
_cache: Dict[str, Any] = {}


def get_config(key: str) -> Any:
    """读取单个配置值（带缓存），首次或缓存 miss 时查 DB。"""
    if key in _cache:
        return _cache[key]

    default_str, cast = DEFAULTS.get(key, ("", str))
    default = cast(default_str) if default_str else cast()

    try:
        from app.db import engine
        from app.models.system_config import SystemConfig

        with engine.connect() as conn:
            row = conn.execute(
                select(SystemConfig.value).where(SystemConfig.key == key)
            ).scalar_one_or_none()
            if row is not None:
                val = cast(row)
            else:
                val = default
    except Exception as exc:
        logger.warning("读取配置 %s 失败: %s，回退默认值", key, exc)
        val = default

    _cache[key] = val
    return val


def reload_config():
    """清空缓存，下次调用重新从 DB 读取。"""
    _cache.clear()


def all_configs() -> Dict[str, Any]:
    """返回全部配置（含默认值）。"""
    result = {}
    for key, (default_str, cast) in DEFAULTS.items():
        result[key] = get_config(key)
    return result


def reset_configs(db):
    """重置所有配置为默认值并写入 DB。"""
    from app.models.system_config import SystemConfig

    for key, (default_str, cast) in DEFAULTS.items():
        row = db.execute(select(SystemConfig).where(SystemConfig.key == key)).scalar_one_or_none()
        if row is None:
            db.add(SystemConfig(key=key, value=default_str, description=""))
        else:
            row.value = default_str
    _cache.clear()
