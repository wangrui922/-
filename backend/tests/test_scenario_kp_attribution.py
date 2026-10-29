# -*- coding: utf-8 -*-
"""B-0 回填的**归因修通**证明（本期真正价值所在）。

背景
----
``generate_scenario_bank.py`` 把 864 道场景题的 ``kp_id`` 硬编码成 ``None``。
后果不是"题库少 864 道题"，而是**归因断链**：

* ``student_dna_service._build_six_dim_events``（``:467``）在 ``kp_id is None``
  时直接 ``continue`` → 没有 ``SixDimEvent`` → 六维掌握度算不出来；
* ``student_dna_service._build_retention_events``（``:514``）同理 → 没有
  ``RetentionEvent`` → 遗忘曲线断掉；
* 上游 ``practice_service.submit_answer``（``:1245`` / ``:1276``）把
  ``knowledge_point_id=question.kp_id`` 写进事件——``kp_id`` 为空，事件就没有
  知识点，L1 → L2 → L3 整条学情链路断在这里。

本文件证明的就是：**回填之后，这条链路被接通**。

四个证明
--------
    P1  **L1 六维事件**：回填前 ``knowledge_point_id`` 为 None、六维事件数为 0；
        回填后带上了知识点 id、六维事件数 ≥ 1（``student_dna_service:467``）。
    P2  **L1 遗忘事件**：回填前复习召回事件数为 0；回填后 ≥ 1（``:514``）。
    P3  **可归因题池**：``practice_service:369`` 的可归因池从「N」涨到「N + 场景题数」，
        且三档抽题的第三档（放开 kp）**没有**因此变形——第三档 == 全池 == 可归因池。
    P4  **archived 0 例外**：archived 题在「学生抽题 / 错题本 / 推荐题」三处
        出现次数必须是 **0**。

⚠️ 本文件**绝不设置** ``DATABASE_URL``（回归测试一律走内存 SQLite）。
    本文件用的是**自己建的临时文件库**（``tmp_path``），与全局内存库完全隔离；
    回填脚本跑在那个临时文件库上——**跑的是真脚本**，不是手工改 kp_id。
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BACKEND_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import backfill_scenario_kp as bsk  # noqa: E402
from app.engines.scenario_kp_mapping import SCENARIO_KP_MAPPING  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.content import Answer, KnowledgePoint, Question  # noqa: E402
from app.models.learning import LearningTask  # noqa: E402
from app.models.user import Student, User  # noqa: E402
from app.services import learning_service, practice_service, profile_service  # noqa: E402
from app.services import student_dna_service  # noqa: E402

#: 目标知识点的元数据（与真实库一致，用于构造种子数据）
KP_SPECS: Tuple[Tuple[str, str, int, int, int], ...] = (
    ("KP_CALC_DIV_1D_VERIFY", "除法验算与有余数", 3, 4, 3),
    ("KP_CALC_DIV_1D", "除数是一位数的除法", 3, 5, 3),
    ("KP_CALC_MUL", "乘除法", 3, 5, 1),
    ("KP_CALC_ADD", "加减法", 3, 5, 1),
    ("KP_WORD_TRAVEL", "行程问题", 5, 5, 5),
    ("KP_UNIT_TIME_ELAPSE", "经过时间计算", 3, 4, 3),
    ("KP_UNIT_TIME", "时间单位", 3, 4, 3),
)

#: 场景题选项（与真实题库的形态一致：4 选 1，答案写成「选X」）
SCENARIO_OPTIONS = {"A": "3", "B": "4", "C": "5", "D": "6"}


# ============================================================ 临时文件库夹具
class ScenarioBank:
    """一个自建的临时题库（文件型 SQLite），供本文件的所有用例共享。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.engine = create_engine(
            f"sqlite:///{db_path.as_posix()}",
            connect_args={"check_same_thread": False},
            future=True,
        )

        @event.listens_for(self.engine, "connect")
        def _set_sqlite_pragma(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

        self.Session = sessionmaker(
            bind=self.engine, class_=Session, autoflush=False, expire_on_commit=False
        )
        self.kp_ids: Dict[str, str] = {}
        self.scenario_question_ids: Dict[str, str] = {}
        self.archived_question_ids: List[str] = []
        self.attributable_question_ids: List[str] = []
        self.student_id: uuid.UUID = uuid.uuid4()

    # ------------------------------------------------------------ 建库
    def create_schema(self) -> None:
        """建全量表（复用 ORM metadata，与真实库同构）。"""
        Base.metadata.create_all(self.engine)
        # FSRS 两张表**没有 ORM 模型**，按迁移 013 的 DDL 手建
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS review_cards ("
                    "id TEXT PRIMARY KEY, student_id TEXT, question_id TEXT, "
                    "kp_id TEXT, due TEXT, state INTEGER, created_at TEXT, updated_at TEXT)"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS review_logs ("
                    "id TEXT PRIMARY KEY, card_id TEXT, student_id TEXT, "
                    "rating INTEGER, review_at TEXT)"
                )
            )

    def seed(self) -> None:
        """造数据：7 个知识点 + 24 道场景题（kp_id 为空）+ 若干对照题。"""
        with self.Session() as db:
            for index, (code, name, grade, importance, difficulty) in enumerate(KP_SPECS):
                kp = KnowledgePoint(
                    name=name,
                    code=code,
                    subject="数学",
                    grade=grade,
                    importance=importance,
                    difficulty=difficulty,
                    status="active",
                    sort=index,
                )
                db.add(kp)
                db.flush()
                self.kp_ids[code] = str(kp.id)

            # 24 道场景题：kp_id 全部为空（模拟回填前的存量状态）
            for index, template in enumerate(sorted(SCENARIO_KP_MAPPING)):
                question = Question(
                    content=f"场景题 {template}：某商店上午卖出 3 盒铅笔，每盒 4 元，一共多少元？",
                    subject="数学",
                    grade=3 + (index % 3),
                    difficulty=3,
                    type_="选择",
                    options=dict(SCENARIO_OPTIONS),
                    status="active",
                    source_type="scenario_bank",
                    source_doc="scenario_bank",
                    kp_verified=True,
                    source_tag="green",
                    ship_ready=True,
                    scenario_type=template.split("/")[0],
                    scenario_template=template,
                    scenario_params=json.dumps({"variant": index}, ensure_ascii=False),
                )
                db.add(question)
                db.flush()
                self.scenario_question_ids[template] = str(question.id)
                db.add(
                    Answer(
                        question_id=question.id,
                        correct_answer="选B",
                        analysis="先算单价乘数量。",
                        distractor_analysis=json.dumps(["3", "5", "6"], ensure_ascii=False),
                    )
                )

            # 对照组①：已有知识点归属的 active 题（回填前就属于可归因池）
            for index in range(5):
                question = Question(
                    content=f"对照题 {index}：3 + 4 = ?",
                    subject="数学",
                    grade=3,
                    difficulty=3,
                    type_="选择",
                    options=dict(SCENARIO_OPTIONS),
                    status="active",
                    source_type="seed",
                    source_doc="seed",
                    kp_verified=True,
                    source_tag="green",
                    ship_ready=True,
                    kp_id=uuid.UUID(self.kp_ids["KP_CALC_MUL"]),
                )
                db.add(question)
                db.flush()
                db.add(
                    Answer(
                        question_id=question.id,
                        correct_answer="选B",
                        analysis="3 + 4 = 7。",
                    )
                )
                self.attributable_question_ids.append(str(question.id))

            # 对照组②：**archived** 题（即便 kp_id 已填、可归因，也绝不能被抽到）
            for index in range(3):
                question = Question(
                    content=f"归档题 {index}：待人工复核的题",
                    subject="数学",
                    grade=3,
                    difficulty=3,
                    type_="选择",
                    options=dict(SCENARIO_OPTIONS),
                    status="archived",
                    source_type="scenario_bank",
                    source_doc="scenario_bank",
                    kp_verified=True,
                    source_tag="yellow",
                    ship_ready=False,
                    scenario_type="cake",
                    scenario_template="cake/0",
                    kp_id=uuid.UUID(self.kp_ids["KP_CALC_MUL"]),
                )
                db.add(question)
                db.flush()
                db.add(
                    Answer(
                        question_id=question.id,
                        correct_answer="选B",
                        analysis="待复核。",
                    )
                )
                self.archived_question_ids.append(str(question.id))

            # 学生 + 账号
            user = User(phone=f"bank{uuid.uuid4().hex[:12]}", role="student", status="active")
            db.add(user)
            db.flush()
            student = Student(user_id=user.id, grade=3, stage="primary")
            db.add(student)
            db.flush()
            self.student_id = student.id
            db.commit()

    # ------------------------------------------------------------ 读数
    def session(self) -> Session:
        """开一个新会话。"""
        return self.Session()

    def question_kp_id(self, question_id: str) -> Optional[str]:
        """读某题的 kp_id。"""
        with self.Session() as db:
            row = db.execute(
                select(Question.kp_id).where(Question.id == uuid.UUID(question_id))
            ).scalar_one_or_none()
            return str(row) if row is not None else None

    def run_backfill(self) -> bsk.BackfillOutcome:
        """跑**真实回填脚本**（默认 dry-run，这里跑 confirm）。"""
        return bsk.run_backfill(self.db_path, confirm=True)

    def dispose(self) -> None:
        """关引擎。"""
        self.engine.dispose()


@pytest.fixture()
def bank(tmp_path: Path) -> ScenarioBank:
    """建库 + 造数据（每个用例一份，互不干扰）。"""
    instance = ScenarioBank(tmp_path / "attribution_bank.db")
    instance.create_schema()
    instance.seed()
    try:
        yield instance
    finally:
        instance.dispose()


# ============================================================ 辅助
def _answer_one(
    bank: ScenarioBank, db: Session, template: str, *, correct: bool = True
) -> str:
    """让学生做一道场景题（走 ``practice_service.submit_answer`` 真链路）。

    Args:
        bank: 题库。
        db: 数据库会话。
        template: 场景子模板。
        correct: 是否答对。

    Returns:
        题目 id。
    """
    question_id = bank.scenario_question_ids[template]
    task = LearningTask(
        student_id=bank.student_id,
        task_type="daily",
        items=[{"question_id": question_id, "status": "pending"}],
        status="doing",
    )
    db.add(task)
    db.commit()

    practice_service.submit_answer(
        db,
        student_id=bank.student_id,
        session_id=task.id,
        question_id=uuid.UUID(question_id),
        selected_answer="B" if correct else "A",
        duration_sec=30,
    )
    return question_id


def _learning_events(db: Session, student_id: uuid.UUID) -> List[Any]:
    """取该生全部学习事件。"""
    from app.models.dna import StudentLearningEvent

    return list(
        db.execute(
            select(StudentLearningEvent).where(
                StudentLearningEvent.student_id == student_id
            )
        )
        .scalars()
        .all()
    )


def _dump_questions(bank: ScenarioBank) -> Dict[str, Dict[str, Any]]:
    """把 ``questions`` 全表导出成 ``{id: {列: 值}}``（逐列比对用）。"""
    with bank.engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM questions")).all()
    return {str(row._mapping["id"]): dict(row._mapping) for row in rows}


# ============================================================ P1 · L1 六维事件
class TestL1SixDimAttribution:
    """P1：回填前后，L1 事件的 ``knowledge_point_id`` 从 None 变成真实 id。"""

    def test_before_backfill_l1_event_has_no_knowledge_point(self, bank: ScenarioBank) -> None:
        """回填前：事件落库了，但 ``knowledge_point_id`` 是 None。"""
        with bank.session() as db:
            _answer_one(bank, db, "cake/0")
            db.commit()

            events = _learning_events(db, bank.student_id)
            answer_events = [
                event for event in events if event.event_type in ("ANSWER", "CORRECT", "ERROR")
            ]
            assert answer_events, "应当有作答事件"
            for event in answer_events:
                assert event.knowledge_point_id is None, (
                    "回填前场景题没有知识点，事件必须不带 knowledge_point_id"
                )

    def test_before_backfill_six_dim_events_are_empty(self, bank: ScenarioBank) -> None:
        """回填前：``_build_six_dim_events`` 因为 ``kp_id is None`` 直接 continue → 0 条。

        这就是 ``student_dna_service.py:467`` 造成的断链现场。
        """
        with bank.session() as db:
            _answer_one(bank, db, "cake/0")
            db.commit()

            six_dim = student_dna_service._build_six_dim_events(
                db, student_id=bank.student_id
            )
            assert six_dim == [], "回填前不应有任何六维事件（归因断链）"

    def test_after_backfill_l1_event_carries_knowledge_point(self, bank: ScenarioBank) -> None:
        """回填后：事件带上了知识点 id。"""
        outcome = bank.run_backfill()
        assert outcome.written == len(SCENARIO_KP_MAPPING)

        template = "cake/0"
        expected_kp_id = bank.kp_ids[SCENARIO_KP_MAPPING[template]]
        with bank.session() as db:
            _answer_one(bank, db, template)
            db.commit()

            events = _learning_events(db, bank.student_id)
            answer_events = [
                event for event in events if event.event_type in ("ANSWER", "CORRECT", "ERROR")
            ]
            assert answer_events
            for event in answer_events:
                assert event.knowledge_point_id is not None, "回填后事件必须带知识点"
                assert str(event.knowledge_point_id) == expected_kp_id

    def test_after_backfill_six_dim_events_are_produced(self, bank: ScenarioBank) -> None:
        """回填后：``_build_six_dim_events`` 产出事件（``student_dna_service:467`` 通了）。"""
        bank.run_backfill()

        template = "shopping/2"
        expected_kp_id = bank.kp_ids[SCENARIO_KP_MAPPING[template]]
        with bank.session() as db:
            _answer_one(bank, db, template, correct=False)
            db.commit()

            six_dim = student_dna_service._build_six_dim_events(
                db, student_id=bank.student_id
            )
            assert len(six_dim) == 1, "回填后应产出 1 条六维事件"
            assert six_dim[0].kp_id == expected_kp_id
            assert six_dim[0].is_correct is False

    def test_attribution_chain_is_repaired_by_backfill_alone(self, bank: ScenarioBank) -> None:
        """端到端：同一个学生、同一道题，回填前断链、回填后接通。

        这是本期价值的**唯一硬证据**：改变量只有 ``questions.kp_id`` 一列。
        """
        template = "time/1"
        expected_kp_id = bank.kp_ids[SCENARIO_KP_MAPPING[template]]

        # ---- 回填前 ----
        with bank.session() as db:
            _answer_one(bank, db, template)
            db.commit()
            before_events = student_dna_service._build_six_dim_events(
                db, student_id=bank.student_id
            )
        assert before_events == []

        # ---- 回填（只写 questions.kp_id）----
        outcome = bank.run_backfill()
        # 24 道 active 场景题被回填；3 道 archived 场景题因"已有其它归属"被跳过
        assert outcome.written == len(SCENARIO_KP_MAPPING)
        assert outcome.skipped_mismatch == len(bank.archived_question_ids)

        # ---- 回填后 ----
        with bank.session() as db:
            _answer_one(bank, db, template)
            db.commit()
            after_events = student_dna_service._build_six_dim_events(
                db, student_id=bank.student_id
            )
        assert len(after_events) == 1
        assert after_events[0].kp_id == expected_kp_id


# ============================================================ P2 · L1 遗忘事件
class TestL1RetentionAttribution:
    """P2：回填前后，FSRS 复习召回事件（``student_dna_service:514``）从 0 变 ≥1。"""

    def _make_review_card(self, bank: ScenarioBank, template: str) -> None:
        """造一张复习卡（``kp_id`` 留空，靠 ``questions.kp_id`` 回退）+ 一条复习日志。"""
        question_id = bank.scenario_question_ids[template]
        card_id = f"card-{uuid.uuid4().hex[:12]}"
        with bank.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO review_cards "
                    "(id, student_id, question_id, kp_id, due, state, created_at, updated_at)"
                    " VALUES (:id, :sid, :qid, NULL, :due, 2, :now, :now)"
                ),
                {
                    "id": card_id,
                    "sid": str(bank.student_id),
                    "qid": question_id,
                    "due": "2026-08-11 00:00:00",
                    "now": "2026-08-11 00:00:00",
                },
            )
            conn.execute(
                text(
                    "INSERT INTO review_logs (id, card_id, student_id, rating, review_at)"
                    " VALUES (:id, :cid, :sid, 2, :at)"
                ),
                {
                    "id": f"log-{uuid.uuid4().hex[:12]}",
                    "cid": card_id,
                    "sid": str(bank.student_id),
                    "at": "2026-08-11 00:00:00",
                },
            )

    def test_before_backfill_retention_events_are_empty(self, bank: ScenarioBank) -> None:
        """回填前：``COALESCE(rc.kp_id, q.kp_id)`` 为空 → ``:514`` 处 continue → 0 条。"""
        self._make_review_card(bank, "distance/1")
        with bank.session() as db:
            events = student_dna_service._build_retention_events(
                db, student_id=bank.student_id
            )
            assert events == [], "回填前不应有遗忘事件（归因断链）"

    def test_after_backfill_retention_events_carry_kp(self, bank: ScenarioBank) -> None:
        """回填后：复习召回事件带上知识点。"""
        template = "distance/1"
        expected_kp_id = bank.kp_ids[SCENARIO_KP_MAPPING[template]]
        bank.run_backfill()
        self._make_review_card(bank, template)

        with bank.session() as db:
            events = student_dna_service._build_retention_events(
                db, student_id=bank.student_id
            )
            assert len(events) == 1
            assert events[0].kp_id == expected_kp_id
            assert events[0].recalled_correctly is True


# ============================================================ P3 · 可归因题池
class TestAttributablePool:
    """P3：``practice_service:369`` 的可归因池变化，以及抽题策略未被破坏。"""

    def _pool_sizes(self, bank: ScenarioBank) -> Tuple[int, int, int]:
        """返回 ``(可归因池, 全池, 场景题可归因数)``。"""
        with bank.session() as db:
            attributable = practice_service._candidate_rows(
                db,
                attributable_only=True,
                grade=None,
                edition=None,
                section=None,
                kp_id=None,
                difficulty=None,
            )
            everything = practice_service._candidate_rows(
                db,
                attributable_only=False,
                grade=None,
                edition=None,
                section=None,
                kp_id=None,
                difficulty=None,
            )
        return len(attributable), len(everything), len(SCENARIO_KP_MAPPING)

    def test_backfill_enlarges_attributable_pool_exactly_by_scenario_questions(
        self, bank: ScenarioBank
    ) -> None:
        before_attributable, before_total, scenario_count = self._pool_sizes(bank)
        assert before_attributable == 5, "回填前只有对照组 5 道可归因题"

        outcome = bank.run_backfill()
        assert outcome.written == scenario_count

        after_attributable, after_total, _ = self._pool_sizes(bank)
        assert after_attributable == before_attributable + scenario_count
        # 全池不受影响（回填不新增题、不改题的状态）
        assert after_total == before_total

    def test_third_tier_pool_equals_everything_after_backfill(self, bank: ScenarioBank) -> None:
        """回填后第三档（放开 kp）== 全池 == 可归因池：**抽题策略没有被扭曲**。

        换句话说：回填前场景题只能落在第三档（"不可归因"），
        回填后它们进入第一档（"可归因"），三档之间的相对关系没有被破坏。
        """
        before_attributable, before_total, _ = self._pool_sizes(bank)
        assert before_attributable < before_total, "回填前应存在「不可归因」的题"

        bank.run_backfill()
        after_attributable, after_total, _ = self._pool_sizes(bank)

        assert after_attributable == after_total, "回填后第三档应等于全池"
        assert after_attributable > before_attributable

    def test_all_scenario_questions_are_l1_safe(self, bank: ScenarioBank) -> None:
        """场景题必须**全部**是 L1 安全题，否则回填后也进不了可归因池。"""
        from app.services import judge_engine

        with bank.session() as db:
            rows = db.execute(
                select(Question.type_, Question.options, Answer.correct_answer)
                .join(Answer, Answer.question_id == Question.id)
                .where(Question.source_doc == "scenario_bank", Question.status == "active")
            ).all()
        assert len(rows) == len(SCENARIO_KP_MAPPING)
        for type_, options, correct_answer in rows:
            assert judge_engine.is_l1_safe(type_, options, correct_answer)


# ============================================================ P4 · archived 0 例外
class TestArchivedZeroException:
    """P4：archived 题在学生抽题 / 错题本 / 推荐题三处出现次数必须是 0。"""

    def test_practice_draw_never_picks_archived_questions(self, bank: ScenarioBank) -> None:
        """学生抽题（``draw_questions``）绝不抽 archived 题。"""
        bank.run_backfill()
        with bank.session() as db:
            drawn = practice_service.draw_questions(
                db, student_id=bank.student_id, grade=None, count=30
            )
        picked = {str(item["question_id"]) for item in drawn["questions"]}
        assert picked, "应当抽到题"
        assert not (picked & set(bank.archived_question_ids)), "抽到了 archived 题"

    def test_candidate_pool_excludes_archived_questions(self, bank: ScenarioBank) -> None:
        """候选池本身就不含 archived 题（``status='active'`` 硬过滤）。"""
        bank.run_backfill()
        with bank.session() as db:
            rows = practice_service._candidate_rows(
                db,
                attributable_only=False,
                grade=None,
                edition=None,
                section=None,
                kp_id=None,
                difficulty=None,
            )
        picked = {str(question_id) for question_id, _kp, _verified in rows}
        assert not (picked & set(bank.archived_question_ids))

    def test_mistake_book_has_zero_archived_exceptions(self, bank: ScenarioBank) -> None:
        """错题本：经正常练习链路产生的错题**没有一条**指向 archived 题。

        注意口径：``list_mistakes`` 本身**不**按 ``question.status`` 过滤——
        archived 题之所以进不了错题本，是因为它**根本抽不出来**
        （见上一个用例）。这里断言的是"正常链路 0 例外"。
        """
        bank.run_backfill()
        with bank.session() as db:
            drawn = practice_service.draw_questions(
                db, student_id=bank.student_id, grade=None, count=5
            )
            picked = [item["question_id"] for item in drawn["questions"]]
            for question_id in picked:
                learning_service.record_mistake(
                    db, student_id=bank.student_id, question_id=question_id
                )
            db.commit()

            mistakes = learning_service.list_mistakes(db, student_id=bank.student_id)
            assert mistakes, "应当记录到错题"

            archived_exceptions = [
                mistake
                for mistake in mistakes
                if str(mistake.question_id) in set(bank.archived_question_ids)
            ]
            assert archived_exceptions == [], "错题本里出现了 archived 题"

    def test_recommendations_have_zero_archived_exceptions(self, bank: ScenarioBank) -> None:
        """推荐题（``profile_service._suggested_questions``）不含 archived 题。"""
        bank.run_backfill()
        with bank.session() as db:
            suggestions = profile_service._suggested_questions(
                db, kp_ids=[bank.kp_ids["KP_CALC_MUL"]], limit=10
            )
        flat = [qid for ids in suggestions.values() for qid in ids]
        assert not (set(flat) & set(bank.archived_question_ids)), "推荐题里出现了 archived 题"


# ============================================================ 回填本身的副作用守卫
class TestBackfillSideEffects:
    """回填不得改动除 ``kp_id`` 以外的任何字段。"""

    def test_only_kp_id_column_changes_on_real_backfill(self, bank: ScenarioBank) -> None:
        """跑真脚本前后逐列比对（与 ``test_backfill_scenario_kp.py`` 互为呼应）。"""
        before = _dump_questions(bank)
        bank.run_backfill()
        after = _dump_questions(bank)

        assert set(before) == set(after)
        changed: set[str] = set()
        for question_id, before_row in before.items():
            after_row = after[question_id]
            assert set(before_row) == set(after_row)
            for column in before_row:
                if before_row[column] != after_row[column]:
                    changed.add(column)
        assert changed == {"kp_id"}, f"除 kp_id 外还有列被改: {changed}"

    def test_backfill_maps_every_template_to_its_kp(self, bank: ScenarioBank) -> None:
        """每道场景题都落到映射表指定的知识点上。"""
        bank.run_backfill()
        with bank.session() as db:
            rows = db.execute(
                select(Question.scenario_template, Question.kp_id).where(
                    Question.source_doc == "scenario_bank",
                    Question.status == "active",
                )
            ).all()
        assert len(rows) == len(SCENARIO_KP_MAPPING)
        for template, kp_id in rows:
            assert template in SCENARIO_KP_MAPPING
            assert str(kp_id) == bank.kp_ids[SCENARIO_KP_MAPPING[template]]

    def test_backfill_does_not_touch_archived_questions(self, bank: ScenarioBank) -> None:
        """archived 题的 kp_id 不得被回填改动（人工闸门语义）。"""
        with bank.session() as db:
            before = {
                str(row.id): row.kp_id
                for row in db.execute(
                    select(Question.id, Question.kp_id).where(
                        Question.status == "archived"
                    )
                ).all()
            }

        bank.run_backfill()

        with bank.session() as db:
            after = {
                str(row.id): row.kp_id
                for row in db.execute(
                    select(Question.id, Question.kp_id).where(
                        Question.status == "archived"
                    )
                ).all()
            }
        assert before == after
