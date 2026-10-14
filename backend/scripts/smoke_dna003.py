"""T3 冒烟：practice/chat 埋点链路 → 掌握表更新（DNA-003 联动）。

模拟一次作答的完整事件序列（与 practice_service._emit_dna_event /
chat orchestrator._emit_chat_dna_event 相同的 record_learning_event 入口），
验证：
  1) ANSWER+CORRECT 两条事件 → practice_count 只 +1（不双计）、correct_count +1
  2) ERROR → wrong_count +1、mastery 负增量
  3) confidence 随样本量正确增长
  4) 时间戳分别写入 last_practice_at / last_correct_at / last_wrong_at
  5) 非驱动事件（HINT/REVIEW）不建行、不改掌握表
"""

from __future__ import annotations

import os
import sys
import uuid

# scripts/ 子目录运行时确保项目根在 sys.path（与 pytest rootdir 一致）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod")
os.environ.setdefault("APP_ENV", "test")

# fsrs_scheduler 占位 stub（同 tests/conftest.py 约定，保证可导入）
try:
    from app.services import fsrs_scheduler as _fsrs  # noqa: F401
except (ModuleNotFoundError, ImportError) as _fsrs_missing:
    import types as _types

    _fsrs_stub = _types.ModuleType("app.services.fsrs_scheduler")

    class _Card:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _ReviewLog:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _FSRSScheduler:
        def review_card(self, card, rating, now=None):
            raise NotImplementedError("stub")

        def get_retrievability(self, card):
            raise NotImplementedError("stub")

    _fsrs_stub.Card = _Card
    _fsrs_stub.ReviewLog = _ReviewLog
    _fsrs_stub.FSRSScheduler = _FSRSScheduler
    sys.modules["app.services.fsrs_scheduler"] = _fsrs_stub

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.models import Base  # noqa: E402
from app.models.dna import (  # noqa: E402
    StudentKnowledgeMastery,
    StudentLearningEvent,
)
from app.services.student_dna_service import record_learning_event  # noqa: E402

STUDENT_ID = uuid.uuid4()
KP_ID = uuid.uuid4()
KP_HINT_ONLY = uuid.uuid4()


def get_row(db: Session, kp_id: uuid.UUID) -> StudentKnowledgeMastery:
    return db.execute(
        select(StudentKnowledgeMastery).where(
            StudentKnowledgeMastery.student_id == STUDENT_ID,
            StudentKnowledgeMastery.knowledge_point_id == kp_id,
        )
    ).scalar_one_or_none()


def main() -> None:
    # 独立内存库（与 iso_db fixture 同款），不触碰共享库
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    db = maker()

    # ---- 1) 一次作答：ANSWER + CORRECT ----
    record_learning_event(
        db, student_id=STUDENT_ID, event_type="ANSWER", knowledge_point_id=KP_ID
    )
    record_learning_event(
        db, student_id=STUDENT_ID, event_type="CORRECT", knowledge_point_id=KP_ID
    )
    db.commit()

    row = get_row(db, KP_ID)
    assert row is not None, "ANSWER 事件应自动建行"
    assert row.practice_count == 1, (
        f"practice_count 应=1 实={row.practice_count}（不双计）"
    )
    assert row.correct_count == 1, f"correct_count 应=1 实={row.correct_count}"
    assert row.wrong_count == 0, f"wrong_count 应=0 实={row.wrong_count}"
    assert row.last_practice_at is not None, "last_practice_at 应写入"
    assert row.last_correct_at is not None, "last_correct_at 应写入"
    assert row.mastery == 0.10, f"基础正确 mastery 应=0.10 实={row.mastery}"
    assert row.confidence == 0.5, f"confidence(1次) 应=0.5 实={row.confidence}"
    print(
        f"[ok] 一次作答: practice=1 correct=1 mastery={row.mastery} conf={row.confidence}"
    )

    # ---- 2) 再来一次作答（ANSWER+ERROR：错题）----
    record_learning_event(
        db, student_id=STUDENT_ID, event_type="ANSWER", knowledge_point_id=KP_ID
    )
    record_learning_event(
        db,
        student_id=STUDENT_ID,
        event_type="ERROR",
        knowledge_point_id=KP_ID,
        meta={"hint_count": 2},
    )
    db.commit()

    row = get_row(db, KP_ID)
    assert row.practice_count == 2, f"practice_count 应=2 实={row.practice_count}"
    assert row.correct_count == 1, f"correct_count 应=1 实={row.correct_count}"
    assert row.wrong_count == 1, f"wrong_count 应=1 实={row.wrong_count}"
    assert row.last_wrong_at is not None, "last_wrong_at 应写入"
    assert abs(row.mastery - (0.10 - 0.08)) < 1e-9, f"mastery 应=0.02 实={row.mastery}"
    assert row.confidence == 0.75, f"confidence(2次) 应=0.75 实={row.confidence}"
    print(
        f"[ok] 错题序列: practice=2 wrong=1 mastery={row.mastery} conf={row.confidence}"
    )

    # ---- 3) 提示后正确（hint_count>=1 → +0.05）----
    record_learning_event(
        db,
        student_id=STUDENT_ID,
        event_type="CORRECT",
        knowledge_point_id=KP_ID,
        meta={"hint_used": True},
    )
    db.commit()
    row = get_row(db, KP_ID)
    assert abs(row.mastery - (0.02 + 0.05)) < 1e-9, (
        f"提示后正确 mastery 应=0.07 实={row.mastery}"
    )
    print(f"[ok] 提示后正确: mastery={row.mastery}")

    # ---- 4) 非驱动事件（HINT/REVIEW）不建行不改值 ----
    before_mastery = row.mastery
    record_learning_event(
        db, student_id=STUDENT_ID, event_type="HINT", knowledge_point_id=KP_HINT_ONLY
    )
    record_learning_event(
        db, student_id=STUDENT_ID, event_type="REVIEW", knowledge_point_id=KP_HINT_ONLY
    )
    db.commit()
    assert get_row(db, KP_HINT_ONLY) is None, "HINT/REVIEW 不应建掌握行"
    row = get_row(db, KP_ID)
    assert row.mastery == before_mastery, "非驱动事件不应改掌握值"
    print("[ok] HINT/REVIEW 不驱动掌握表")

    # ---- 5) 事件完整性：8 类事件全部落库（事件流不缩水）----
    types = db.execute(select(StudentLearningEvent.event_type)).scalars().all()
    assert sorted(types) == [
        "ANSWER",
        "ANSWER",
        "CORRECT",
        "CORRECT",
        "ERROR",
        "HINT",
        "REVIEW",
    ], types
    print(f"[ok] 事件流完整: {len(types)} 条")

    db.close()
    print("\nT3 SMOKE PASS: practice/chat 埋点 → 掌握度更新链路正确")


if __name__ == "__main__":
    main()
