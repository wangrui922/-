# -*- coding: utf-8 -*-
"""编排层 · ``adaptive_plan_service`` 单测（V6.5 Sprint 5 · T07）。

覆盖目标：

    * S1 ``aggregate_recent_results``：窗口边界、按知识点分组、脏数据丢弃、空数据；
    * S2 ``build_prerequisite_gaps`` / ``build_graph_degrees``：真实查库、缓存语义、
      「后置零样本不判缺口」在真实链路上同样成立；
    * S3 ``draw_questions_by_plan``：逐计划逐档抽题、全局去重、少给不凑数；
    * S5 ``build_adaptive_plan``：信封契约、四个回滚开关、降级路径、脏数据不抛异常。

CI 卫生：沿用 T15 的 ``db`` fixture（函数级、跑完 commit），不自建 engine、
不写文件；图谱缓存在每个用例前后清干净，避免跨用例串味。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pytest
from sqlalchemy.orm import Session

from app.engines.config import AdaptiveConfig, DEFAULT_ADAPTIVE_CONFIG
from app.engines.generation import FALLBACK_KNOWLEDGE_POINT_ID
from app.engines.types import ExercisePlan
from app.models.content import Answer, KnowledgePoint, Question
from app.models.dna import StudentKnowledgeMastery
from app.models.knowledge_prerequisite import KnowledgePrerequisite
from app.models.learning import Attempt
from app.models.user import Student, User
from app.services import adaptive_plan_service as aps
from app.services.practice_service import draw_questions_by_plan

#: 时间基准。⚠️ 取**模块导入时**的真实 UTC：``aggregate_recent_results`` 内部用
#: ``models.base.utcnow()`` 算窗口下界，测试必须用同一时刻做减法，否则
#: 「6.9 天前 / 7.1 天前」这类边界断言会随真实时钟漂移。
_NOW: datetime = datetime.now(timezone.utc)


# ============================================================ 造数工具
def _uid() -> str:
    """取一个带随机后缀的短串，规避 UNIQUE 约束（同会话库跨用例共享）。"""
    return uuid.uuid4().hex[:8]


def _make_student(db: Session) -> Student:
    """造一个干净的学生。"""
    user = User(id=uuid.uuid4(), phone=f"13{_uid()}", nickname="编排")
    db.add(user)
    db.flush()
    stu = Student(id=uuid.uuid4(), user_id=user.id, grade=4)
    db.add(stu)
    db.flush()
    return stu


def _make_kp(db: Session, name: str = "知识点") -> KnowledgePoint:
    """造一个知识点（``code`` 有 UNIQUE 约束，必须带随机后缀）。"""
    kp = KnowledgePoint(
        id=uuid.uuid4(),
        name=name,
        code=f"T07_{name}_{_uid()}",
        sort=0,
    )
    db.add(kp)
    db.flush()
    return kp


def _make_question(
    db: Session,
    *,
    kp_id: Optional[uuid.UUID],
    difficulty: int = 3,
    grade: int = 4,
) -> Question:
    """造一道**可抽**的题（L1 安全池四件套齐全，否则抽题层直接落空）。"""
    question = Question(
        id=uuid.uuid4(),
        content=f"1+{_uid()} = ?",
        kp_id=kp_id,
        subject="数学",
        grade=grade,
        difficulty=difficulty,
        type_="选择",
        options={"A": "4", "B": "3", "C": "2", "D": "1"},
        kp_verified=True,
        status="active",
    )
    db.add(question)
    db.flush()
    db.add(Answer(question_id=question.id, correct_answer="A", analysis="凑十法"))
    db.flush()
    return question


def _make_attempt(
    db: Session,
    *,
    student_id: uuid.UUID,
    question_id: uuid.UUID,
    is_correct: bool,
    days_ago: float = 0.0,
) -> Attempt:
    """造一条作答流水（``created_at`` 显式指定，便于控制窗口边界）。"""
    attempt = Attempt(
        id=uuid.uuid4(),
        student_id=student_id,
        question_id=question_id,
        is_correct=is_correct,
        created_at=_NOW - timedelta(days=days_ago),
    )
    db.add(attempt)
    db.flush()
    return attempt


def _make_mastery(
    db: Session,
    *,
    student_id: uuid.UUID,
    kp_id: uuid.UUID,
    mastery: float = 0.3,
    confidence: float = 0.5,
    practice_count: int = 5,
    correct_count: int = 2,
    days_ago: float = 3.0,
) -> StudentKnowledgeMastery:
    """造一条已落库的掌握度汇总行（家长端看板同一口径）。"""
    row = StudentKnowledgeMastery(
        id=uuid.uuid4(),
        student_id=student_id,
        knowledge_point_id=kp_id,
        mastery=mastery,
        confidence=confidence,
        practice_count=practice_count,
        correct_count=correct_count,
        wrong_count=max(0, practice_count - correct_count),
        last_practice_at=_NOW - timedelta(days=days_ago),
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture(autouse=True)
def _clean_graph_cache():
    """每个用例前后清空图谱进程内缓存（它与 DB 无关，必须手动隔离）。"""
    aps.clear_graph_cache()
    yield
    aps.clear_graph_cache()


# ============================================================ S1 results 聚合
def test_aggregate_recent_results_groups_by_knowledge_point(db):
    """按知识点分组统计近窗口的对错次数。"""
    stu = _make_student(db)
    kp_a, kp_b = _make_kp(db, "A"), _make_kp(db, "B")
    qa = _make_question(db, kp_id=kp_a.id)
    qb = _make_question(db, kp_id=kp_b.id)

    _make_attempt(db, student_id=stu.id, question_id=qa.id, is_correct=True)
    _make_attempt(db, student_id=stu.id, question_id=qa.id, is_correct=False)
    _make_attempt(db, student_id=stu.id, question_id=qb.id, is_correct=True)

    results = aps.aggregate_recent_results(db, student_id=stu.id, window_days=7)
    assert set(results[str(kp_a.id)]) == {
        "recent_attempts",
        "recent_correct",
        "last_result_at",
    }
    assert results[str(kp_a.id)]["recent_attempts"] == 2
    assert results[str(kp_a.id)]["recent_correct"] == 1
    assert results[str(kp_b.id)]["recent_attempts"] == 1
    assert results[str(kp_b.id)]["recent_correct"] == 1


def test_aggregate_recent_results_window_boundary(db):
    """窗口边界：``>= now - 7d`` 计入，早于它的不计入（闭左端）。"""
    stu = _make_student(db)
    kp = _make_kp(db, "窗口")
    inside = _make_question(db, kp_id=kp.id)
    outside = _make_question(db, kp_id=kp.id)

    _make_attempt(
        db, student_id=stu.id, question_id=inside.id, is_correct=True, days_ago=6.9
    )
    _make_attempt(
        db, student_id=stu.id, question_id=outside.id, is_correct=True, days_ago=7.1
    )

    results = aps.aggregate_recent_results(db, student_id=stu.id, window_days=7)
    assert results[str(kp.id)]["recent_attempts"] == 1

    # 放大窗口后两条都进来
    results = aps.aggregate_recent_results(db, student_id=stu.id, window_days=30)
    assert results[str(kp.id)]["recent_attempts"] == 2


def test_aggregate_recent_results_drops_unattributable_questions(db):
    """``kp_id`` 为空的题（脏数据）不进聚合。"""
    stu = _make_student(db)
    dirty = _make_question(db, kp_id=None)
    _make_attempt(db, student_id=stu.id, question_id=dirty.id, is_correct=True)
    assert aps.aggregate_recent_results(db, student_id=stu.id, window_days=7) == {}


def test_aggregate_recent_results_empty_and_dirty_inputs(db):
    """无数据 / 脏窗口参数：返回空字典，不抛异常。"""
    stu = _make_student(db)
    assert aps.aggregate_recent_results(db, student_id=stu.id, window_days=7) == {}
    assert aps.aggregate_recent_results(db, student_id=stu.id, window_days=-5) == {}
    assert aps.aggregate_recent_results(db, student_id=stu.id, window_days="脏值") == {}


# ============================================================ S2 图谱取数
def test_build_graph_degrees_queries_real_db(db):
    """出度 = 该知识点被多少后置依赖；首次调用**真实查库**。"""
    a, b, c = _make_kp(db, "出度A"), _make_kp(db, "出度B"), _make_kp(db, "出度C")
    db.add(KnowledgePrerequisite(id=uuid.uuid4(), pre_kp_id=a.id, kp_id=b.id, weight=1.0))
    db.add(KnowledgePrerequisite(id=uuid.uuid4(), pre_kp_id=a.id, kp_id=c.id, weight=0.6))
    db.add(KnowledgePrerequisite(id=uuid.uuid4(), pre_kp_id=b.id, kp_id=c.id, weight=0.3))
    db.flush()

    degrees = aps.build_graph_degrees(db, use_cache=False)
    assert degrees[str(a.id)] == 2
    assert degrees[str(b.id)] == 1
    assert str(c.id) not in degrees


def test_build_graph_degrees_cache_hit_then_clear(db):
    """缓存生效：第二次走缓存；``clear_graph_cache`` 后再查仍是同一结果。"""
    a, b = _make_kp(db, "缓存A"), _make_kp(db, "缓存B")
    db.add(KnowledgePrerequisite(id=uuid.uuid4(), pre_kp_id=a.id, kp_id=b.id, weight=1.0))
    db.flush()

    first = aps.build_graph_degrees(db)
    assert first[str(a.id)] == 1
    # 缓存期内再取（关掉 use_cache 之外）应命中同一份
    second = aps.build_graph_degrees(db)
    assert second == first

    aps.clear_graph_cache()
    third = aps.build_graph_degrees(db, use_cache=False)
    assert third == first


def test_build_prerequisite_gaps_end_to_end(db):
    """真实链路：后置有样本 + 前置未达标 → 判缺口，名称被补齐。"""
    stu = _make_student(db)
    pre, target = _make_kp(db, "前置"), _make_kp(db, "后置")
    db.add(
        KnowledgePrerequisite(
            id=uuid.uuid4(), pre_kp_id=pre.id, kp_id=target.id, weight=0.9
        )
    )
    db.flush()
    # 后置练过 5 次；前置练过但只有 0.2（< 0.5 缺口线）
    _make_mastery(db, student_id=stu.id, kp_id=target.id, mastery=0.8, practice_count=5)
    _make_mastery(db, student_id=stu.id, kp_id=pre.id, mastery=0.2, practice_count=4)

    dna = _load_dna(db, stu.id)
    items = _mastery_rows(dna)
    gaps = aps.build_prerequisite_gaps(db, student_id=stu.id, items=items)

    assert len(gaps) == 1
    assert gaps[0].kp_id == str(target.id)
    assert gaps[0].pre_kp_id == str(pre.id)
    assert gaps[0].missing is False
    assert gaps[0].kp_name == "后置"
    assert gaps[0].pre_kp_name == "前置"


def test_build_prerequisite_gaps_skips_zero_sample_target(db):
    """**关键语义**：后置零样本（``practice_count=0``）不判缺口。

    T07 的掌握度行里 ``attempts`` 取 ``practice_count``，因此「还没练过」的
    知识点即使前置再差也不会被标红——这是 ``detect_prerequisite_gaps`` 的
    设计意图，真实链路上也必须成立。
    """
    stu = _make_student(db)
    pre, target = _make_kp(db, "零样本前置"), _make_kp(db, "零样本后置")
    db.add(
        KnowledgePrerequisite(
            id=uuid.uuid4(), pre_kp_id=pre.id, kp_id=target.id, weight=1.0
        )
    )
    db.flush()
    # 后置 practice_count=0（零样本），前置也很差
    _make_mastery(db, student_id=stu.id, kp_id=target.id, mastery=0.1, practice_count=0)
    _make_mastery(db, student_id=stu.id, kp_id=pre.id, mastery=0.1, practice_count=3)

    items = _mastery_rows(_load_dna(db, stu.id))
    assert aps.build_prerequisite_gaps(db, student_id=stu.id, items=items) == []


def test_build_prerequisite_gaps_empty_inputs(db):
    """无掌握度行 / 无依赖边 → 空列表，不抛异常。"""
    stu = _make_student(db)
    assert aps.build_prerequisite_gaps(db, student_id=stu.id, items=[]) == []
    assert aps.build_prerequisite_gaps(db, student_id=stu.id, items=None) == []


# ============================================================ S3 按计划抽题
def test_draw_questions_by_plan_respects_quota_and_dedupes(db):
    """逐计划抽题：题量不超额、跨计划跨档位全局去重。"""
    stu = _make_student(db)
    kp = _make_kp(db, "抽题KP")
    for difficulty in (1, 3, 5):
        _make_question(db, kp_id=kp.id, difficulty=difficulty)
        _make_question(db, kp_id=kp.id, difficulty=difficulty)

    plans = [
        ExercisePlan(
            knowledge_point_id=str(kp.id),
            question_count=3,
            difficulty_levels=(1, 3),
            strategy="new_first",
        ),
        ExercisePlan(
            knowledge_point_id=str(kp.id),
            question_count=2,
            difficulty_levels=(5,),
            strategy="new_first",
        ),
    ]
    drawn = draw_questions_by_plan(db, student_id=stu.id, plans=plans)

    assert drawn["total"] == 5
    assert drawn["actual_count"] == 5
    assert drawn["miss_count"] == 0
    assert len(set(drawn["question_ids"])) == 5
    assert drawn["relaxation_steps"]


def test_draw_questions_by_plan_short_without_padding(db):
    """少给不凑数：池子不够时如实报 ``miss_count``，不拿别的知识点顶数。"""
    stu = _make_student(db)
    kp = _make_kp(db, "稀缺KP")
    _make_question(db, kp_id=kp.id, difficulty=3)

    plans = [
        ExercisePlan(
            knowledge_point_id=str(kp.id),
            question_count=5,
            difficulty_levels=(3,),
            strategy="balanced",
        )
    ]
    drawn = draw_questions_by_plan(db, student_id=stu.id, plans=plans)
    assert drawn["total"] == 5
    assert drawn["actual_count"] == 1
    assert drawn["miss_count"] == 4


def test_draw_questions_by_plan_diagnostic_fallback_uses_mixed_pool(db):
    """``diagnostic_fallback``：``kp_id`` 置空走综合题池。"""
    stu = _make_student(db)
    for _ in range(3):
        _make_question(db, kp_id=None)  # 三道不可归因的题
    plans = [
        ExercisePlan(
            knowledge_point_id=FALLBACK_KNOWLEDGE_POINT_ID,
            question_count=2,
            difficulty_levels=(3,),
            strategy="diagnostic_fallback",
        )
    ]
    drawn = draw_questions_by_plan(db, student_id=stu.id, plans=plans)
    assert drawn["actual_count"] == 2
    assert drawn["miss_count"] == 0


def test_draw_questions_by_plan_empty_and_dirty(db):
    """空计划 / 脏计划：返回空结果，不抛异常。"""
    stu = _make_student(db)
    assert draw_questions_by_plan(db, student_id=stu.id, plans=[])["actual_count"] == 0
    assert draw_questions_by_plan(db, student_id=stu.id, plans=None)["total"] == 0
    # 非法知识点 ID 的计划：按综合题池处理，不该炸（题库是会话级共享的，
    # 综合池里可能有别的用例造的题，所以只断言口径自洽，不断言具体题数）
    dirty = draw_questions_by_plan(
        db,
        student_id=stu.id,
        plans=[ExercisePlan(knowledge_point_id="not-a-uuid", question_count=1)],
    )
    assert dirty["total"] == 1
    assert dirty["actual_count"] + dirty["miss_count"] == 1
    # 零题量的计划不参与统计
    zero = draw_questions_by_plan(
        db,
        student_id=stu.id,
        plans=[ExercisePlan(knowledge_point_id="not-a-uuid", question_count=0)],
    )
    assert zero == {
        "question_ids": [],
        "relaxation_steps": [],
        "total": 0,
        "actual_count": 0,
        "miss_count": 0,
    }


# ============================================================ S5 编排主入口
def _load_dna(db: Session, student_id: uuid.UUID) -> Dict[str, Any]:
    """取一次学生 DNA（编排层用的就是这个口径）。"""
    from app.services import student_dna_service

    return student_dna_service.get_learning_dna(db, student_id=student_id)


def _mastery_rows(dna: Dict[str, Any]) -> List[Dict[str, Any]]:
    """走 ``types.mastery_rows`` 官方适配器拆信封。"""
    from app.engines.types import mastery_rows

    return mastery_rows(dna)


def _seed_full_scene(db: Session) -> Tuple[Student, List[KnowledgePoint]]:
    """造一个「有掌握度 + 有图谱 + 有题库 + 有流水」的完整场景。"""
    stu = _make_student(db)
    pre, target = _make_kp(db, "分数比较"), _make_kp(db, "分数加减法")
    db.add(
        KnowledgePrerequisite(
            id=uuid.uuid4(), pre_kp_id=pre.id, kp_id=target.id, weight=0.9
        )
    )
    db.flush()

    _make_mastery(
        db,
        student_id=stu.id,
        kp_id=target.id,
        mastery=0.75,
        confidence=0.8,
        practice_count=6,
        correct_count=4,
        days_ago=5.0,
    )
    _make_mastery(
        db,
        student_id=stu.id,
        kp_id=pre.id,
        mastery=0.2,
        confidence=0.6,
        practice_count=4,
        correct_count=1,
        days_ago=1.0,
    )

    for kp in (pre, target):
        for difficulty in (1, 3, 5):
            question = _make_question(db, kp_id=kp.id, difficulty=difficulty)
            _make_attempt(
                db,
                student_id=stu.id,
                question_id=question.id,
                is_correct=(kp is pre),
                days_ago=1.0,
            )
    return stu, [pre, target]


def test_build_adaptive_plan_contract(db):
    """信封契约：八键齐全、camelCase 之前的 snake_case 字段名与施工图一致。"""
    stu, _kps = _seed_full_scene(db)
    plan = aps.build_adaptive_plan(db, student_id=stu.id, count=6)

    assert plan["engine_version"] == aps.ENGINE_VERSION
    assert plan["total"] == 6
    assert plan["actual_count"] <= 6
    assert plan["miss_count"] == plan["total"] - plan["actual_count"]
    assert len(set(plan["question_ids"])) == plan["actual_count"]
    assert plan["generated_at"]
    assert isinstance(plan["relaxation_steps"], list)
    assert plan["items"]
    for item in plan["items"]:
        assert set(item.keys()) == {
            "knowledge_point_id",
            "kp_name",
            "question_count",
            "difficulty_levels",
            "strategy",
            "reason",
        }
        assert item["question_count"] >= 1
        assert item["difficulty_levels"]
        assert item["reason"]


def test_build_adaptive_plan_prefers_prerequisite_gap(db):
    """教学效果：未掌握的前置知识点被排进今日计划（缺口优先）。"""
    stu, kps = _seed_full_scene(db)
    plan = aps.build_adaptive_plan(db, student_id=stu.id, count=4)
    planned_ids = {item["knowledge_point_id"] for item in plan["items"]}
    assert str(kps[0].id) in planned_ids  # pre（掌握度 0.2 且是缺口）


def test_build_adaptive_plan_with_no_data_falls_back_gracefully(db):
    """新学生（无任何掌握度行）：走综合摸底兜底计划，不抛异常。"""
    stu = _make_student(db)
    for _ in range(3):
        _make_question(db, kp_id=None)

    plan = aps.build_adaptive_plan(db, student_id=stu.id, count=3)
    assert plan["engine_version"] == aps.ENGINE_VERSION
    assert plan["items"]  # 引擎保证计划非空（diagnostic_fallback）


def test_build_adaptive_plan_clamps_count(db):
    """题数被夹到 ``[1, generation_max_total]``。"""
    stu, _kps = _seed_full_scene(db)
    assert aps.build_adaptive_plan(db, student_id=stu.id, count=999)["total"] == 20
    assert aps.build_adaptive_plan(db, student_id=stu.id, count=-3)["total"] == 1
    assert aps.build_adaptive_plan(db, student_id=stu.id, count="脏值")["total"] == 10
    assert aps.build_adaptive_plan(db, student_id=stu.id)["total"] == 10


# ============================================================ 四个回滚开关
def test_rollback_adaptive_disabled_short_circuits(db):
    """开关① ``adaptive.enabled=false``：整条链路短路到普通抽题。"""
    stu, _kps = _seed_full_scene(db)
    plan = aps.build_adaptive_plan(
        db, student_id=stu.id, count=5, config=_cfg(enabled=False)
    )
    assert plan["engine_version"] == aps.ENGINE_VERSION_LEGACY
    assert plan["items"] == []
    assert plan["total"] == 5
    assert plan["actual_count"] > 0


def test_rollback_results_none(db):
    """开关② ``results`` 不参与：注入空聚合器后仍出计划，且不抛异常。"""
    stu, _kps = _seed_full_scene(db)
    plan = aps.build_adaptive_plan(
        db,
        student_id=stu.id,
        count=5,
        config=_cfg(priority_recent_window=0),  # 窗口 0 → 聚合不到任何流水
    )
    assert plan["engine_version"] == aps.ENGINE_VERSION
    assert plan["items"]


def test_rollback_wrong_boost_zero(db):
    """开关③ ``wrong_boost=0``：只保留「取消近期降权」，计划仍正常产出。"""
    stu, _kps = _seed_full_scene(db)
    boosted = aps.build_adaptive_plan(db, student_id=stu.id, count=5)
    flat = aps.build_adaptive_plan(
        db, student_id=stu.id, count=5, config=_cfg(priority_wrong_boost=0)
    )
    assert boosted["engine_version"] == flat["engine_version"] == aps.ENGINE_VERSION
    assert flat["items"]


def test_rollback_saturation_penalty_one(db):
    """开关④ ``saturation_penalty=1.0``：关闭单点刷量抑制，计划仍正常产出。"""
    stu, _kps = _seed_full_scene(db)
    plan = aps.build_adaptive_plan(
        db,
        student_id=stu.id,
        count=5,
        config=_cfg(priority_saturation_penalty=1.0),
    )
    assert plan["engine_version"] == aps.ENGINE_VERSION
    assert plan["items"]


def _cfg(**overrides: Any) -> AdaptiveConfig:
    """在缺省配置上覆盖若干阈值。"""
    return DEFAULT_ADAPTIVE_CONFIG.replace(**overrides)


def test_build_adaptive_plan_degrades_when_upstream_blows_up(db, monkeypatch):
    """编排链路任一步炸了 → 降级到普通抽题，绝不让孩子白屏。"""
    stu, _kps = _seed_full_scene(db)

    def _boom(*_args: Any, **_kwargs: Any):
        raise RuntimeError("模拟编排层炸了")

    monkeypatch.setattr(
        "app.services.student_dna_service.get_learning_dna", _boom
    )
    plan = aps.build_adaptive_plan(db, student_id=stu.id, count=4)
    assert plan["engine_version"] == aps.ENGINE_VERSION_LEGACY
    assert plan["items"] == []
    assert plan["actual_count"] > 0


def test_build_adaptive_plan_read_only(db):
    """``GET`` 语义：编排不落任何 ``LearningTask`` 会话（只读、无副作用）。"""
    from sqlalchemy import func, select

    from app.models.learning import LearningTask

    stu, _kps = _seed_full_scene(db)
    before = db.execute(select(func.count()).select_from(LearningTask)).scalar_one()
    aps.build_adaptive_plan(db, student_id=stu.id, count=5)
    after = db.execute(select(func.count()).select_from(LearningTask)).scalar_one()
    assert before == after
