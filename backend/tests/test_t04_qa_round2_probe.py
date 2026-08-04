"""T04 · QA 第 2 轮回归探针（严过关独立编写，用于验证缺陷1修复未改坏去重）。

背景：
    缺陷1 的修复把 ``_load_mistakes`` 中 solved 正样本附加块移到了去重
    ``continue`` **之前**。该改动直接动了去重代码区，而仓库中**原本没有任何
    用例覆盖负样本去重**，故「100 passed」并不能证明去重未被改坏。
    本文件专门补这一缺口。

覆盖：
    A. 同一题同时被 attempts 与 mistake 覆盖时，负样本只计一次（去重仍生效）；
    B. 该题没有 attempts 前置时，mistake 负样本正常计入（去重没有误伤）；
    C. 有 attempts 前置 + 攻克 时，correct_count +1 且 score 严格上升（缺陷1 已修）。
"""

from __future__ import annotations

from typing import Any, Dict

from t04_helpers import (
    assert_ok,
    login,
    patch_ark,
    seed_attempts,
    seed_kp_and_questions,
    seed_mistake,
)

MASTERY_URL = "/api/profile/mastery"


def _row(client, headers: Dict[str, str]) -> Dict[str, Any]:
    data = assert_ok(client.get(MASTERY_URL, headers=headers))
    assert data["mastery"], f"应有掌握度行: {data}"
    return data["mastery"][0]


def _wrong(row: Dict[str, Any]) -> int:
    """接口不直接暴露 wrong_count，用 attempts - correct_count 反推负样本数。"""
    return int(row["attempts"]) - int(row["correct_count"])


def test_probe_negative_sample_deduped_when_attempt_and_mistake_overlap(
    client, monkeypatch, new_phone
):
    """A：同一道题既有 attempts 错答、又建了错题 → 负样本只能计一次。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _kp, questions = seed_kp_and_questions("去重探针点A", question_count=1)

    seed_attempts(student_id, questions[0], [False])
    seed_mistake(student_id, questions[0], solved=False)

    row = _row(client, headers)
    # 一次错答 + 一条同题错题，去重后 wrong 事件应恒为 1
    assert row["attempts"] == 1, f"负样本去重被改坏，同题重复计数: {row}"
    assert _wrong(row) == 1, f"负样本去重被改坏，重复计数: {row}"
    assert row["correct_count"] == 0, row


def test_probe_mistake_negative_still_counted_without_attempt(
    client, monkeypatch, new_phone
):
    """B：没有 attempts 前置的错题，负样本仍要计入（去重不能误伤）。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _kp, questions = seed_kp_and_questions("去重探针点B", question_count=1)

    seed_mistake(student_id, questions[0], solved=False)

    row = _row(client, headers)
    assert row["attempts"] == 1, f"无 attempts 的错题负样本被漏计: {row}"
    assert _wrong(row) == 1, f"无 attempts 的错题负样本被漏计: {row}"
    assert row["correct_count"] == 0, row


def test_probe_solve_raises_score_strictly_with_attempts_present(
    client, monkeypatch, new_phone
):
    """C：有 attempts 前置时攻克 → correct_count +1 且 score 严格上升。"""
    patch_ark(monkeypatch)
    headers, student_id = login(client, new_phone())
    _kp, questions = seed_kp_and_questions("去重探针点C", question_count=1)

    seed_attempts(student_id, questions[0], [False, False])
    mistake_id = seed_mistake(student_id, questions[0], solved=False)

    before = _row(client, headers)
    assert_ok(client.put(f"/api/mistake/{mistake_id}/solve", headers=headers))
    after = _row(client, headers)

    assert after["correct_count"] == before["correct_count"] + 1, (before, after)
    assert after["score"] > before["score"], (before, after)
    # 攻克不应凭空增加负样本
    assert _wrong(after) == _wrong(before), (before, after)
