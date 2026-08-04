"""ORM 基类与方言兼容类型（★方言感知点之二，共两处）。

同一份 models 必须能在 PostgreSQL 与 SQLite 两种方言下 ``create_all()`` 成功，
差异全部封装在下面三个 ``TypeDecorator`` 里：

| 逻辑类型     | PostgreSQL      | SQLite                    |
|--------------|-----------------|---------------------------|
| ``GUID``     | ``UUID``        | ``CHAR(36)``              |
| ``JSONType`` | ``JSONB``       | ``TEXT`` + json 序列化    |
| ``UUIDArray``| ``UUID[]``      | ``TEXT`` + JSON 字符串数组|

另外主键一律用 Python 侧 ``uuid.uuid4()`` 生成，**不用 PG 专属的 ``gen_random_uuid()``**。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import CHAR, DateTime, Text, TypeDecorator, func
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

PG_DIALECT_NAME = "postgresql"


def utcnow() -> datetime:
    """当前 UTC 时间（timezone-aware）。全库统一 UTC 存储。"""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""

    def to_dict(self, exclude: Sequence[str] = ()) -> Dict[str, Any]:
        """把模型实例转成普通 dict（键为 DB 的 snake_case 字段名）。"""
        skip = set(exclude)
        result: Dict[str, Any] = {}
        for column in self.__table__.columns:  # type: ignore[attr-defined]
            if column.name in skip:
                continue
            value = getattr(self, column.name, None)
            if isinstance(value, uuid.UUID):
                value = str(value)
            elif isinstance(value, datetime):
                value = value.isoformat()
            result[column.name] = value
        return result

    def __repr__(self) -> str:  # pragma: no cover - 调试友好
        pk = getattr(self, "id", None)
        return f"<{self.__class__.__name__} id={pk}>"


# ==================================================================== GUID
class GUID(TypeDecorator):
    """跨方言 UUID 类型：PG 落 ``UUID``，SQLite 落 ``CHAR(36)``。

    Python 侧统一以 ``uuid.UUID`` 对象读写，业务代码无需关心底层存储形态。
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == PG_DIALECT_NAME:
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        if dialect.name == PG_DIALECT_NAME:
            return value
        return str(value)

    def process_result_value(self, value: Any, dialect: Any) -> Optional[uuid.UUID]:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


# ================================================================ JSONType
class JSONType(TypeDecorator):
    """跨方言 JSON 类型：PG 落 ``JSONB``（可索引），SQLite 落 ``TEXT`` + json 序列化。"""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == PG_DIALECT_NAME:
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if dialect.name == PG_DIALECT_NAME:
            return value
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list, int, float, bool)):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value


# =============================================================== UUIDArray
class UUIDArray(TypeDecorator):
    """跨方言 UUID 数组：PG 落 ``UUID[]``，SQLite 落 JSON 字符串数组（TEXT）。

    Python 侧统一读写 ``list[uuid.UUID]``。
    业务层做包含判断时**禁止**用 PG 的 ``@>``，一律取出后在 Python 内存里比对
    （V4.1 知识点 < 300 条，无性能问题）。
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == PG_DIALECT_NAME:
            return dialect.type_descriptor(PG_ARRAY(PG_UUID(as_uuid=True)))
        return dialect.type_descriptor(Text())

    @staticmethod
    def _coerce(value: Any) -> List[uuid.UUID]:
        """把任意可迭代输入规整成 ``list[uuid.UUID]``。"""
        if value is None:
            return []
        if isinstance(value, (str, bytes)):
            raise TypeError("UUIDArray expects a sequence of UUIDs, got str/bytes")
        items: List[uuid.UUID] = []
        for item in value:
            items.append(item if isinstance(item, uuid.UUID) else uuid.UUID(str(item)))
        return items

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        items = self._coerce(value)
        if dialect.name == PG_DIALECT_NAME:
            return items
        return json.dumps([str(item) for item in items])

    def process_result_value(self, value: Any, dialect: Any) -> List[uuid.UUID]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return self._coerce(value)
        try:
            raw = json.loads(value)
        except (TypeError, ValueError):
            return []
        return self._coerce(raw)


# ============================================================ 列/Mixin 工具
def uuid_pk() -> Mapped[uuid.UUID]:
    """标准 UUID 主键列（Python 侧生成，跨方言一致）。"""
    return mapped_column(GUID(), primary_key=True, default=uuid.uuid4, nullable=False)


def fk_uuid(target: str, *, nullable: bool = False, ondelete: str = "CASCADE", index: bool = True) -> Mapped[Any]:
    """标准 UUID 外键列。

    Args:
        target: 形如 ``users.id`` 的目标列。
        nullable: 是否可空。
        ondelete: 级联策略（CASCADE / SET NULL / RESTRICT）。
        index: 是否单列索引。
    """
    from sqlalchemy import ForeignKey

    return mapped_column(
        GUID(),
        ForeignKey(target, ondelete=ondelete),
        nullable=nullable,
        index=index,
    )


class TimestampMixin:
    """``created_at`` / ``updated_at``（UTC，两方言通用）。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
        comment="创建时间(UTC)",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        comment="更新时间(UTC)",
    )


class CreatedAtMixin:
    """只需要 ``created_at`` 的事件流表用。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utcnow,
        server_default=func.now(),
        comment="创建时间(UTC)",
    )


class SoftDeleteMixin:
    """软删列；查询默认需过滤 ``deleted_at IS NULL``。"""

    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        comment="软删时间(UTC)，NULL 表示未删除",
    )


__all__ = [
    "Base",
    "GUID",
    "JSONType",
    "UUIDArray",
    "TimestampMixin",
    "CreatedAtMixin",
    "SoftDeleteMixin",
    "uuid_pk",
    "fk_uuid",
    "utcnow",
    "PG_DIALECT_NAME",
]
