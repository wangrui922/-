"""数据库引擎与会话（★方言感知点之一，共两处）。

设计要点：
    * 唯一切换点是环境变量 ``DATABASE_URL``：
      - 生产 ``postgresql+psycopg://coach:coach@localhost:5433/coach``
      - 开发 ``sqlite:///./dev.db``
    * 除本文件与 ``app/models/base.py`` 外，**任何业务代码不得感知方言**，
      更不得出现 PG 专属语法（``->>``、``@>``、``ARRAY[]``、``gen_random_uuid()``、``WITH RECURSIVE``）。
    * SQLite 侧统一开启外键约束，使 FK 行为与 PG 对齐。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Generator, Iterator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import BACKEND_DIR, settings

logger = logging.getLogger("app.db")

DIALECT_POSTGRES = "postgresql"
DIALECT_SQLITE = "sqlite"


def normalize_database_url(raw_url: str) -> str:
    """规范化连接串。

    * ``postgres://`` / ``postgresql://`` 一律补上 ``+psycopg`` 驱动（V4.1 用 psycopg3）；
    * SQLite 相对路径统一锚定到 ``backend/`` 目录，避免不同 CWD 下建出多个 dev.db。
    """
    url_str = (raw_url or "").strip() or "sqlite:///./dev.db"

    if url_str.startswith("postgres://"):
        url_str = "postgresql+psycopg://" + url_str[len("postgres://"):]
    elif url_str.startswith("postgresql://"):
        url_str = "postgresql+psycopg://" + url_str[len("postgresql://"):]

    url: URL = make_url(url_str)

    if url.get_backend_name() == DIALECT_SQLITE and url.database and url.database != ":memory:":
        db_path = Path(url.database)
        if not db_path.is_absolute():
            db_path = (BACKEND_DIR / db_path).resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        url = url.set(database=str(db_path))

    return url.render_as_string(hide_password=False)


DATABASE_URL: str = normalize_database_url(settings.database_url)
_URL: URL = make_url(DATABASE_URL)

#: 当前方言名：``postgresql`` 或 ``sqlite``
DIALECT: str = _URL.get_backend_name()


def is_postgres() -> bool:
    """当前是否为 PostgreSQL。"""
    return DIALECT == DIALECT_POSTGRES


def is_sqlite() -> bool:
    """当前是否为 SQLite。"""
    return DIALECT == DIALECT_SQLITE


def db_kind() -> str:
    """对外暴露的数据库类型名（健康检查用）：``postgres`` / ``sqlite``。"""
    return "postgres" if is_postgres() else DIALECT


def _build_engine() -> Engine:
    """按方言构造 Engine（连接池参数差异只在这里）。"""
    kwargs: Dict[str, Any] = {
        "echo": settings.db_echo,
        "future": True,
        "pool_pre_ping": True,
    }

    if is_sqlite():
        kwargs["connect_args"] = {"check_same_thread": False}
        if _URL.database in (None, "", ":memory:"):
            # 内存库必须共享同一连接，否则 create_all 与请求各看各的库
            kwargs["poolclass"] = StaticPool
    else:
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow
        kwargs["pool_recycle"] = 1800

    engine = create_engine(DATABASE_URL, **kwargs)
    logger.info("DB engine created: dialect=%s url=%s", DIALECT, _URL.render_as_string(hide_password=True))
    return engine


engine: Engine = _build_engine()

SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
    """SQLite 打开外键约束 + WAL，使其行为尽量贴近 PG。"""
    module_name = type(dbapi_connection).__module__ or ""
    if "sqlite3" not in module_name:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：请求级数据库会话（异常回滚，结束必关）。"""
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def session_scope() -> Iterator[Session]:
    """脚本/定时任务用的上下文会话。

    Example:
        >>> with contextlib.closing(next(iter([]))):  # doctest: +SKIP
        ...     pass
    """
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ping() -> bool:
    """连通性探测：能跑通 ``SELECT 1`` 即视为健康。"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # pragma: no cover - 依赖外部服务
        logger.warning("DB ping failed: %s", exc)
        return False


def server_version() -> str:
    """返回数据库版本字符串（失败返回 ``unknown``）。"""
    sql = "SHOW server_version" if is_postgres() else "SELECT sqlite_version()"
    try:
        with engine.connect() as conn:
            row = conn.execute(text(sql)).scalar()
        return str(row) if row is not None else "unknown"
    except Exception:  # pragma: no cover - 依赖外部服务
        return "unknown"


__all__ = [
    "DATABASE_URL",
    "DIALECT",
    "DIALECT_POSTGRES",
    "DIALECT_SQLITE",
    "engine",
    "SessionLocal",
    "get_db",
    "session_scope",
    "is_postgres",
    "is_sqlite",
    "db_kind",
    "ping",
    "server_version",
    "normalize_database_url",
]
