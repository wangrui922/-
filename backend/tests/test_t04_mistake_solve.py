"""T04 · T5 ``PUT /api/mistake/{id}/solve`` 集成测试。

覆盖：
    * 成功：``solved`` 翻真、``solvedAt`` 落库、``note`` 回带
    * 幂等：重复调用不刷新 ``solvedAt``
    * 越权：别人的错题 -> **404**（不是 403，避免探测资源存在性）
    * 不存在：合法 UUID 但查无此条 -> 404；UUID 非法 -> 400
    * 鉴权：无令牌 -> 401
    * B11 联动：攻克后掌握度缓存失效，下次看板重算并把它计成一次正样本
    * 三态联动：攻克后错题从 ``unsolved`` 移到 ``mastered``
"""

from __future__ import annotations

import uuid

from app.core.constants import MODE_CACHE, MODE_FRESH

from t04_helpers import (
    assert_fail,
    assert_ok,
    login,
    make_question,
    patch_ark,
    seed_attempts,
    seed_kp_and_questions,
    seed_mistake,
)

MASTERY_URL = "/api/profile/mastery"


def _solve_url(mistake_id: str) -> str:
    """拼攻克接口地址。"""
    return f"/api/mistake/{mistake_id}/solve"


# ==================================================================== 成功
def test_solve_mistake_success(client, monkeypatch, new_phone):
    """标记攻克成功：solved=true，solvedAt 有值，note 被写入并回带。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _, questions = seed_kp_and_questions("攻克测试点", question_count=1)
    mistake_id = seed_mistake(student_id, questions[0], error_type="计算失误")

    data = assert_ok(
        client.put(_solve_url(mistake_id), headers=headers, json={"note": "重做了三遍，进位记住了"})
    )

    assert data["id"] == mistake_id, data
    assert data["solved"] is True, data
    assert data["solvedAt"], data
    assert data["note"] == "重做了三遍，进位记住了", data
    assert data["questionId"] == questions[0], data
    assert data["errorType"] == "计算失误", data


def test_solve_mistake_without_body(client, monkeypatch, new_phone):
    """不带请求体也能攻克（note 可选），note 保持 null。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _, questions = seed_kp_and_questions("无备注攻克点", question_count=1)
    mistake_id = seed_mistake(student_id, questions[0])

    data = assert_ok(client.put(_solve_url(mistake_id), headers=headers))

    assert data["solved"] is True, data
    assert data["note"] is None, data


def test_solve_mistake_moves_to_mastered_state(client, monkeypatch, new_phone):
    """攻克后错题从 unsolved 移动到 mastered。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _, questions = seed_kp_and_questions("三态联动点", question_count=1)
    mistake_id = seed_mistake(student_id, questions[0])

    before = assert_ok(client.get("/api/mistake/list?state=unsolved", headers=headers))
    assert any(item["id"] == mistake_id for item in before["items"]), before

    assert_ok(client.put(_solve_url(mistake_id), headers=headers))

    after_unsolved = assert_ok(client.get("/api/mistake/list?state=unsolved", headers=headers))
    after_mastered = assert_ok(client.get("/api/mistake/list?state=mastered", headers=headers))

    assert all(item["id"] != mistake_id for item in after_unsolved["items"]), after_unsolved
    assert any(item["id"] == mistake_id for item in after_mastered["items"]), after_mastered


# ==================================================================== 幂等
def test_solve_mistake_is_idempotent(client, monkeypatch, new_phone):
    """重复调用不刷新 solvedAt，返回体保持一致。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _, questions = seed_kp_and_questions("幂等攻克点", question_count=1)
    mistake_id = seed_mistake(student_id, questions[0])

    first = assert_ok(client.put(_solve_url(mistake_id), headers=headers, json={"note": "第一次"}))
    second = assert_ok(client.put(_solve_url(mistake_id), headers=headers))

    assert first["solvedAt"] == second["solvedAt"], (first, second)
    assert second["solved"] is True, second
    assert second["note"] == "第一次", second  # 不传 note 不会把已有备注抹掉


def test_solve_mistake_can_update_note_afterwards(client, monkeypatch, new_phone):
    """已攻克后补写/修改 note 允许生效，但仍不刷新 solvedAt。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _, questions = seed_kp_and_questions("补备注点", question_count=1)
    mistake_id = seed_mistake(student_id, questions[0])

    first = assert_ok(client.put(_solve_url(mistake_id), headers=headers))
    second = assert_ok(client.put(_solve_url(mistake_id), headers=headers, json={"note": "后补的心得"}))

    assert first["note"] is None, first
    assert second["note"] == "后补的心得", second
    assert first["solvedAt"] == second["solvedAt"], (first, second)


def test_solve_mistake_blank_note_is_ignored(client, monkeypatch, new_phone):
    """空白 note 视为没传，不会写进库。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _, questions = seed_kp_and_questions("空备注点", question_count=1)
    mistake_id = seed_mistake(student_id, questions[0])

    data = assert_ok(client.put(_solve_url(mistake_id), headers=headers, json={"note": "   "}))

    assert data["solved"] is True, data
    assert data["note"] is None, data


# ==================================================================== 越权 / 不存在
def test_solve_other_student_mistake_returns_404(client, monkeypatch, new_phone):
    """别人的错题 -> 404（**不是 403**），且不能被改动。"""
    patch_ark(monkeypatch)
    headers_a, student_a = login(client, new_phone())
    headers_b, _ = login(client, new_phone())

    _, questions = seed_kp_and_questions("越权测试点", question_count=1)
    mistake_id = seed_mistake(student_a, questions[0])

    resp = client.put(_solve_url(mistake_id), headers=headers_b, json={"note": "我不是主人"})

    assert resp.status_code == 404, resp.text
    assert resp.status_code != 403, "越权必须伪装成 404，不能返回 403"
    assert resp.json()["code"] == 404, resp.text

    # 原主人看到的仍是未攻克
    items = assert_ok(client.get("/api/mistake/list", headers=headers_a))["items"]
    target = next(item for item in items if item["id"] == mistake_id)
    assert target["solved"] is False, target
    assert target["note"] is None, target


def test_solve_nonexistent_mistake_returns_404(client, monkeypatch, new_phone):
    """合法 UUID 但库里没有 -> 404。"""
    patch_ark(monkeypatch)
    headers, _ = login(client, new_phone())

    assert_fail(client.put(_solve_url(str(uuid.uuid4())), headers=headers), 404)


def test_solve_malformed_id_returns_400(client, monkeypatch, new_phone):
    """ID 不是合法 UUID -> 400（与 404 区分开，便于定位问题）。"""
    patch_ark(monkeypatch)
    headers, _ = login(client, new_phone())

    assert_fail(client.put(_solve_url("not-a-uuid"), headers=headers), 400)


def test_solve_requires_auth(client, monkeypatch):
    """未登录 -> 401。"""
    patch_ark(monkeypatch)

    assert_fail(client.put(_solve_url(str(uuid.uuid4()))), 401)


# ==================================================================== B11 缓存联动
def test_solve_invalidates_mastery_cache(client, monkeypatch, new_phone):
    """B11：攻克后掌握度缓存失效，下次看板重算并把它计成一次正样本。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    kp_id, questions = seed_kp_and_questions("缓存联动点", question_count=1)
    # 故意不造 attempts：这样错题就是该知识点的唯一事件源，攻克前后差异最明显
    mistake_id = seed_mistake(student_id, questions[0], error_type="计算失误")

    first = assert_ok(client.get(MASTERY_URL, headers=headers))
    assert first["mode"] == MODE_FRESH, first
    before = first["mastery"][0]
    assert before["kp_id"] == kp_id, before
    assert before["attempts"] == 1, before
    assert before["correct_count"] == 0, before

    # 缓存已生效
    assert assert_ok(client.get(MASTERY_URL, headers=headers))["mode"] == MODE_CACHE

    # 攻克 -> 缓存应被标脏
    assert_ok(client.put(_solve_url(mistake_id), headers=headers, json={"note": "搞懂了"}))

    second = assert_ok(client.get(MASTERY_URL, headers=headers))
    after = second["mastery"][0]

    assert second["mode"] == MODE_FRESH, second
    assert after["attempts"] == 2, after           # 负样本 + 攻克推断的正样本
    assert after["correct_count"] == 1, after
    assert after["score"] > before["score"], (before, after)


def test_solve_on_unmapped_question_does_not_crash(client, monkeypatch, new_phone):
    """R5：题目没挂知识点时，攻克照样成功（缓存失效自然是 0 行，不能报错）。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    orphan = make_question(kp_id=None, content="没挂知识点的攻克题")
    mistake_id = seed_mistake(student_id, orphan, error_type="计算失误")

    data = assert_ok(client.put(_solve_url(mistake_id), headers=headers))

    assert data["solved"] is True, data


def test_solve_keeps_other_kp_cache_valid(client, monkeypatch, new_phone):
    """只让被攻克题目所属知识点的缓存失效，别的知识点行不受影响。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    kp_hit, qs_hit = seed_kp_and_questions("被影响点", question_count=1)
    kp_keep, qs_keep = seed_kp_and_questions("不受影响点", question_count=1)

    seed_attempts(student_id, qs_keep[0], [True, True, True])
    mistake_id = seed_mistake(student_id, qs_hit[0], error_type="概念不清")

    baseline = assert_ok(client.get(MASTERY_URL, headers=headers))
    keep_before = next(item for item in baseline["mastery"] if item["kp_id"] == kp_keep)

    assert_ok(client.put(_solve_url(mistake_id), headers=headers))

    after = assert_ok(client.get(MASTERY_URL, headers=headers))
    keep_after = next(item for item in after["mastery"] if item["kp_id"] == kp_keep)
    hit_after = next(item for item in after["mastery"] if item["kp_id"] == kp_hit)

    # 未受影响的知识点数字不变（重算结果一致）
    assert keep_after["attempts"] == keep_before["attempts"], (keep_before, keep_after)
    assert keep_after["correct_count"] == keep_before["correct_count"], (keep_before, keep_after)
    # 被攻克的知识点多了一次正样本
    assert hit_after["correct_count"] == 1, hit_after
