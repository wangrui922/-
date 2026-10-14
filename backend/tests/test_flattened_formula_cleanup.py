"""2026-08-19 存量公式压扁题清洗回归测试。"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path


_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "data_cleanup"
    / "20260819_repair_flattened_formulas.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location("flattened_formula_cleanup", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_db(path: Path, module) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE questions ("
        "id TEXT PRIMARY KEY, content TEXT NOT NULL, status TEXT NOT NULL, "
        "source_tag TEXT, ship_ready INTEGER)"
    )
    for qid, patch in module.REPAIRS.items():
        con.execute(
            "INSERT INTO questions VALUES (?, ?, 'active', 'green', 1)",
            (qid, patch.old_content),
        )
    for qid, patch in module.ARCHIVES.items():
        con.execute(
            "INSERT INTO questions VALUES (?, ?, 'active', 'green', 1)",
            (qid, patch.old_content),
        )
    con.execute(
        "INSERT INTO questions VALUES "
        "('healthy', '计算 1/2 + 1/3。', 'active', 'green', 1)"
    )
    con.commit()
    return con


def test_cleanup_repairs_three_and_archives_three(tmp_path: Path):
    module = _load_script()
    con = _seed_db(tmp_path / "bank.db", module)

    result = module.apply_cleanup(con)

    assert result == {"repaired": 3, "archived": 3}
    for qid, patch in module.REPAIRS.items():
        row = con.execute(
            "SELECT content, status FROM questions WHERE id=?", (qid,)
        ).fetchone()
        assert tuple(row) == (patch.new_content, "active")
    for qid in module.ARCHIVES:
        row = con.execute(
            "SELECT status, source_tag, ship_ready FROM questions WHERE id=?", (qid,)
        ).fetchone()
        assert tuple(row) == ("archived", "red", 0)
    assert tuple(con.execute(
        "SELECT content, status FROM questions WHERE id='healthy'"
    ).fetchone()) == ("计算 1/2 + 1/3。", "active")
    assert con.execute(
        f"SELECT COUNT(*) FROM {module.BACKUP_TABLE}"
    ).fetchone()[0] == 6


def test_cleanup_is_idempotent(tmp_path: Path):
    module = _load_script()
    con = _seed_db(tmp_path / "bank.db", module)

    assert module.apply_cleanup(con) == {"repaired": 3, "archived": 3}
    assert module.apply_cleanup(con) == {"repaired": 0, "archived": 0}
    assert con.execute(
        f"SELECT COUNT(*) FROM {module.BACKUP_TABLE}"
    ).fetchone()[0] == 6


def test_seed_has_no_active_flattened_formula_questions():
    """重建数据库时，旧种子不能把已下架的公式乱码重新激活。"""
    from app.services.importers.quality_gate import flattened_formula_reasons

    seed_path = Path(__file__).resolve().parents[1] / "seed" / "questions.json"
    questions = json.loads(seed_path.read_text(encoding="utf-8"))["questions"]
    hits = [
        (question["bankId"], flattened_formula_reasons(question["content"]))
        for question in questions
        if question["status"] == "active"
        and flattened_formula_reasons(question["content"])
    ]
    assert hits == []


def test_target_fraction_question_is_mathematically_consistent_in_seed():
    """目标题的可读公式、选项 C 与标准答案必须保持一致。"""
    seed_path = Path(__file__).resolve().parents[1] / "seed" / "questions.json"
    questions = json.loads(seed_path.read_text(encoding="utf-8"))["questions"]
    target = next(q for q in questions if q["bankId"] == "x5-042")
    assert target["content"] == "若 1/n + 1/n + 1/n = 1/4（n 为非 0 自然数），则 n =（ ）。"
    assert target["options"]["C"] == "12"
    assert target["answer"] == "选C。C．12"


def test_extract_bank_archives_flattened_formula_questions():
    """从旧页面重新抽取时，公式压扁题也必须默认归档。"""
    from scripts.extract_bank import flatten_bank

    bank = {
        "小学": {
            "五年级": [
                {
                    "id": "formula-bad",
                    "q": "若11114nnn＝++（n为非0自然数），则n＝（",
                    "a": "选C。C．12",
                    "tags": {"knowledge": "综合", "options": {"A": "2", "B": "8", "C": "12", "D": "16"}},
                },
                {
                    "id": "formula-good",
                    "q": "若 1/n + 1/n + 1/n = 1/4（n 为非 0 自然数），则 n =（ ）。",
                    "a": "选C。C．12",
                    "tags": {"knowledge": "综合", "options": {"A": "2", "B": "8", "C": "12", "D": "16"}},
                },
            ]
        }
    }

    questions, _meta = flatten_bank(bank)
    assert [question["status"] for question in questions] == ["archived", "active"]
