"""把 ``backend/seed/*.json`` 灌进 knowledge_points / questions / answers（T02 · 2.3）。

幂等设计（重跑不产生重复）：
    * 题目主键不用随机 UUID，而是由题库业务 id 派生的 **uuid5 确定性主键**
      （``uuid5(NS_QUESTION_BANK, "qbank:" + bankId)``），
      因此「按 question.id 存在则跳过」天然成立，且换库重跑 id 保持一致。
    * 知识点主键同理由 ``code`` 派生；已存在则按需补齐 name/description/sort。
    * 答案主键由 ``"qbank-answer:" + bankId`` 派生，1:1 挂在题目上。

方言中立：
    * 只用 SQLAlchemy ORM + ``select``，不写任何 PG 专属 SQL；
    * 幂等靠「先 select 后 insert」，不用 ``ON CONFLICT``（SQLite 语法不同）。

学段规则（老板拍板）：
    * 小学 6 个年级 893 题 → ``status='active'``
    * 初中 16 题        → ``status='archived'``（入库留存，不进推荐池）

用法::

    DATABASE_URL=sqlite:///./dev.db python backend/scripts/seed_db.py
    DATABASE_URL=sqlite:///./dev.db python backend/scripts/seed_db.py --stage 小学
    DATABASE_URL=postgresql+psycopg://coach:coach@localhost:5433/coach \\
        python backend/scripts/seed_db.py
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# 目录结构: <root>/backend/scripts/seed_db.py → BACKEND_DIR = <root>/backend
BACKEND_DIR: Path = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import func, inspect, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import DATABASE_URL, SessionLocal, engine  # noqa: E402
from app.models import Answer, Base, KnowledgePoint, Question  # noqa: E402
from app.models.content import (  # noqa: E402
    QUESTION_STATUS_ACTIVE,
    QUESTION_STATUS_ARCHIVED,
    SUBJECT_DEFAULT,
)

#: seed 文件默认目录
SEED_DIR: Path = BACKEND_DIR / "seed"

#: uuid5 命名空间（固定值，任何环境派生结果一致，切勿修改）
NS_QUESTION_BANK: uuid.UUID = uuid.UUID("6f1b1a1e-6b3f-5c2b-9a44-0d5f1c0a7e21")

#: 允许入库的题目状态
_VALID_STATUS = (QUESTION_STATUS_ACTIVE, QUESTION_STATUS_ARCHIVED)


# ==================================================================== 工具
def question_uuid(bank_id: str) -> uuid.UUID:
    """由题库业务 id 派生题目主键（确定性）。"""
    return uuid.uuid5(NS_QUESTION_BANK, f"qbank:{bank_id}")


def answer_uuid(bank_id: str) -> uuid.UUID:
    """由题库业务 id 派生答案主键（确定性）。"""
    return uuid.uuid5(NS_QUESTION_BANK, f"qbank-answer:{bank_id}")


def kp_uuid(code: str) -> uuid.UUID:
    """由知识点 code 派生知识点主键（确定性）。"""
    return uuid.uuid5(NS_QUESTION_BANK, f"kp:{code}")


def load_json(path: Path) -> Any:
    """读取 UTF-8 JSON 文件。"""
    if not path.exists():
        raise FileNotFoundError(f"找不到 seed 文件: {path}（请先跑 scripts/extract_bank.py）")
    return json.loads(path.read_text(encoding="utf-8"))


#: T05 题库扩容新增列：``列名 -> ALTER 片段``（两方言通用写法，不用 IF NOT EXISTS）。
#:
#: ``kp_verified`` 默认 TRUE 的原因见 V7：存量种子题都是人工整理的，
#: 补列瞬间必须立刻被视为「已复核」，否则学习画像统计会瞬时塌陷。
#: 该列的 DEFAULT 由建表侧保证，回填由下面的 UPDATE 兜底。
_T05_BANK_COLUMNS: Dict[str, str] = {
    "edition": "ALTER TABLE questions ADD COLUMN edition VARCHAR(16)",
    "source_type": "ALTER TABLE questions ADD COLUMN source_type VARCHAR(16)",
    "section": "ALTER TABLE questions ADD COLUMN section VARCHAR(16)",
    "source_doc": "ALTER TABLE questions ADD COLUMN source_doc VARCHAR(255)",
    "kp_verified": "ALTER TABLE questions ADD COLUMN kp_verified BOOLEAN NOT NULL DEFAULT 1",
}


def ensure_schema() -> None:
    """保证表结构存在且含 T04/T05 新增列（幂等，两方言通用）。

    * 表不存在 → ``Base.metadata.create_all``；
    * 表存在但缺 ``status`` 列（T01 建的老库）→ ``ALTER TABLE ADD COLUMN``；
    * 表存在但缺 T05 题库扩容五列（edition / source_type / section /
      source_doc / kp_verified）→ 逐列 ``ALTER``，并对 ``kp_verified``
      立刻回填 TRUE（存量题视为已复核，见 V7 线上事故复盘）。

    该函数与 ``app.services.importers.schema_guard.ensure_bank_columns``
    是**同一件事的两个入口**：seed 走这里，导入 CLI 走那里，二者互相兜底。
    """
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    columns = {col["name"] for col in inspector.get_columns("questions")}

    if "status" not in columns:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE questions ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'active'")
            )
        print("[seed_db] 已为 questions 表补充 status 列")

    is_sqlite = engine.dialect.name == "sqlite"
    missing = [name for name in _T05_BANK_COLUMNS if name not in columns]
    if not missing:
        return

    with engine.begin() as conn:
        for name in missing:
            ddl = _T05_BANK_COLUMNS[name]
            if name == "kp_verified" and not is_sqlite:
                # PostgreSQL 布尔字面量用 TRUE，SQLite 用 1
                ddl = ddl.replace("DEFAULT 1", "DEFAULT TRUE")
            conn.execute(text(ddl))
        if "kp_verified" in missing:
            # 存量题（source_doc IS NULL）一律视为已人工复核，与补列同事务完成
            conn.execute(
                text(
                    "UPDATE questions SET kp_verified = {truthy} "
                    "WHERE source_doc IS NULL".format(truthy="1" if is_sqlite else "TRUE")
                )
            )
    print(f"[seed_db] 已为 questions 表补充 T05 题库列：{', '.join(missing)}")


# ==================================================================== 知识点
def seed_knowledge_points(session: Session, items: Iterable[Dict[str, Any]]) -> Dict[str, uuid.UUID]:
    """写入/补齐知识点，返回 ``{name: kp_id}`` 与 ``{code: kp_id}`` 合并映射。

    Args:
        session: 数据库会话。
        items: ``[{"code","name","sort","description"}, ...]``。

    Returns:
        既能用 name 也能用 code 查到知识点主键的字典。
    """
    lookup: Dict[str, uuid.UUID] = {}
    created = 0
    updated = 0

    for item in items:
        code = str(item.get("code") or "").strip()
        name = str(item.get("name") or "").strip()
        if not code or not name:
            continue

        row: Optional[KnowledgePoint] = session.execute(
            select(KnowledgePoint).where(KnowledgePoint.code == code)
        ).scalar_one_or_none()

        if row is None:
            row = KnowledgePoint(
                id=kp_uuid(code),
                code=code,
                name=name,
                description=item.get("description") or None,
                sort=int(item.get("sort") or 0),
                parent_id=None,
            )
            session.add(row)
            created += 1
        else:
            changed = False
            if row.name != name:
                row.name = name
                changed = True
            desc = item.get("description") or None
            if desc and row.description != desc:
                row.description = desc
                changed = True
            sort = int(item.get("sort") or 0)
            if row.sort != sort:
                row.sort = sort
                changed = True
            if changed:
                updated += 1

        lookup[code] = row.id
        lookup[name] = row.id

    session.flush()
    print(f"[seed_db] 知识点：新增 {created} 条，更新 {updated} 条，共 {len(set(lookup.values()))} 个")
    return lookup


# ==================================================================== 题目
def _resolve_status(record: Dict[str, Any]) -> str:
    """取记录的入库状态，非法值一律按 archived 处理（保守策略）。"""
    status = str(record.get("status") or "").strip()
    if status in _VALID_STATUS:
        return status
    return QUESTION_STATUS_ACTIVE if record.get("stage") == "小学" else QUESTION_STATUS_ARCHIVED


def _build_analysis(record: Dict[str, Any]) -> Optional[str]:
    """用 tags 拼一段轻量解析（V4.1 题库无原始解析文本）。"""
    parts: List[str] = []
    knowledge = record.get("knowledge")
    if knowledge:
        parts.append(f"知识点：{knowledge}")
    difficulty_label = record.get("difficultyLabel")
    if difficulty_label:
        parts.append(f"难度：{difficulty_label}")
    error_type = record.get("errorType")
    if error_type:
        parts.append(f"易错点：{error_type}")
    ability = record.get("ability")
    if ability:
        parts.append(f"考察能力：{ability}")
    return "；".join(parts) if parts else None


def seed_questions(
    session: Session,
    records: Iterable[Dict[str, Any]],
    kp_lookup: Dict[str, uuid.UUID],
    stages: Optional[Iterable[str]] = None,
) -> Dict[str, int]:
    """写入题目与答案（幂等：主键已存在则跳过）。

    Args:
        session: 数据库会话。
        records: 由 ``extract_bank.py`` 产出的题目列表。
        kp_lookup: 知识点 name/code → id 映射。
        stages: 只入指定学段；``None`` 表示全部（小学 active + 初中 archived）。

    Returns:
        统计字典 ``{"inserted","skipped","statusFixed","answers","missingKp"}``。
    """
    stage_filter = set(stages) if stages else None
    stats = {"inserted": 0, "skipped": 0, "statusFixed": 0, "answers": 0, "missingKp": 0}

    for record in records:
        stage = str(record.get("stage") or "")
        if stage_filter is not None and stage not in stage_filter:
            continue

        bank_id = str(record.get("bankId") or "").strip()
        if not bank_id:
            continue

        qid = question_uuid(bank_id)
        status = _resolve_status(record)

        existing: Optional[Question] = session.get(Question, qid)
        if existing is not None:
            # 幂等：已存在只做状态纠偏（初中题必须是 archived），不覆盖题干
            if existing.status != status:
                existing.status = status
                stats["statusFixed"] += 1
            stats["skipped"] += 1
            continue

        kp_id = kp_lookup.get(str(record.get("kpCode") or "")) or kp_lookup.get(
            str(record.get("knowledge") or "")
        )
        if kp_id is None:
            stats["missingKp"] += 1

        question = Question(
            id=qid,
            content=str(record.get("content") or "").strip(),
            image_url=None,
            subject=SUBJECT_DEFAULT,
            grade=record.get("grade"),
            difficulty=int(record.get("difficulty") or 3),
            kp_id=kp_id,
            type_=record.get("type") or None,
            options=record.get("options") or None,
            status=status,
        )
        session.add(question)

        answer = Answer(
            id=answer_uuid(bank_id),
            question_id=qid,
            correct_answer=str(record.get("answer") or "").strip() or None,
            analysis=_build_analysis(record),
            distractor_analysis={},
        )
        session.add(answer)

        stats["inserted"] += 1
        stats["answers"] += 1

        if stats["inserted"] % 200 == 0:
            session.flush()

    session.flush()
    return stats


# ==================================================================== 入口
def seed_all(
    session: Session,
    seed_dir: Path = SEED_DIR,
    stages: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """执行完整 seed 流程（供脚本与单测复用）。

    Args:
        session: 已开启的数据库会话（调用方负责 commit）。
        seed_dir: seed JSON 所在目录。
        stages: 只入指定学段；``None`` 表示全部。

    Returns:
        统计字典，含各表最终行数。
    """
    kp_payload = load_json(seed_dir / "knowledge_points.json")
    kp_items = kp_payload.get("knowledgePoints") if isinstance(kp_payload, dict) else kp_payload

    q_payload = load_json(seed_dir / "questions.json")
    q_items = q_payload.get("questions") if isinstance(q_payload, dict) else q_payload

    kp_lookup = seed_knowledge_points(session, kp_items or [])
    stats = seed_questions(session, q_items or [], kp_lookup, stages=stages)

    counts = {
        "knowledgePoints": int(session.execute(select(func.count()).select_from(KnowledgePoint)).scalar() or 0),
        "questions": int(session.execute(select(func.count()).select_from(Question)).scalar() or 0),
        "answers": int(session.execute(select(func.count()).select_from(Answer)).scalar() or 0),
        "questionsActive": int(
            session.execute(
                select(func.count()).select_from(Question).where(Question.status == QUESTION_STATUS_ACTIVE)
            ).scalar()
            or 0
        ),
        "questionsArchived": int(
            session.execute(
                select(func.count()).select_from(Question).where(Question.status == QUESTION_STATUS_ARCHIVED)
            ).scalar()
            or 0
        ),
    }
    return {**stats, **counts}


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="把 seed JSON 灌进 knowledge_points / questions / answers")
    parser.add_argument("--seed-dir", default=str(SEED_DIR), help="seed JSON 目录")
    parser.add_argument(
        "--stage",
        action="append",
        default=None,
        help="只入指定学段（可重复），如 --stage 小学；缺省全部",
    )
    args = parser.parse_args()

    print(f"[seed_db] DATABASE_URL = {DATABASE_URL}")
    ensure_schema()

    session: Session = SessionLocal()
    try:
        result = seed_all(session, Path(args.seed_dir).resolve(), stages=args.stage)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    print(f"[seed_db] 新增题目           : {result['inserted']}")
    print(f"[seed_db] 跳过（已存在）     : {result['skipped']}")
    print(f"[seed_db] 状态纠偏           : {result['statusFixed']}")
    print(f"[seed_db] 未匹配知识点的题目 : {result['missingKp']}")
    print(f"[seed_db] knowledge_points 行数 : {result['knowledgePoints']}")
    print(f"[seed_db] questions 行数        : {result['questions']}"
          f"（active {result['questionsActive']} / archived {result['questionsArchived']}）")
    print(f"[seed_db] answers 行数          : {result['answers']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
