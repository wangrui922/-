"""系统配置读取（带默认值 + 缓存）。

所有阈值、开关等运营配置统一从这里读取；
DB 无记录时自动回退到代码默认值。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from sqlalchemy import select

logger = logging.getLogger("app.core.system_config")

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
