"""#70 真实试卷入库 (exam_yunnan_2025) — 双不变式闸门回归。

锁死:
- 批次 A (云南中考真题) ship_ready=1 + source_tag=green
- 所有选择题过 classify_question(clean) + validate_choice_options + is_l1_safe
- 27 道题入库齐全(15 选择 + 4 填空 + 8 解答)
"""
import json
import sqlite3
from pathlib import Path

from app.services.importers.quality_gate import (
    classify_question,
    validate_choice_options,
)
from app.services.judge_engine import is_l1_safe

ROOT = Path(__file__).resolve().parents[1]
DEV_DB = ROOT / "dev.db"
SEED_FILE = ROOT / "seed" / "exam_yunnan_2025.json"


def _conn():
    return sqlite3.connect(str(DEV_DB))


class TestExamYunnan2025Import:
    """云南中考真题批次: 入库后回归闸门。"""

    def test_seed_file_present(self):
        assert SEED_FILE.is_file(), f"种子文件不存在: {SEED_FILE}"

    def test_27_questions_imported_with_answers(self):
        c = _conn()
        try:
            n = c.execute(
                "SELECT count(*) FROM questions q JOIN answers a ON a.question_id=q.id "
                "WHERE q.source_doc='exam_yunnan_2025'"
            ).fetchone()[0]
        finally:
            c.close()
        assert n == 27, f"应入库 27 题, 实入 {n} 题"

    def test_batch_a_is_ship_ready_green(self):
        c = _conn()
        try:
            n = c.execute(
                "SELECT count(*) FROM questions "
                "WHERE source_doc='exam_yunnan_2025' AND ship_ready=1 AND source_tag='green'"
            ).fetchone()[0]
        finally:
            c.close()
        assert n == 27, f"批次A应 27 题全 ship_ready=1+green, 实为 {n}"

    def test_choice_questions_pass_dual_invariant(self):
        """选择题过三道闸门：清白 + ABCD卡口 + L1安全池。"""
        c = _conn()
        try:
            rows = c.execute(
                "SELECT q.content, q.options, a.correct_answer FROM questions q "
                "JOIN answers a ON a.question_id=q.id "
                "WHERE q.source_doc='exam_yunnan_2025' AND q.type='选择'"
            ).fetchall()
        finally:
            c.close()
        assert len(rows) == 15, f"应 15 道选择题, 实 {len(rows)}"
        bad = []
        for content, options_json, correct in rows:
            options = json.loads(options_json) if isinstance(options_json, str) else options_json
            v, reasons = classify_question(content, None, q_type="选择", options=options)
            if v != "clean":
                bad.append(("clean", reasons, content[:30]))
                continue
            if not validate_choice_options(options, correct):
                bad.append(("valid_opts", options, content[:30]))
                continue
            if not is_l1_safe("选择", options, correct):
                bad.append(("l1_safe", correct, content[:30]))
        assert not bad, f"{len(bad)} 道选择题未过双不变式: {bad[:3]}"

    def test_choice_questions_linked_to_cz_kp(self):
        """选择题 100% 挂上 CZ_ 知识点, 入掌握度闭环。"""
        c = _conn()
        try:
            n = c.execute(
                "SELECT count(*) FROM questions q "
                "JOIN knowledge_points kp ON kp.id=q.kp_id "
                "WHERE q.source_doc='exam_yunnan_2025' AND q.type='选择' AND kp.code LIKE 'CZ_%'"
            ).fetchone()[0]
        finally:
            c.close()
        assert n == 15, f"选择题应全部挂CZ_ KP, 实挂 {n}/15"

    def test_question_idempotency(self):
        """同一 JSON 重跑应零新增(uid5 派生 id 幂等)。"""
        from app.scripts.import_exam_pdf import run  # noqa: WPS433
        before = _conn().execute(
            "SELECT count(*) FROM questions WHERE source_doc='exam_yunnan_2025'"
        ).fetchone()[0]
        run(str(SEED_FILE))
        after = _conn().execute(
            "SELECT count(*) FROM questions WHERE source_doc='exam_yunnan_2025'"
        ).fetchone()[0]
        assert before == after == 27, f"重入应幂等: before={before} after={after}"