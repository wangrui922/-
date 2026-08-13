"""题库列的自愈式建表守卫（T05 · T1 数据层 + V7 保险②）。

⚠️ V7 线上事故坑 · **两道保险**
    保险① 在迁移 SQL 里（``backend/migrations/004_bank_columns*.sql``）：
          ``ADD COLUMN kp_verified`` 与 ``UPDATE … WHERE source_doc IS NULL``
          写在同一个事务内。
    保险② 就是本模块：**导入器每次启动都二次探测**——
          * 缺列 → 自动 ``ALTER TABLE ADD COLUMN`` 并回填；
          * 若发现「``source_doc IS NULL`` 且 ``kp_verified`` 为假」的存量题
            → **拒绝导入，直接 exit 2**（由 CLI 捕获 :class:`SchemaGuardError` 退出）。

为什么两道都要：迁移 SQL 可能被漏跑、被人工分两次执行、或在 SQLite 上因
``IF NOT EXISTS`` 不受支持而中断在半路。保险② 保证「无论 DB 处于哪种半吊子
状态，导入器要么修好它，要么拒绝跑」，不会把存量 909 题的统计打断。
"""

from __future__ import annotations

import logging
from typing import Dict, List, Tuple

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.services.importers.schema import SchemaGuardError

logger = logging.getLogger("app.services.importers.schema_guard")

#: T05 新增的 5 列：``列名 -> (PG DDL 片段, SQLite DDL 片段)``
#:
#: 两方言差异只在布尔默认值（PG 用 ``TRUE``，SQLite 用 ``1``）。
BANK_COLUMNS: Tuple[Tuple[str, str, str], ...] = (
    ("edition", "VARCHAR(16)", "VARCHAR(16)"),
    ("source_type", "VARCHAR(16)", "VARCHAR(16)"),
    ("section", "VARCHAR(32)", "VARCHAR(32)"),
    ("source_doc", "VARCHAR(256)", "VARCHAR(256)"),
    ("kp_verified", "BOOLEAN NOT NULL DEFAULT TRUE", "BOOLEAN NOT NULL DEFAULT 1"),
)

#: 需要保证存在的索引：``索引名 -> 列定义``
BANK_INDEXES: Tuple[Tuple[str, str], ...] = (
    ("idx_questions_source_doc", "questions (source_doc)"),
    ("idx_questions_edition_grade", "questions (edition, grade)"),
)


def _is_sqlite(engine: Engine) -> bool:
    """当前引擎是否 SQLite。

    Args:
        engine: SQLAlchemy 引擎。

    Returns:
        是 SQLite 返回 True。
    """
    return engine.dialect.name == "sqlite"


def existing_columns(engine: Engine, table: str = "questions") -> Dict[str, dict]:
    """取表的现有列信息。

    Args:
        engine: SQLAlchemy 引擎。
        table: 表名。

    Returns:
        ``{列名: 列信息}``；表不存在时返回空字典。
    """
    inspector = inspect(engine)
    if not inspector.has_table(table):
        return {}
    return {col["name"]: col for col in inspector.get_columns(table)}


def ensure_bank_columns(engine: Engine, *, strict: bool = True) -> Dict[str, object]:
    """保证 ``questions`` 表上 T05 的 5 列齐全，并做 V7 前置守卫。

    执行顺序（每一步都幂等）：
        1. 探测缺列 → ``ALTER TABLE ADD COLUMN``；
        2. 只要本次补过 ``kp_verified``，或库里存在 ``kp_verified`` 为假的存量题，
           就执行回填 ``UPDATE questions SET kp_verified=TRUE WHERE source_doc IS NULL``；
        3. 补建索引；
        4. **复查**：仍存在「``source_doc IS NULL`` 且 ``kp_verified`` 为假」→ 抛
           :class:`SchemaGuardError`。

    Args:
        engine: SQLAlchemy 引擎。
        strict: 复查不通过时是否抛异常。``False`` 仅记录（单测探针用）。

    Returns:
        ``{"added": [...], "backfilled": N, "legacy_unverified": N}``。

    Raises:
        SchemaGuardError: ``questions`` 表不存在，或 V7 复查未通过（strict 时）。
    """
    columns = existing_columns(engine)
    if not columns:
        raise SchemaGuardError("questions 表不存在，请先执行 Base.metadata.create_all() 或 001 迁移")

    sqlite = _is_sqlite(engine)
    added: List[str] = []
    true_literal = "1" if sqlite else "TRUE"

    with engine.begin() as conn:
        # ---- 1. 补列 ---------------------------------------------------
        for name, pg_ddl, sqlite_ddl in BANK_COLUMNS:
            if name in columns:
                continue
            ddl = sqlite_ddl if sqlite else pg_ddl
            conn.execute(text(f"ALTER TABLE questions ADD COLUMN {name} {ddl}"))
            added.append(name)
            logger.warning("[schema_guard] questions 表补充列: %s %s", name, ddl)

        # ---- 2. 补索引 -------------------------------------------------
        for index_name, definition in BANK_INDEXES:
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {definition}"))

    # ---- 3. V7 复查（独立连接，读到的是已提交状态）----------------------
    with engine.connect() as conn:
        legacy_unverified = int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM questions "
                    f"WHERE source_doc IS NULL AND (kp_verified IS NULL OR kp_verified <> {true_literal})"
                )
            ).scalar()
            or 0
        )

    # ---- 4. 严格模式：发现未复核存量 → **拒绝导入**，绝不静默修正 --------
    #   这与 R-23 同源：运营标记（含 kp_verified）不允许被自动流程篡改。
    #   若此处先回填再复查，严格分支将永远不可达（V7 守卫形同虚设），且
    #   自动把"未人工确认的题"标成已复核，正是 R-23 要防的静默腐化。
    #   非严格模式（如 seed_db 首次建库）才允许自动回填。
    if legacy_unverified and strict:
        raise SchemaGuardError(
            f"V7 守卫拒绝导入：仍有 {legacy_unverified} 道存量题（source_doc IS NULL）"
            f"的 kp_verified 为假。这会让掌握度/画像/家长周报当场断流，"
            f"且属于运营标记被自动篡改（R-23 同源风险）。"
            f"请先人工复核或手工回填后重试（不要依赖自动修正）。"
        )

    backfilled = 0
    if legacy_unverified and not strict:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE questions SET kp_verified = :t "
                    "WHERE source_doc IS NULL AND (kp_verified IS NULL OR kp_verified <> :t)"
                ),
                {"t": True},
            )
        backfilled = legacy_unverified
        logger.warning(
            "[schema_guard] 已回填 %d 道存量题的 kp_verified=TRUE（非严格模式）", backfilled
        )

    return {"added": added, "backfilled": backfilled, "legacy_unverified": legacy_unverified}


def ensure_mistake_kp_column(engine: Engine) -> Dict[str, object]:
    """保证 ``mistakes`` 表存在 ``kp_id`` 列（T01 · 错题→知识点深链）。

    沿用 :func:`ensure_bank_columns` 模式：先 ``inspect`` 探测列是否存在，
    不存在则 ``ALTER TABLE ADD COLUMN`` + 回填 + 建索引。全部幂等。

    与 005 迁移 SQL 的关系：
        * PG 走迁移文件（``005_mistake_kp.sql``）；
        * SQLite 的 ``ADD COLUMN`` 不支持 ``IF NOT EXISTS``，靠本函数运行时探测兜底。
        * 两套机制互不依赖，谁先跑都行，重复执行不报错。

    Args:
        engine: SQLAlchemy 引擎。

    Returns:
        ``{"added": bool, "backfilled": int}``。

    Raises:
        SchemaGuardError: ``mistakes`` 表不存在。
    """
    columns = existing_columns(engine, table="mistakes")
    if not columns:
        raise SchemaGuardError("mistakes 表不存在，请先执行 Base.metadata.create_all() 或 001 迁移")

    if "kp_id" in columns:
        return {"added": False, "backfilled": 0}

    sqlite = _is_sqlite(engine)
    col_ddl = "CHAR(36) REFERENCES knowledge_points(id) ON DELETE SET NULL" if sqlite else (
        "UUID REFERENCES knowledge_points(id) ON DELETE SET NULL"
    )

    added = False
    backfilled = 0

    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE mistakes ADD COLUMN kp_id {col_ddl}"))
        added = True
        logger.warning("[schema_guard] mistakes 表补充列: kp_id %s", col_ddl)

        # 回填（与 ADD COLUMN 同事务）：存量错题从题目继承 kp_id
        result = conn.execute(
            text(
                "UPDATE mistakes SET kp_id = ("
                "SELECT q.kp_id FROM questions q WHERE q.id = mistakes.question_id"
                ") WHERE mistakes.kp_id IS NULL"
            )
        )
        backfilled = int(result.rowcount or 0)
        if backfilled:
            logger.warning("[schema_guard] 已回填 %d 条存量错题的 kp_id", backfilled)

        # 建索引
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_mistakes_kp ON mistakes (kp_id)"))

    return {"added": added, "backfilled": backfilled}


def ensure_source_tag_column(engine: Engine) -> Dict[str, object]:
    """保证 ``questions`` 表存在 ``source_tag`` 列（T06 · 溯源护栏 · examprep 借鉴）。

    沿用 :func:`ensure_mistake_kp_column` 模式：先 ``inspect`` 探测列是否存在，
    不存在则 ``ALTER TABLE ADD COLUMN`` + 回填 + 建索引。全部幂等。

    回填语义（与 migrations/006_source_tag_sqlite.sql 一致）：
        * ``source_doc = 'ai_gen'`` → ``red``（疑似编造/未核验，进待抽检队列）；
        * 其余（docx 资料题）→ ``green``（已核验，讲解可直接信任）。

    Args:
        engine: SQLAlchemy 引擎。

    Returns:
        ``{"added": bool, "backfilled": int, "red": int, "green": int}``。

    Raises:
        SchemaGuardError: ``questions`` 表不存在。
    """
    columns = existing_columns(engine, table="questions")
    if not columns:
        raise SchemaGuardError("questions 表不存在，请先执行 Base.metadata.create_all() 或 001 迁移")

    if "source_tag" in columns:
        return {"added": False, "backfilled": 0, "red": 0, "green": 0}

    added = False
    backfilled = 0
    red = 0
    green = 0

    with engine.begin() as conn:
        # SQLite 不支持 NOT NULL + DEFAULT 同条，建为 nullable 再回填
        conn.execute(text("ALTER TABLE questions ADD COLUMN source_tag VARCHAR(8)"))
        added = True
        logger.warning("[schema_guard] questions 表补充列: source_tag VARCHAR(8)")

        r = conn.execute(
            text("UPDATE questions SET source_tag = 'red' WHERE source_doc = 'ai_gen'")
        )
        red = int(r.rowcount or 0)
        g = conn.execute(
            text(
                "UPDATE questions SET source_tag = 'green' "
                "WHERE source_tag IS NULL OR source_tag <> 'red'"
            )
        )
        green = int(g.rowcount or 0)
        backfilled = red + green
        if backfilled:
            logger.warning(
                "[schema_guard] 已回填 source_tag：red=%d, green=%d", red, green
            )

        conn.execute(
            text("CREATE INDEX IF NOT EXISTS idx_questions_source_tag ON questions (source_tag)")
        )

    return {"added": added, "backfilled": backfilled, "red": red, "green": green}


def ensure_ship_ready_column(engine: Engine) -> Dict[str, object]:
    """保证 ``questions`` 表存在 ``ship_ready`` 列（T07 · 版权护栏）。

    沿用 :func:`ensure_source_tag_column` 模式：先 ``inspect`` 探测列是否存在，
    不存在则 ``ALTER TABLE ADD COLUMN`` + 回填 + 建索引。全部幂等。

    回填语义（与 migrations/007_ship_ready_sqlite.sql 一致）：
        * 存量题（导入本功能前的所有题）默认可发布 → ``ship_ready = 1``；
        * 真实考卷导入时若版权未清/仅内部评测，由导入脚本**显式**置 ``0``。
        * 本函数不做"置 0"的自动判定，只保证列存在且存量非空。

    Args:
        engine: SQLAlchemy 引擎。

    Returns:
        ``{"added": bool, "backfilled": int}``。

    Raises:
        SchemaGuardError: ``questions`` 表不存在。
    """
    columns = existing_columns(engine, table="questions")
    if not columns:
        raise SchemaGuardError("questions 表不存在，请先执行 Base.metadata.create_all() 或 001 迁移")

    if "ship_ready" in columns:
        return {"added": False, "backfilled": 0}

    added = False
    backfilled = 0

    with engine.begin() as conn:
        # SQLite 不支持 NOT NULL + DEFAULT 同条，建为 nullable 再回填
        conn.execute(text("ALTER TABLE questions ADD COLUMN ship_ready BOOLEAN"))
        added = True
        logger.warning("[schema_guard] questions 表补充列: ship_ready BOOLEAN")

        r = conn.execute(
            text("UPDATE questions SET ship_ready = 1 WHERE ship_ready IS NULL")
        )
        backfilled = int(r.rowcount or 0)
        if backfilled:
            logger.warning(
                "[schema_guard] 已回填 %d 道存量题的 ship_ready=1（默认可发布）", backfilled
            )

        conn.execute(
            text("CREATE INDEX IF NOT EXISTS idx_questions_ship_ready ON questions (ship_ready)")
        )

    return {"added": added, "backfilled": backfilled}


__all__ = [
    "BANK_COLUMNS",
    "BANK_INDEXES",
    "existing_columns",
    "ensure_bank_columns",
    "ensure_mistake_kp_column",
    "ensure_source_tag_column",
    "ensure_ship_ready_column",
]
