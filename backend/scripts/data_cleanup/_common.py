"""练习模块数据补全脚本的共用工具（T1）。

提供：

* :func:`get_engine` —— 从 ``app.core.config.settings`` 取 SQLAlchemy engine。
* :func:`append_log` —— 追加写入 ``docs/data-cleanup-log-20260811.md``。
* :func:`utc_now` —— 统一时间戳格式。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

#: ``docs/data-cleanup-log-20260811.md`` 的根相对路径固定在项目根。
#: ``backend/app`` 不在父链上，所以这里用 ``parents[3]`` 拿到项目根。
_PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
LOG_PATH: Path = _PROJECT_ROOT / "docs" / "data-cleanup-log-20260811.md"


def get_engine() -> Engine:
    """按后端 ``Settings.database_url`` 构造 SQLAlchemy engine（不缓存）。"""
    from app.core.config import get_settings  # 延迟导入，避免脚本无 backend 时也拉满

    settings = get_settings()
    return create_engine(settings.database_url, future=True)


def utc_now() -> datetime:
    """UTC 当前时间（与 ``app.models.base.utcnow`` 对齐）。"""
    return datetime.now(timezone.utc)


def utc_isoformat(value: Optional[datetime] = None) -> str:
    """``YYYY-MM-DD HH:MM`` 形式的时间串，写日志时用人话格式。"""
    moment = value or utc_now()
    return moment.strftime("%Y-%m-%d %H:%M")


def append_log(markdown_block: str) -> None:
    """把一段 markdown 追加写到统一日志里。

    若父目录不存在先创建；写入失败只打 ``WARNING`` 而不抛异常——脚本落库已
    完成，**绝不能因为写日志失败回滚事务**。
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            if not markdown_block.endswith("\n"):
                markdown_block += "\n"
            fh.write(markdown_block)
            fh.write("\n")
    except Exception as exc:  # noqa: BLE001 - 日志失败属于可观测降级，不能阻断
        import warnings

        warnings.warn(f"追加 data-cleanup 日志失败：{exc}", stacklevel=2)


def build_log_block(title: str, lines: list[tuple[str, Any]]) -> str:
    """构造一段统一的 markdown 日志段。

    Args:
        title: ``##`` 二级标题（带 emoji 或纯文本都行）。
        lines: ``(key, value)`` 列表；key 会自动加 ``- `` 前缀；value 转 str。
    """
    head = f"## {title}\n"
    body = "\n".join(f"- {key}：{value}" for key, value in lines)
    return f"{head}\n{body}\n"


__all__ = [
    "get_engine",
    "utc_now",
    "utc_isoformat",
    "append_log",
    "build_log_block",
    "LOG_PATH",
]
