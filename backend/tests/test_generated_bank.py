"""#63 专项测试：批量生成的初中/高中题库满足溯源护栏与 L1 不变式。

只验证真实题库 ``dev.db``（与 ``test_practice.py`` 的 RealBank 测试同源）；
库不存在时自动 skip，不影响 CI 主流程。
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid

import pytest

from app.services.importers.quality_gate import validate_choice_options
from app.services import judge_engine

_DEV_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dev.db")
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKIP = not os.path.exists(_DEV_DB)


def _conn():
    return sqlite3.connect(_DEV_DB)


@pytest.mark.skipif(_SKIP, reason="真实题库 dev.db 不存在，跳过 #63 生成题库测试")
class TestGeneratedBank:
    def test_gen_questions_all_l1_safe_and_clean(self):
        """所有 gen_math_bank 选择题都过 L1 安全池 + ABCD 卡口。

        与 ``test_practice`` 的 RealBank 测试同源：直接读 dev.db（options 经
        ``json.loads`` 还原为 dict，模拟 ORM 加载后的形态，``is_l1_safe`` 只认 dict）。
        """
        import json

        c = _conn()
        rows = c.execute(
            "SELECT q.options, a.correct_answer FROM questions q "
            "JOIN answers a ON a.question_id=q.id "
            "WHERE q.status='active' AND q.source_doc='gen_math_bank'"
        ).fetchall()
        c.close()
        assert rows, "应至少生成数百道 gen_math_bank 题"
        bad = 0
        for options_raw, correct in rows:
            options = json.loads(options_raw) if isinstance(options_raw, str) else options_raw
            if not validate_choice_options(options, correct):
                bad += 1
                continue
            if not judge_engine.is_l1_safe("选择", options, correct):
                bad += 1
        assert bad == 0, f"有 {bad} 道生成题未过 L1 安全池/ABCD 卡口"

    def test_gen_source_tag_and_kp_verified(self):
        """生成题溯源标记 = yellow 且知识点已复核（可信任归因）。"""
        c = _conn()
        n = c.execute(
            "SELECT count(*) FROM questions WHERE source_doc='gen_math_bank' "
            "AND source_tag='yellow' AND kp_verified=1"
        ).fetchone()[0]
        total = c.execute(
            "SELECT count(*) FROM questions WHERE source_doc='gen_math_bank'"
        ).fetchone()[0]
        c.close()
        assert n == total, "所有生成题必须 yellow + kp_verified"
        assert total >= 800, f"生成题库规模应达千级（当前 {total}）"

    def test_gen_covers_junior_and_senior(self):
        """初中(7-9)与高中(10-12)每一年级都有可用题。"""
        c = _conn()
        rows = c.execute(
            "SELECT grade, count(*) FROM questions WHERE status='active' "
            "AND source_doc='gen_math_bank' AND grade BETWEEN 7 AND 12 "
            "GROUP BY grade ORDER BY grade"
        ).fetchall()
        c.close()
        by_grade = dict(rows)
        for g in range(7, 13):
            assert by_grade.get(g, 0) >= 50, f"年级 {g} 生成题不足（{by_grade.get(g,0)}）"

    def test_cz_kp_tree_is_two_level(self):
        """初中知识点树严格 2 层（章节→知识点），无 L3 / 无自环 / parentCode 必存在。"""
        raw = json.load(open(os.path.join(_BACKEND, "seed", "math_kp_middle.json"), encoding="utf-8"))
        kps = raw["knowledgePoints"]
        codes = {kp["code"] for kp in kps}
        parent_of = {kp["code"]: kp["parentCode"] for kp in kps}
        for code, pc in parent_of.items():
            if pc is None:
                continue
            assert pc in codes, f"parentCode 不存在: {code}"
            assert pc != code, f"自环: {code}"
            assert parent_of[pc] is None, f"L3: {code}->{pc}->{parent_of[pc]}"
        # 落库计数与 JSON 一致（seed_kp_cz.py 已跑过）
        c = _conn()
        cz = c.execute(
            "SELECT count(*) FROM knowledge_points WHERE code LIKE 'CZ_%'"
        ).fetchone()[0]
        c.close()
        assert cz == len(kps), f"库内 CZ 知识点数({cz})应与 JSON({len(kps)})一致"
