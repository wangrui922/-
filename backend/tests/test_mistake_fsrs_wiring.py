"""错题本 × FSRS 复习调度联动 wiring 测试（错题入本即建档 · AI 大脑装配模块4）。

产品口径（逐条对应，docstring 即验收标准）：
    ① 错题入本即建档：``record_mistake`` **新建**成功后建题目粒度卡
       （Again(1) 近期重现），走 review_service 既有封装；
    ② 幂等复用不重复建卡：同题已有未攻克错题时复用旧记录，不建新卡；
    ③ 攻克联动：``mark_mistake_solved`` 后该题卡片不再 due（暂停调度、
       历史保留），错题本出参 due 变 None；
    ④ KP 卡去重：``upsert_cards_from_practice`` 建 KP 卡前查题目卡，
       已覆盖该知识点则跳过（题目卡优先，KP 卡仅兜底）；
    ⑤ 错题本出参带 due：``GET /api/mistake/list`` 补 due（无卡为 None）；
    ⑥ orchestrator 咨询归档路径也建卡（旁路直写改走 record_mistake）；
    ⑦ 双内核灰度：建卡 due 计算走 fsrs_scheduler / review_service 既有
       封装（按学生透传 student_id 裁决 sm2/fsrs_py），不绕过。

口径备注：
    * 本文件**不设** ``DATABASE_URL``，走 conftest 的会话级共享内存
      SQLite（团队回归基线闸门，见 conftest 顶部注释）；
    * ``review_cards`` / ``review_logs`` 是 raw SQL 迁移表（013），不在
      ORM 内，用模块级 fixture 幂等补建（与 test_review_cards_link.py
      同款）；
    * 断言一律挂**自造隔离键**（每用例新学生 / 新题 / 新知识点），不假设
      全表计数——会话级共享库里还有别的文件造的数据。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Tuple

import pytest
from sqlalchemy import text as sa_text

from app.db import SessionLocal, engine
from app.models.content import KnowledgePoint, Question
from app.models.learning import Mistake
from app.models.user import Student, User
from app.services import learning_service, review_service
from app.services.fsrs_scheduler import (
    MODE_FSRS_PY,
    MODE_SM2,
    STATE_LEARNING,
    STATE_RELEARNING,
    resolve_scheduler_mode,
)
from app.services.review_service import ReviewService
from t03_helpers import FakeArk, assert_ok, patch_ark

#: 固定时钟：精确断言 due = now + 1 天（近期重现），不用近似窗口
NOW = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module", autouse=True)
def _fsrs_tables():
    """幂等补建 013 迁移的 review_cards / review_logs 表（raw SQL 迁移表）。"""
    import pathlib

    sql = (
        pathlib.Path(__file__).resolve().parents[1]
        / "migrations"
        / "013_fsrs_cards_logs_sqlite.sql"
    ).read_text(encoding="utf-8")
    with engine.raw_connection() as conn:
        conn.executescript(sql)
    yield


# ------------------------------------------------------------------ 造数据
def _uid() -> str:
    """短随机后缀（隔离键）。"""
    return uuid.uuid4().hex[:8]


def _make_student(db) -> uuid.UUID:
    """造一个干净学生，返回 ID（手机号随机 → 与其他用例隔离）。"""
    user = User(id=uuid.uuid4(), phone=f"13{uuid.uuid4().hex[:9]}", nickname="错题FSRS")
    db.add(user)
    db.flush()
    stu = Student(id=uuid.uuid4(), user_id=user.id, grade=4)
    db.add(stu)
    db.flush()
    return stu.id


def _make_question(db, *, kp_id: Optional[uuid.UUID] = None) -> uuid.UUID:
    """造一道题，返回 ID（content 随机 → 隔离键）。"""
    q = Question(
        id=uuid.uuid4(),
        content=f"错题FSRS-{_uid()}",
        subject="数学",
        grade=4,
        difficulty=3,
        kp_id=kp_id,
    )
    db.add(q)
    db.flush()
    return q.id


def _make_kp(db) -> uuid.UUID:
    """造一个知识点，返回 ID（code 随机后缀避开 UNIQUE）。"""
    kp = KnowledgePoint(
        id=uuid.uuid4(), name=f"KP-{_uid()}", code=f"WIR_KP_{_uid()}", sort=0
    )
    db.add(kp)
    db.flush()
    return kp.id


def _parse_due(raw: Any) -> datetime:
    """把 SQLite RAW SQL 取出的 due 串解析成 datetime。

    SQLite 存的是**空格分隔**（``2026-10-02 08:00:00+00:00``），PG 侧是
    ``T`` 分隔；统一替换后 ``fromisoformat`` 两种都吃。
    """
    text = str(raw).strip().replace(" ", "T", 1)
    return datetime.fromisoformat(text)


def _card_rows(db, student_id: uuid.UUID, question_id: uuid.UUID) -> List[Tuple]:
    """读 (student, question) 的题目粒度复习卡行（隔离键查询）。

    Returns:
        ``[(id, due_raw, state, lapses, reps, kp_id), ...]``。
    """
    return list(
        db.execute(
            sa_text(
                "SELECT id, due, state, lapses, reps, kp_id FROM review_cards "
                "WHERE student_id = :p1 AND question_id = :p2"
            ),
            {"p1": str(student_id), "p2": str(question_id)},
        ).fetchall()
    )


# ================================================================ 口径① 入本即建档
class TestMistakeEntryCreatesCard:
    def test_new_mistake_creates_question_card_with_again(self, db, monkeypatch):
        """口径①：错题入本即建档——record_mistake 新建成功后立即建**题目粒度**
        复习卡：错→Again(1)（state=Relearning(3)、lapses=1、reps=0、
        due=+1 天近期重现）；卡片 kp_id 为空（题目粒度，不吃 KP 口径）。"""
        stu = _make_student(db)
        qid = _make_question(db)
        db.commit()
        monkeypatch.setattr(learning_service, "utcnow", lambda: NOW)

        mistake = learning_service.record_mistake(
            db, student_id=stu, question_id=qid, error_type="CALC"
        )
        assert mistake.solved is False

        rows = _card_rows(db, stu, qid)
        assert len(rows) == 1, f"入本即应建档且仅一张：{rows}"
        _card_id, due_raw, state, lapses, reps, kp_id = rows[0]
        assert kp_id is None  # 题目粒度卡（区别于 KP 卡）
        assert state == STATE_RELEARNING  # Again → 遗忘重学态
        assert lapses == 1
        assert reps == 0
        assert _parse_due(due_raw) == NOW + timedelta(days=1)  # 近期重现

    def test_existing_mistake_reuse_creates_no_second_card(self, db, monkeypatch):
        """口径②：幂等复用不重复建卡——同 (student, question) 已有**未攻克**
        错题时 record_mistake 直接复用旧记录，不触发建卡，复习卡仍只有
        1 张。"""
        stu = _make_student(db)
        qid = _make_question(db)
        db.commit()
        monkeypatch.setattr(learning_service, "utcnow", lambda: NOW)

        m1 = learning_service.record_mistake(db, student_id=stu, question_id=qid)
        m2 = learning_service.record_mistake(db, student_id=stu, question_id=qid)
        assert m2.id == m1.id, "未攻克记录应复用"

        rows = _card_rows(db, stu, qid)
        assert len(rows) == 1, f"复用不应重复建卡：{rows}"

    def test_card_creation_failure_does_not_block_mistake(self, db, monkeypatch):
        """兜底口径：建卡异常不得拖垮错题入本——record_mistake 仍返回已落库
        的错题（主链路优先，复习卡故障只 warning）。"""
        stu = _make_student(db)
        qid = _make_question(db)
        db.commit()

        def _boom(*args, **kwargs):
            raise RuntimeError("模拟建卡故障")

        monkeypatch.setattr(ReviewService, "create_card_for_question", _boom)

        mistake = learning_service.record_mistake(
            db, student_id=stu, question_id=qid
        )
        assert mistake.id is not None
        assert _card_rows(db, stu, qid) == []


# ================================================================ 口径③ 攻克联动
class TestMasteredMistakePausesCard:
    def test_solve_pauses_card_and_keeps_history(self, db, monkeypatch):
        """口径③：攻克后卡片不再 due——mark_mistake_solved 把该题题目卡
        due 置空（暂停调度）：不再进入 get_due_cards 到期队列（哪怕把 now
        推到 10 年后）、错题本出参 due 变 None；卡片行本身保留（不删行，
        历史可查）。"""
        stu = _make_student(db)
        qid = _make_question(db)
        db.commit()
        monkeypatch.setattr(learning_service, "utcnow", lambda: NOW)

        mistake = learning_service.record_mistake(
            db, student_id=stu, question_id=qid
        )
        rows = _card_rows(db, stu, qid)
        card_id = rows[0][0]
        assert rows[0][1] is not None, "建档后 due 应非空"

        learning_service.mark_mistake_solved(db, mistake_id=mistake.id)

        rows = _card_rows(db, stu, qid)
        assert len(rows) == 1, "暂停不删行：历史保留"
        assert rows[0][1] is None, "暂停 = due 置空"

        # 不再进入到期队列
        due_ids = [
            c["id"]
            for c in ReviewService().get_due_cards(
                str(stu), now=NOW + timedelta(days=3650)
            )
        ]
        assert card_id not in due_ids

        # 错题本出参 due：暂停后为 None
        due_map = review_service.get_question_due_map(db, str(stu))
        assert due_map.get(str(qid)) is None

    def test_rewrong_after_mastery_revives_card(self, db, monkeypatch):
        """复活口径：攻克后再次做错同一题——record_mistake 新建错题记录，
        已暂停的复习卡按 Again(1) 复活重新排程（不永远沉底）。"""
        stu = _make_student(db)
        qid = _make_question(db)
        db.commit()
        monkeypatch.setattr(learning_service, "utcnow", lambda: NOW)

        first = learning_service.record_mistake(
            db, student_id=stu, question_id=qid
        )
        learning_service.mark_mistake_solved(db, mistake_id=first.id)
        assert _card_rows(db, stu, qid)[0][1] is None  # 已暂停

        learning_service.record_mistake(db, student_id=stu, question_id=qid)

        rows = _card_rows(db, stu, qid)
        assert len(rows) == 1, "复活不新建行"
        _card_id, due_raw, state, lapses, reps, _kp = rows[0]
        assert _parse_due(due_raw) == NOW + timedelta(days=1)  # Again(1) 近期重现
        assert state == STATE_RELEARNING
        assert lapses == 2  # 原 1 + 本次 Again
        assert reps == 0


# ================================================================ 口径④ KP 卡去重
class TestKpCardDedup:
    def test_upsert_skips_kp_card_when_question_card_exists(self, db, monkeypatch):
        """口径④：题目粒度卡优先——学生已有题目卡覆盖的知识点，
        upsert_cards_from_practice 跳过 KP 卡（KP 卡仅兜底）；没有题目卡
        覆盖的知识点照常建 KP 卡。"""
        stu = _make_student(db)
        kp_covered = _make_kp(db)
        kp_fresh = _make_kp(db)
        q_covered = _make_question(db, kp_id=kp_covered)
        db.commit()
        monkeypatch.setattr(learning_service, "utcnow", lambda: NOW)

        # 做错 kp_covered 的题 → 题目粒度卡（Again）
        learning_service.record_mistake(
            db, student_id=stu, question_id=q_covered
        )

        out = review_service.upsert_cards_from_practice(
            stu,
            {
                str(kp_covered): {"correct": False, "attempts": 2},
                str(kp_fresh): {"correct": True, "attempts": 2},
            },
            now=NOW,
        )
        assert out == {"created": 1, "skipped": 1, "total": 2}, out

        with SessionLocal() as s:
            s.rollback()
            rows = s.execute(
                sa_text(
                    "SELECT question_id, kp_id FROM review_cards "
                    "WHERE student_id = :p1"
                ),
                {"p1": str(stu)},
            ).fetchall()
        by_kind = {(r[0], r[1]) for r in rows}
        assert (str(q_covered), None) in by_kind  # 题目卡在
        assert (None, str(kp_covered)) not in by_kind  # 被题目卡去重
        assert (None, str(kp_fresh)) in by_kind  # KP 兜底卡照常建


# ================================================================ 口径⑤ 错题本出参
class TestMistakeListDue:
    def test_list_returns_due_for_carded_and_none_without(self, client, new_phone):
        """口径⑤：错题本出参带 due——入本即建档的错题返回下次到期时间
        （合法 ISO 8601 串）；无卡错题（建卡前历史数据）due 为 None
        （前端不展示「何时该再练」）。"""
        from conftest import login_with_code

        data = login_with_code(client, new_phone())
        headers = {"Authorization": f"Bearer {data['access']}"}
        sid = uuid.UUID(str(data["user"]["studentId"]))

        # 两道题：q1 走 POST /api/mistake 正常入本（应建卡）；
        # q2 直接插 Mistake 绕过 record_mistake（模拟建卡前历史数据 → 无卡）。
        with SessionLocal() as s:
            q1 = Question(
                id=uuid.uuid4(), content=f"API-{_uid()}", subject="数学", grade=4
            )
            q2 = Question(
                id=uuid.uuid4(), content=f"API-{_uid()}", subject="数学", grade=4
            )
            s.add_all([q1, q2])
            s.flush()
            s.add(Mistake(student_id=sid, question_id=q2.id, solved=False))
            s.commit()
            q1_id, q2_id = str(q1.id), str(q2.id)

        created = assert_ok(
            client.post(
                "/api/mistake", headers=headers, json={"questionId": q1_id}
            )
        )
        assert created["solved"] is False

        items = assert_ok(client.get("/api/mistake/list", headers=headers))["items"]
        by_q = {it["questionId"]: it for it in items}
        assert set(by_q) == {q1_id, q2_id}, sorted(by_q)

        due1 = by_q[q1_id]["due"]
        assert due1 is not None, "入本即建档 → due 应非空"
        parsed = datetime.fromisoformat(due1)  # 合法 ISO 8601
        assert timedelta(hours=23) <= parsed - datetime.now(timezone.utc) <= timedelta(
            days=1, hours=1
        )
        # 无卡错题 → None
        assert by_q[q2_id]["due"] is None

    def test_solved_mistake_due_becomes_none(self, client, new_phone):
        """口径⑤（联动）：攻克后该题出参 due 变 None——PUT .../solve 返回
        条目与列表出参都不再携带「何时该再练」。"""
        from conftest import login_with_code

        data = login_with_code(client, new_phone())
        headers = {"Authorization": f"Bearer {data['access']}"}

        with SessionLocal() as s:
            q = Question(
                id=uuid.uuid4(), content=f"API-{_uid()}", subject="数学", grade=4
            )
            s.add(q)
            s.commit()
            qid = str(q.id)

        created = assert_ok(
            client.post(
                "/api/mistake", headers=headers, json={"questionId": qid}
            )
        )
        mistake_id = str(created["id"])

        items = assert_ok(client.get("/api/mistake/list", headers=headers))["items"]
        before = next(it for it in items if it["questionId"] == qid)
        assert before["due"] is not None

        solved = assert_ok(
            client.put(
                f"/api/mistake/{mistake_id}/solve", headers=headers, json={}
            )
        )
        assert solved["solved"] is True
        assert solved["due"] is None, "攻克后卡片暂停 → due 为 None"

        items = assert_ok(client.get("/api/mistake/list", headers=headers))["items"]
        after = next(it for it in items if it["questionId"] == qid)
        assert after["due"] is None


# ================================================================ 口径⑥ orchestrator 归档
class TestOrchestratorArchiveWiring:
    def test_chat_archive_creates_question_card(self, client, monkeypatch, new_phone):
        """口径⑥：AI 咨询归档路径也建卡——orchestrator 旁路直写改走
        record_mistake 后，学生咨询沉淀的题目同样以题目粒度进入 FSRS
        （Again(1) 近期重现），且错因标签「咨询求助题」语义保留。"""
        from conftest import login_with_code

        patch_ark(monkeypatch, FakeArk(correct_answer="x = 3", solution="等式两边同时除以 7。"))
        data = login_with_code(client, new_phone())
        headers = {"Authorization": f"Bearer {data['access']}"}
        sid = uuid.UUID(str(data["user"]["studentId"]))

        stem = f"咨询归档建卡测试 {_uid()}：解方程 7x = 21"
        assert_ok(client.post("/api/chat", headers=headers, json={"text": stem}))

        with SessionLocal() as s:
            s.rollback()
            qid = s.execute(
                sa_text(
                    "SELECT id FROM questions "
                    "WHERE content = :c AND source_doc = 'student_asked'"
                ),
                {"c": stem},
            ).scalar_one()
            reason = s.execute(
                sa_text(
                    "SELECT error_reason FROM mistakes "
                    "WHERE student_id = :p1 AND question_id = :p2"
                ),
                {"p1": str(sid), "p2": str(qid)},
            ).scalar_one()
            card = s.execute(
                sa_text(
                    "SELECT due, state, lapses FROM review_cards "
                    "WHERE student_id = :p1 AND question_id = :p2"
                ),
                {"p1": str(sid), "p2": str(qid)},
            ).fetchone()

        assert reason == "咨询求助题", "错因标签语义应保留"
        assert card is not None, "咨询归档路径也应建档复习卡"
        due_raw, state, lapses = card
        assert state == STATE_RELEARNING  # Again(1)
        assert lapses == 1
        # 近期重现：建档后约 1 天到期
        assert timedelta(hours=23) <= _parse_due(due_raw) - datetime.now(
            timezone.utc
        ) <= timedelta(days=1, hours=1)


# ================================================================ 口径⑦ 双内核灰度
class TestSchedulerWrapper:
    def _spy_scheduler_for(self, monkeypatch) -> List[dict]:
        """给 ReviewService._scheduler_for 包一层间谍，记录调用路径。"""
        calls: List[dict] = []
        real_scheduler_for = ReviewService._scheduler_for

        def spy(self, student_id):
            scheduler = real_scheduler_for(self, student_id)
            real_review_card = scheduler.review_card

            def wrapped(card, rating, now=None):
                calls.append(
                    {
                        "student_id": student_id,
                        "mode": scheduler.mode,
                        "rating": rating,
                    }
                )
                return real_review_card(card, rating, now)

            scheduler.review_card = wrapped
            return scheduler

        monkeypatch.setattr(ReviewService, "_scheduler_for", spy)
        return calls

    def test_card_due_computed_through_scheduler_wrapper(self, db, monkeypatch):
        """口径⑦：双内核灰度下 due 计算走既有封装——建卡必须经
        ReviewService._scheduler_for(student_id) → FSRSScheduler.review_card
        （按学生稳定哈希裁决 sm2/fsrs_py），不得绕过调度器直写 due。"""
        stu = _make_student(db)
        qid = _make_question(db)
        db.commit()
        calls = self._spy_scheduler_for(monkeypatch)

        learning_service.record_mistake(db, student_id=stu, question_id=qid)

        assert len(calls) == 1, f"建卡应恰好走一次调度封装：{calls}"
        assert calls[0]["student_id"] == str(stu), "学生上下文必须透传（灰度按学生哈希）"
        assert calls[0]["rating"] == 1, "错题入本 = Again(1)"
        assert calls[0]["mode"] in (MODE_SM2, MODE_FSRS_PY)
        assert calls[0]["mode"] == resolve_scheduler_mode(str(stu))

    def test_fsrs_py_full_rollout_also_through_wrapper(self, db, monkeypatch):
        """口径⑦（放量侧）：scheduler=fsrs_py + ratio=1 全量放量时，建卡
        due 同样由封装按该模式算出——证明灰度开关对建卡路径生效、没有
        绕过封装直写 due 的旁路（回滚开关不影响数据的前提）。"""
        from app.core.config import get_settings

        settings_obj = get_settings()
        monkeypatch.setattr(settings_obj, "scheduler", "fsrs_py")
        monkeypatch.setattr(settings_obj, "scheduler_fsrs_rollout_ratio", 1.0)

        stu = _make_student(db)
        qid = _make_question(db)
        db.commit()
        monkeypatch.setattr(learning_service, "utcnow", lambda: NOW)
        calls = self._spy_scheduler_for(monkeypatch)

        learning_service.record_mistake(db, student_id=stu, question_id=qid)

        assert len(calls) == 1
        assert calls[0]["mode"] == MODE_FSRS_PY, "放量后应命中 py-fsrs 内核"
        # due 由 py-fsrs 内核算出（Again → 重学步 1 天），同样近期重现
        rows = _card_rows(db, stu, qid)
        assert len(rows) == 1
        assert _parse_due(rows[0][1]) == NOW + timedelta(days=1)
        # Again 的新卡状态两内核略有差异（SM-2 → Relearning(3)，
        # py-fsrs → Learning(1)），但都保证 lapses=1 且 +1 天重现
        assert rows[0][2] in (STATE_LEARNING, STATE_RELEARNING)
        assert rows[0][3] == 1  # lapses
