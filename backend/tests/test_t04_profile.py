"""T04 · T4 学习画像三接口集成测试。

覆盖：
    * 鉴权：401（无令牌）/ 403（非学生角色、未绑定学生）
    * 空态：全新学生 -> 空数组 + 齐全的字段骨架，**不报错**
    * 正常：掌握度 / 错因分布 / 推荐三条链路端到端
    * 缓存：第二次命中 ``mode="cache"``，``?force=1`` 强制回到 ``fresh``
    * 契约：出参恒为 **snake_case**，错因恒 5 类
    * R3/R5：AI 消息兜底证据、未关联知识点的错题单独计数

隔离约定：
    内存 SQLite 是**会话级共享**的，所以每个用例都用 ``new_phone()`` 拿一个
    全新手机号 = 全新学生，避免统计数被别的用例污染。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict

from app.core.constants import (
    CODE_PROFILE_COMPUTE_FAILED,
    ERROR_CALC,
    ERROR_CATEGORIES,
    MODE_CACHE,
    MODE_FRESH,
)
from app.core.error_categories import ERROR_KNOWLEDGE

from t04_helpers import (
    assert_fail,
    assert_ok,
    find_pattern,
    load_mastery_rows,
    login,
    make_ai_messages,
    make_question,
    patch_ark,
    seed_attempts,
    seed_kp_and_questions,
    seed_mistake,
    seed_student_with_mistakes,
)

MASTERY_URL = "/api/profile/mastery"
PATTERN_URL = "/api/profile/error-patterns"
RECOMMEND_URL = "/api/profile/recommendations"


def _mastery(client, headers: Dict[str, str], query: str = "") -> Dict[str, Any]:
    """拉一次掌握度，返回 ``data``。"""
    return assert_ok(client.get(f"{MASTERY_URL}{query}", headers=headers))


# ==================================================================== 鉴权
def test_profile_requires_auth(client, monkeypatch):
    """三个接口都需要登录 -> 401。"""
    patch_ark(monkeypatch)

    assert_fail(client.get(MASTERY_URL), 401)
    assert_fail(client.get(PATTERN_URL), 401)
    assert_fail(client.get(RECOMMEND_URL), 401)


def test_profile_forbidden_for_non_student(client, make_token, monkeypatch):
    """非学生角色（家长/管理员）访问学生画像 -> 403。"""
    patch_ark(monkeypatch)
    headers = {"Authorization": f"Bearer {make_token(sub='1', role='parent', sid=str(uuid.uuid4()))}"}

    for url in (MASTERY_URL, PATTERN_URL, RECOMMEND_URL):
        body = assert_fail(client.get(url, headers=headers), 403)
        assert "学生" in body["msg"], body


def test_profile_forbidden_without_student_binding(client, make_token, monkeypatch):
    """学生角色但没绑定 sid -> 403。"""
    patch_ark(monkeypatch)
    headers = {"Authorization": f"Bearer {make_token(sub='1', role='student', sid=None)}"}

    assert_fail(client.get(MASTERY_URL, headers=headers), 403)


# ==================================================================== 空态
def test_mastery_empty_for_new_student(client, monkeypatch, new_phone):
    """全新学生：空数组 + 字段骨架齐全，不能 500 也不能少字段。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())

    data = _mastery(client, headers)

    assert data["student_id"] == str(student_id), data
    assert data["mastery"] == [], data
    assert data["summary"]["total_kp"] == 0, data
    assert data["summary"]["level_count"] == {"0": 0, "1": 0, "2": 0, "3": 0}, data
    assert data["mode"] == MODE_FRESH, data
    assert data["computed_at"], data


def test_error_patterns_empty_still_returns_five_categories(client, monkeypatch, new_phone):
    """没有任何错题时，错因分布仍返回 5 类（count 全 0），保证环形图完整。"""
    patch_ark(monkeypatch)
    headers, _ = login(client, new_phone())

    data = assert_ok(client.get(PATTERN_URL, headers=headers))

    assert len(data["error_patterns"]) == 5, data
    assert {item["category"] for item in data["error_patterns"]} == set(ERROR_CATEGORIES), data
    assert all(item["count"] == 0 for item in data["error_patterns"]), data
    assert data["total_mistakes"] == 0, data
    assert data["unmapped_mistakes"] == 0, data
    assert data["range_days"] == 90, data
    assert data["mode"] == MODE_FRESH, data


def test_recommendations_empty_for_new_student(client, monkeypatch, new_phone):
    """全新学生无推荐 -> 空数组，不报错。"""
    patch_ark(monkeypatch)
    headers, _ = login(client, new_phone())

    data = assert_ok(client.get(RECOMMEND_URL, headers=headers))

    assert data["recommendations"] == [], data
    assert data["mode"] == MODE_FRESH, data


# ==================================================================== 正常链路
def test_mastery_normal_flow(client, monkeypatch, new_phone):
    """有作答有错题 -> 掌握度条目齐全，字段全 snake_case。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    seeded = seed_student_with_mistakes(student_id, kp_name="一元一次方程", wrong=3, right=1)

    data = _mastery(client, headers)

    assert data["summary"]["total_kp"] == 1, data
    item = data["mastery"][0]

    # ---- 契约字段（snake_case，一个都不能少）--------------------------
    for key in (
        "kp_id",
        "kp_name",
        "score",
        "level",
        "attempts",
        "correct_count",
        "error_rate",
        "effective_attempts",
        "last_practiced_at",
    ):
        assert key in item, (key, item)
    assert not any("_" not in key and key[0].islower() and any(c.isupper() for c in key) for key in item), item

    assert item["kp_id"] == seeded["kp_id"], item
    assert item["kp_name"] == "一元一次方程", item
    assert item["attempts"] == 4, item        # 3 错 + 1 对
    assert item["correct_count"] == 1, item
    assert round(item["error_rate"], 2) == 0.75, item
    assert 0.0 <= item["score"] <= 1.0, item
    assert item["level"] in (0, 1, 2, 3), item
    assert item["last_practiced_at"], item

    # 4 次里错 3 次 -> 应该是薄弱档
    assert item["level"] <= 1, item


def test_mastery_sorted_weakest_first(client, monkeypatch, new_phone):
    """多个知识点时按「等级升序 -> 错误率降序」排，最该补的排最前。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())

    _, weak_qs = seed_kp_and_questions("薄弱点", question_count=1)
    _, strong_qs = seed_kp_and_questions("拿手点", question_count=1)
    seed_attempts(student_id, weak_qs[0], [False] * 6)
    seed_attempts(student_id, strong_qs[0], [True] * 8)

    data = _mastery(client, headers)
    names = [item["kp_name"] for item in data["mastery"]]

    assert names == ["薄弱点", "拿手点"], data
    assert data["mastery"][0]["level"] <= data["mastery"][1]["level"], data
    assert data["summary"]["total_kp"] == 2, data
    assert sum(data["summary"]["level_count"].values()) == 2, data


def test_error_patterns_normal_flow(client, monkeypatch, new_phone):
    """错题错因被正确归类，占比之和为 1，样例文本回带。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _, questions = seed_kp_and_questions("错因统计点", question_count=3)

    seed_mistake(student_id, questions[0], error_type="计算失误", error_reason="进位忘了")
    seed_mistake(student_id, questions[1], error_type="计算失误", error_reason="约分错了")
    seed_mistake(student_id, questions[2], error_type="概念不清", error_reason="定义没记住")

    data = assert_ok(client.get(PATTERN_URL, headers=headers))

    assert data["total_mistakes"] == 3, data
    assert data["unmapped_mistakes"] == 0, data

    calc = find_pattern(data["error_patterns"], ERROR_CALC)
    concept = find_pattern(data["error_patterns"], ERROR_KNOWLEDGE)

    assert calc["count"] == 2, calc
    assert concept["count"] == 1, concept
    assert calc["label"] == "计算错", calc
    assert round(calc["ratio"] + concept["ratio"], 4) == 1.0, data
    assert len(calc["sample_hits"]) == 2, calc
    assert data["error_patterns"][0]["category"] == ERROR_CALC, data  # count 最大排最前


def test_error_patterns_range_days_window(client, monkeypatch, new_phone):
    """``range_days`` 窗口生效：窗口外的老错题不计入。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _, questions = seed_kp_and_questions("窗口测试点", question_count=2)

    seed_mistake(student_id, questions[0], error_type="计算失误", days_ago=1)
    seed_mistake(student_id, questions[1], error_type="概念不清", days_ago=200)

    recent = assert_ok(client.get(f"{PATTERN_URL}?range_days=90", headers=headers))
    all_time = assert_ok(client.get(f"{PATTERN_URL}?range_days=3650", headers=headers))

    assert recent["total_mistakes"] == 1, recent
    assert recent["range_days"] == 90, recent
    assert all_time["total_mistakes"] == 2, all_time
    assert all_time["range_days"] == 3650, all_time


def test_error_patterns_counts_unmapped_mistakes(client, monkeypatch, new_phone):
    """R5：没关联知识点的错题不进掌握度，但要在 unmapped_mistakes 里报出来。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    orphan_question = make_question(kp_id=None, content="没挂知识点的题")
    seed_mistake(student_id, orphan_question, error_type="计算失误")

    patterns = assert_ok(client.get(PATTERN_URL, headers=headers))
    mastery = _mastery(client, headers)

    assert patterns["total_mistakes"] == 1, patterns
    assert patterns["unmapped_mistakes"] == 1, patterns
    assert find_pattern(patterns["error_patterns"], ERROR_CALC)["count"] == 1, patterns
    # 掌握度里查不到它（没有归属知识点，算不了）
    assert mastery["mastery"] == [], mastery


def test_error_patterns_fallback_to_ai_message(client, monkeypatch, new_phone):
    """R3 兜底：错题没写错因时，取该题 AI 会话里学生的最后一句当证据。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _, questions = seed_kp_and_questions("兜底证据点", question_count=1)

    make_ai_messages(
        student_id,
        questions[0],
        ["这题看不懂", "我是概念不清，定义没记住"],
        role="user",
    )
    seed_mistake(student_id, questions[0], error_type=None, error_reason=None)

    data = assert_ok(client.get(PATTERN_URL, headers=headers))
    concept = find_pattern(data["error_patterns"], ERROR_KNOWLEDGE)

    assert data["total_mistakes"] == 1, data
    assert concept["count"] == 1, data
    assert concept["sample_hits"] == ["我是概念不清，定义没记住"], concept


def test_recommendations_normal_flow(client, monkeypatch, new_phone):
    """推荐链路：给出理由、优先级、可练题目 ID。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    kp_id, questions = seed_kp_and_questions("推荐测试点", question_count=2)
    seed_attempts(student_id, questions[0], [False] * 5)
    seed_mistake(student_id, questions[0], error_type="概念不清", error_reason="定义没记住")

    data = assert_ok(client.get(RECOMMEND_URL, headers=headers))

    assert len(data["recommendations"]) == 1, data
    item = data["recommendations"][0]

    for key in (
        "kp_id",
        "kp_name",
        "reason",
        "priority",
        "score",
        "error_rate",
        "top_error_category",
        "suggested_question_ids",
    ):
        assert key in item, (key, item)

    assert item["kp_id"] == kp_id, item
    assert item["kp_name"] == "推荐测试点", item
    assert "推荐测试点" in item["reason"], item
    assert 0.0 < item["priority"] <= 1.0, item
    assert item["top_error_category"] == ERROR_KNOWLEDGE, item
    assert set(item["suggested_question_ids"]) <= set(questions), item


def test_recommendations_respects_limit(client, monkeypatch, new_phone):
    """``limit`` 生效。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    for index in range(4):
        _, questions = seed_kp_and_questions(f"限流点{index}", question_count=1)
        seed_attempts(student_id, questions[0], [False, False])

    data = assert_ok(client.get(f"{RECOMMEND_URL}?limit=2", headers=headers))

    assert len(data["recommendations"]) == 2, data
    assert data["recommendations"][0]["priority"] >= data["recommendations"][1]["priority"], data


# ==================================================================== 缓存
def test_mastery_uses_cache_on_second_call(client, monkeypatch, new_phone):
    """第一次 fresh 并物化落库，第二次命中 cache 且结果一致。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    seed_student_with_mistakes(student_id, kp_name="缓存测试点", wrong=2, right=2)

    first = _mastery(client, headers)
    assert first["mode"] == MODE_FRESH, first

    rows = load_mastery_rows(student_id)
    assert len(rows) == 1, rows
    assert rows[0].computed_at is not None, rows
    assert isinstance(rows[0].error_summary, dict), rows

    second = _mastery(client, headers)
    assert second["mode"] == MODE_CACHE, second
    assert second["mastery"] == first["mastery"], (first, second)
    assert second["summary"] == first["summary"], (first, second)


def test_mastery_force_bypasses_cache(client, monkeypatch, new_phone):
    """``?force=1`` 跳过缓存强制重算，mode 回到 fresh。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    seed_student_with_mistakes(student_id, kp_name="强刷测试点", wrong=1, right=3)

    assert _mastery(client, headers)["mode"] == MODE_FRESH
    assert _mastery(client, headers)["mode"] == MODE_CACHE
    assert _mastery(client, headers, "?force=1")["mode"] == MODE_FRESH
    # 强刷之后缓存重新变新，下一次又能命中
    assert _mastery(client, headers)["mode"] == MODE_CACHE


def test_mastery_cache_invalidated_by_new_practice(client, monkeypatch, new_phone):
    """新的作答落库后，缓存里的旧数字必须被刷新（脏检查生效）。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _, questions = seed_kp_and_questions("脏检查点", question_count=1)
    seed_attempts(student_id, questions[0], [False, False])

    first = _mastery(client, headers)
    assert first["mastery"][0]["attempts"] == 2, first
    assert _mastery(client, headers)["mode"] == MODE_CACHE

    # 新增作答 -> 强刷一次就能看到新数字
    seed_attempts(student_id, questions[0], [True, True, True])
    refreshed = _mastery(client, headers, "?force=1")

    assert refreshed["mastery"][0]["attempts"] == 5, refreshed
    assert refreshed["mastery"][0]["correct_count"] == 3, refreshed
    assert refreshed["mastery"][0]["score"] > first["mastery"][0]["score"], (first, refreshed)


def test_mastery_kp_id_filter(client, monkeypatch, new_phone):
    """``kp_id`` 过滤只返回指定知识点。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    kp_a, qs_a = seed_kp_and_questions("过滤甲", question_count=1)
    _, qs_b = seed_kp_and_questions("过滤乙", question_count=1)
    seed_attempts(student_id, qs_a[0], [False, True])
    seed_attempts(student_id, qs_b[0], [True, True])

    data = _mastery(client, headers, f"?kp_id={kp_a}")

    assert len(data["mastery"]) == 1, data
    assert data["mastery"][0]["kp_id"] == kp_a, data
    assert data["summary"]["total_kp"] == 1, data


# ==================================================================== 降级
def test_mastery_degrades_to_code_5001_on_compute_error(client, monkeypatch, new_phone):
    """计算炸了也不能整页 500：HTTP 仍 200，业务码 5001，data 骨架齐全。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟画像计算异常")

    monkeypatch.setattr("app.services.profile_service.get_mastery", _boom)

    resp = client.get(MASTERY_URL, headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == CODE_PROFILE_COMPUTE_FAILED, body
    assert body["data"]["mastery"] == [], body
    assert body["data"]["summary"]["total_kp"] == 0, body
    assert body["data"]["student_id"] == str(student_id), body


def test_error_patterns_degrades_to_code_5001(client, monkeypatch, new_phone):
    """错因接口同样走局部降级。"""
    patch_ark(monkeypatch)
    headers, _ = login(client, new_phone())

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟错因聚合异常")

    monkeypatch.setattr("app.services.profile_service.get_error_patterns", _boom)

    resp = client.get(PATTERN_URL, headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == CODE_PROFILE_COMPUTE_FAILED, body
    assert body["data"]["error_patterns"] == [], body


def test_recommendations_degrades_to_code_5001(client, monkeypatch, new_phone):
    """推荐接口同样走局部降级。"""
    patch_ark(monkeypatch)
    headers, _ = login(client, new_phone())

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟推荐异常")

    monkeypatch.setattr("app.services.profile_service.get_recommendations", _boom)

    resp = client.get(RECOMMEND_URL, headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == CODE_PROFILE_COMPUTE_FAILED, body
    assert body["data"]["recommendations"] == [], body


# ==================================================================== 隔离
def test_profile_is_isolated_between_students(client, monkeypatch, new_phone):
    """A 的练习数据不能出现在 B 的画像里。"""
    patch_ark(monkeypatch)
    headers_a, student_a = login(client, new_phone())
    headers_b, _ = login(client, new_phone())

    seed_student_with_mistakes(student_a, kp_name="仅甲可见点", wrong=2, right=0)

    data_a = _mastery(client, headers_a)
    data_b = _mastery(client, headers_b)

    assert [item["kp_name"] for item in data_a["mastery"]] == ["仅甲可见点"], data_a
    assert data_b["mastery"] == [], data_b
