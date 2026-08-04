"""T04 · QA 独立验收测试（由 QA「严过关」补充，不依赖工程师自测结论）。

补测动机：
    工程师自测集中在「顺风路径」——例如 ``test_solve_invalidates_mastery_cache``
    的注释明确写着「故意不造 attempts」，恰好绕开了**真实主场景**
    （学生先作答、再建错题、最后攻克）。本文件专门补这些被绕开的分支。

覆盖清单：
    1. 缓存联动：有 attempts 前置时，solve 后 mode 是否真的回到 fresh；
    2. 错因仲裁：同一段文本同时命中 CALC + CONCEPT 时是否取 CONCEPT；
    3. 推荐排序：低分 + 错因集中的知识点是否排在前面；
    4. 空数据契约：全新学生三接口的字段骨架是否齐全且不报错；
    5. 鉴权：无 token / 非学生角色；
    6. 参数边界：range_days / limit / force 越界的兜底口径；
    7. force=1 是否真的重算（computed_at 前进）；
    8. 证据上限：sample_hits 是否恒 <= 5。
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from app.core.constants import (
    ERROR_CALC,
    ERROR_CATEGORIES,
    ERROR_EVIDENCE_LIMIT,
    MODE_CACHE,
    MODE_FRESH,
)
from app.core.error_categories import ERROR_KNOWLEDGE
from app.services.mastery_engine import classify_error

from t04_helpers import (
    assert_ok,
    find_pattern,
    load_mastery_rows,
    login,
    make_question,
    patch_ark,
    seed_attempts,
    seed_kp_and_questions,
    seed_mistake,
)

MASTERY_URL = "/api/profile/mastery"
PATTERN_URL = "/api/profile/error-patterns"
RECOMMEND_URL = "/api/profile/recommendations"


def _solve_url(mistake_id: str) -> str:
    return f"/api/mistake/{mistake_id}/solve"


def _mastery(client, headers: Dict[str, str], query: str = "") -> Dict[str, Any]:
    return assert_ok(client.get(f"{MASTERY_URL}{query}", headers=headers))


# ==================================================== 1. 缓存联动（真实主场景）
def test_qa_solve_after_attempts_returns_fresh_mode(client, monkeypatch, new_phone):
    """真实主场景：先作答→再建错题→攻克，攻克后掌握度必须回到 ``fresh``。

    工程师的同类用例故意不造 attempts，绕开了这条路径。这里补上：
    只要 ``student_mastery.last_practiced_at`` 被推到 ``computed_at`` 之后，
    下一次读取就必须是实算，而不是继续吃旧缓存。
    """
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _kp_id, questions = seed_kp_and_questions("先作答再攻克点", question_count=1)

    # 真实顺序：学生答错过 -> 建了错题 -> 之后攻克
    seed_attempts(student_id, questions[0], [False, False])
    mistake_id = seed_mistake(student_id, questions[0], error_type="计算错误")

    first = _mastery(client, headers)
    assert first["mode"] == MODE_FRESH, first
    # 第二次应命中缓存，确认缓存确实建立了
    assert _mastery(client, headers)["mode"] == MODE_CACHE

    assert_ok(client.put(_solve_url(mistake_id), headers=headers))

    after = _mastery(client, headers)
    assert after["mode"] == MODE_FRESH, f"攻克后缓存未失效，仍在吃旧缓存: {after}"


def test_qa_solve_pushes_last_practiced_after_computed_at(client, monkeypatch, new_phone):
    """物化表层面核对：solve 必须把 ``last_practiced_at`` 推到 ``computed_at`` 之后。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _kp_id, questions = seed_kp_and_questions("脏标记核对点", question_count=1)
    seed_attempts(student_id, questions[0], [False])
    mistake_id = seed_mistake(student_id, questions[0], error_type="计算错误")

    _mastery(client, headers)  # 物化一次
    rows_before = load_mastery_rows(student_id)
    assert rows_before, "掌握度应已落库"

    assert_ok(client.put(_solve_url(mistake_id), headers=headers))

    rows_after = load_mastery_rows(student_id)
    dirty = [
        row
        for row in rows_after
        if row.last_practiced_at is not None
        and row.computed_at is not None
        and row.last_practiced_at > row.computed_at
    ]
    assert dirty, f"solve 后应至少有一行被标脏: {[(r.last_practiced_at, r.computed_at) for r in rows_after]}"


# ==================================================== 2. 错因优先级仲裁
def test_qa_classify_concept_beats_calc_pure():
    """纯算法层：同时命中 CALC 与 KNOWLEDGE 时取 KNOWLEDGE。"""
    assert classify_error("计算错误 概念不清导致算错") == ERROR_KNOWLEDGE
    assert classify_error("算错了，其实是公式记错") == ERROR_KNOWLEDGE
    assert classify_error("单纯进位算错") == ERROR_CALC


def test_qa_error_pattern_priority_end_to_end(client, monkeypatch, new_phone):
    """端到端：错题文本同时命中 CALC + KNOWLEDGE，接口应把它计到 KNOWLEDGE。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _kp_id, questions = seed_kp_and_questions("仲裁点", question_count=1)
    # error_type 命中 CALC（"计算"），error_reason 命中 KNOWLEDGE（"概念"）
    seed_mistake(
        student_id,
        questions[0],
        error_type="计算错误",
        error_reason="概念不清，公式记错了",
    )

    data = assert_ok(client.get(PATTERN_URL, headers=headers))
    patterns = data["error_patterns"]

    assert find_pattern(patterns, ERROR_KNOWLEDGE)["count"] == 1, patterns
    assert find_pattern(patterns, ERROR_CALC)["count"] == 0, patterns
    # 排在第一位的应是 KNOWLEDGE（count 最高）
    assert patterns[0]["category"] == ERROR_KNOWLEDGE, patterns


# ==================================================== 3. 推荐排序
def test_qa_recommendation_ranks_weakest_first(client, monkeypatch, new_phone):
    """低分 + 高错误率的知识点必须排在高分知识点前面，且带中文 reason。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    weak_kp, weak_qs = seed_kp_and_questions("极弱点", question_count=1)
    strong_kp, strong_qs = seed_kp_and_questions("较强点", question_count=1)

    seed_attempts(student_id, weak_qs[0], [False, False, False, False])
    seed_attempts(student_id, strong_qs[0], [True, True, True, True])

    data = assert_ok(client.get(RECOMMEND_URL, headers=headers))
    items = data["recommendations"]
    assert items, data

    order = [item["kp_id"] for item in items]
    assert order[0] == weak_kp, f"最弱知识点应排第一: {items}"
    if strong_kp in order:
        assert order.index(weak_kp) < order.index(strong_kp), items

    top = items[0]
    assert top["reason"], "reason 必须由后端生成"
    assert top["kp_name"] in top["reason"], top
    assert 0.0 <= top["priority"] <= 1.0, top
    # 契约：snake_case 字段齐全
    for field in (
        "kp_id",
        "kp_name",
        "reason",
        "priority",
        "score",
        "error_rate",
        "top_error_category",
        "suggested_question_ids",
    ):
        assert field in top, (field, top)


# ==================================================== 4. 空数据契约
def test_qa_empty_student_all_three_endpoints(client, monkeypatch, new_phone):
    """全新学生：三接口都要返回齐全骨架，且 code=0、不抛 500。"""
    patch_ark(monkeypatch)
    headers, _student_id = login(client, new_phone())

    mastery = _mastery(client, headers)
    assert mastery["mastery"] == [], mastery
    assert mastery["summary"]["total_kp"] == 0, mastery
    assert mastery["summary"]["level_count"] == {"0": 0, "1": 0, "2": 0, "3": 0}, mastery

    patterns = assert_ok(client.get(PATTERN_URL, headers=headers))
    assert len(patterns["error_patterns"]) == 5, patterns
    assert {item["category"] for item in patterns["error_patterns"]} == set(ERROR_CATEGORIES)
    assert all(item["count"] == 0 for item in patterns["error_patterns"]), patterns
    assert all(item["ratio"] == 0.0 for item in patterns["error_patterns"]), patterns
    assert patterns["total_mistakes"] == 0, patterns
    assert patterns["unmapped_mistakes"] == 0, patterns

    recommends = assert_ok(client.get(RECOMMEND_URL, headers=headers))
    assert recommends["recommendations"] == [], recommends


# ==================================================== 5. 鉴权
def test_qa_all_endpoints_require_auth(client, monkeypatch):
    """无 token：三接口一律 401。"""
    patch_ark(monkeypatch)
    for url in (MASTERY_URL, PATTERN_URL, RECOMMEND_URL):
        resp = client.get(url)
        assert resp.status_code == 401, (url, resp.status_code, resp.text)


def test_qa_non_student_role_forbidden(client, monkeypatch, make_token):
    """教师 / 家长角色：三接口一律 403（画像是学生私有资源）。"""
    patch_ark(monkeypatch)
    for role in ("teacher", "parent"):
        headers = {"Authorization": f"Bearer {make_token(role=role, sid=None)}"}
        for url in (MASTERY_URL, PATTERN_URL, RECOMMEND_URL):
            resp = client.get(url, headers=headers)
            assert resp.status_code == 403, (role, url, resp.status_code, resp.text)


def test_qa_solve_cross_student_is_404_not_403(client, monkeypatch, new_phone):
    """越权攻克他人错题：必须 404（不泄露资源存在性），而不是 403。"""
    patch_ark(monkeypatch)
    _owner_headers, owner_id = login(client, new_phone())
    attacker_headers, _attacker_id = login(client, new_phone())

    _kp, questions = seed_kp_and_questions("越权点", question_count=1)
    mistake_id = seed_mistake(owner_id, questions[0])

    resp = client.put(_solve_url(mistake_id), headers=attacker_headers)
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == 404, resp.text


# ==================================================== 6. 参数边界
def test_qa_range_days_out_of_bounds_rejected(client, monkeypatch, new_phone):
    """range_days 越界应被参数校验拦下（项目统一归一为 400）。"""
    patch_ark(monkeypatch)
    headers, _sid = login(client, new_phone())

    for bad in (0, -1, 3651, 99999):
        resp = client.get(f"{PATTERN_URL}?range_days={bad}", headers=headers)
        assert resp.status_code == 400, (bad, resp.status_code, resp.text)

    for good in (1, 90, 3650):
        data = assert_ok(client.get(f"{PATTERN_URL}?range_days={good}", headers=headers))
        assert data["range_days"] == good, (good, data)


def test_qa_limit_and_force_out_of_bounds_rejected(client, monkeypatch, new_phone):
    """limit / force 越界同样被拦下，合法值正常放行。"""
    patch_ark(monkeypatch)
    headers, _sid = login(client, new_phone())

    for bad in (0, -3, 21, 1000):
        resp = client.get(f"{RECOMMEND_URL}?limit={bad}", headers=headers)
        assert resp.status_code == 400, (bad, resp.status_code, resp.text)

    for bad in (2, -1, 99):
        resp = client.get(f"{MASTERY_URL}?force={bad}", headers=headers)
        assert resp.status_code == 400, (bad, resp.status_code, resp.text)

    assert_ok(client.get(f"{RECOMMEND_URL}?limit=1", headers=headers))
    assert_ok(client.get(f"{RECOMMEND_URL}?limit=20", headers=headers))


def test_qa_recommend_limit_truncates(client, monkeypatch, new_phone):
    """limit=2 时最多返回 2 条。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    for index in range(4):
        _kp, questions = seed_kp_and_questions(f"限流点{index}", question_count=1)
        seed_attempts(student_id, questions[0], [False, False])

    data = assert_ok(client.get(f"{RECOMMEND_URL}?limit=2", headers=headers))
    assert len(data["recommendations"]) == 2, data


# ==================================================== 7. force 真重算
def test_qa_force_really_recomputes(client, monkeypatch, new_phone):
    """``?force=1`` 必须绕过缓存实算，并把 computed_at 往前推。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _kp, questions = seed_kp_and_questions("强制重算点", question_count=1)
    seed_attempts(student_id, questions[0], [True, False])

    first = _mastery(client, headers)
    assert first["mode"] == MODE_FRESH, first
    assert _mastery(client, headers)["mode"] == MODE_CACHE

    forced = _mastery(client, headers, "?force=1")
    assert forced["mode"] == MODE_FRESH, forced
    assert forced["computed_at"] >= first["computed_at"], (first, forced)

    # 重算后应重新落库且能再次命中缓存
    assert _mastery(client, headers)["mode"] == MODE_CACHE
    assert load_mastery_rows(student_id), "force 重算后应有物化行"


# ==================================================== 8. 证据上限
def test_qa_sample_hits_capped_at_five(client, monkeypatch, new_phone):
    """每类错因的 sample_hits 最多 5 条（ERROR_EVIDENCE_LIMIT）。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _kp, questions = seed_kp_and_questions("证据上限点", question_count=1)
    for index in range(9):
        seed_mistake(
            student_id,
            questions[0],
            error_type="计算错误",
            error_reason=f"第{index}次进位算错",
        )

    data = assert_ok(client.get(PATTERN_URL, headers=headers))
    calc = find_pattern(data["error_patterns"], ERROR_CALC)

    assert calc["count"] == 9, calc
    assert len(calc["sample_hits"]) <= ERROR_EVIDENCE_LIMIT, calc
    assert data["total_mistakes"] == 9, data


# ==================================================== 9. TTL 过期
def test_qa_cache_expires_after_ttl(client, monkeypatch, new_phone):
    """把 ``computed_at`` 手工回拨到 TTL 之外，下次读取必须重新实算。

    工程师的缓存用例只覆盖了「TTL 内命中」与「被新练习标脏」，
    没有覆盖**纯 TTL 过期**这一条，这里补上。
    """
    import uuid as _uuid
    from datetime import timedelta

    from sqlalchemy import select

    from app.core.constants import MASTERY_CACHE_TTL_SECONDS
    from app.db import SessionLocal
    from app.models.base import utcnow
    from app.models.student_mastery import StudentMastery

    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _kp, questions = seed_kp_and_questions("TTL过期点", question_count=1)
    seed_attempts(student_id, questions[0], [True, False])

    assert _mastery(client, headers)["mode"] == MODE_FRESH
    assert _mastery(client, headers)["mode"] == MODE_CACHE

    # 把 computed_at 回拨到 TTL 之前（同时保证 last_practiced_at 仍早于它，
    # 这样触发重算的唯一原因就是 TTL 过期本身）
    stale = utcnow() - timedelta(seconds=MASTERY_CACHE_TTL_SECONDS + 60)
    with SessionLocal() as db:
        rows = list(
            db.execute(
                select(StudentMastery).where(
                    StudentMastery.student_id == _uuid.UUID(str(student_id))
                )
            ).scalars()
        )
        assert rows, "应已有物化行"
        for row in rows:
            row.computed_at = stale
            row.last_practiced_at = stale - timedelta(seconds=60)
        db.commit()

    assert _mastery(client, headers)["mode"] == MODE_FRESH, "TTL 过期后应重新实算"


# ================================================ 10. 缺陷1 回归（已修复）
def test_qa_solve_should_count_as_positive_even_with_attempts(client, monkeypatch, new_phone):
    """期望行为：攻克错题应恒计一次正样本，与该题是否已有 attempts 无关。

    历史缺陷：``profile_service._load_mistakes`` 的去重 ``continue`` 连带吞掉了
    solved 正样本，导致「该题已有 attempts 记录」时攻克不影响掌握度。修复方式是
    把正样本附加逻辑前置到去重 ``continue`` 之前（负样本仍保留去重）。
    """
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _kp, questions = seed_kp_and_questions("攻克正样本点", question_count=1)
    seed_attempts(student_id, questions[0], [False, False])
    mistake_id = seed_mistake(student_id, questions[0], error_type="计算错误")

    before = _mastery(client, headers)["mastery"][0]
    assert_ok(client.put(_solve_url(mistake_id), headers=headers))
    after = _mastery(client, headers)["mastery"][0]

    assert after["correct_count"] == before["correct_count"] + 1, (before, after)
    assert after["score"] > before["score"], (before, after)


def test_qa_unmapped_mistake_counted_not_dropped(client, monkeypatch, new_phone):
    """R5：没挂知识点的错题不能凭空消失，要计进 unmapped_mistakes。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    orphan = make_question(kp_id=None, content="无知识点题")
    seed_mistake(student_id, orphan, error_type="审题失误", error_reason="看错条件")

    data = assert_ok(client.get(PATTERN_URL, headers=headers))
    assert data["total_mistakes"] == 1, data
    assert data["unmapped_mistakes"] == 1, data

    # 掌握度里不应出现这条（没有归属知识点）
    mastery = _mastery(client, headers)
    assert mastery["mastery"] == [], mastery
