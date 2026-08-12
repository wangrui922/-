"""死题归档脚本端到端测试（2026-08-12 优化轮）。

隔离约定完全复用 ``test_residual_choice_cleanup.py`` 的模式：以 **隔离 tmp_path 文件
SQLite** 跑 ``20260812_archive_dead_inventory.py`` 的 ``main()``，并用 ``monkeypatch``
把 ``get_engine`` / ``_REMOVED_CSV`` / ``_common.LOG_PATH`` 重定向到 ``tmp_path``，
避免污染真实交付物（``docs/dead-inventory-removed-20260812.csv`` 与运维日志）。

覆盖：
* ``--dry-run`` 仅统计不写库；
* ``--expect`` 量级熔断（偏差 > 5% 即 exit 2）；
* 实际执行：4 类死题（丢题型 / 缺答案 / 选择答案无法解析 / 判断答案无法解析）
  全部归档进备份表 + CSV；
* **不误杀**：有题型 + 有答案 的 选择/判断/填空 一律保留（留给 AI 判分路径）；
* 防新增卡口 ``validate_question_integrity`` 纯函数单测；
* :class:`TestRealDocsUntouched` 常驻兜底（sha256 快照）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json as _json
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Optional, Sequence

import pytest
import uuid

from scripts.data_cleanup import _common as _cleanup_common
from scripts.data_cleanup import _residual_common as _residual_common_mod

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _BACKEND_ROOT.parent
_SCRIPTS_DIR = _BACKEND_ROOT / "scripts" / "data_cleanup"
_DEAD_SCRIPT = _SCRIPTS_DIR / "20260812_archive_dead_inventory.py"

_PYTHON = sys.executable

#: 真实交付物 —— 测试**只读**这些路径做兜底断言，永不写。
_REAL_ARTIFACTS = (
    _PROJECT_ROOT / "docs" / "dead-inventory-removed-20260812.csv",
    _PROJECT_ROOT / "docs" / "data-cleanup-log-20260811.md",
)

#: 脚本模块里需要被重定向的产物常量名 → 临时文件名。
_CSV_ATTR_TO_NAME = {
    "_REMOVED_CSV": "dead-inventory-removed-20260812.csv",
}


@dataclass
class ScriptResult:
    """``main()`` 的执行结果，字段与 ``subprocess.CompletedProcess`` 对齐。"""

    returncode: int
    stdout: str
    stderr: str


def _load_script(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_dead_script_{path.stem}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - 路径写错才会触发
        raise ImportError(f"无法加载脚本模块：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def run_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., ScriptResult]:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_cleanup_common, "LOG_PATH", docs_dir / "data-cleanup-log.md")

    def _run(script: Path, args: Optional[Sequence[str]], db_url: str) -> ScriptResult:
        module = _load_script(script)
        from sqlalchemy import create_engine

        engine = create_engine(db_url, future=True)
        monkeypatch.setattr(module, "get_engine", lambda: engine)
        for attr, filename in _CSV_ATTR_TO_NAME.items():
            if hasattr(module, attr):
                monkeypatch.setattr(module, attr, docs_dir / filename)

        argv = list(args or [])
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = module.main(argv)
        except SystemExit as exc:
            code = int(exc.code or 0)
        finally:
            engine.dispose()
        return ScriptResult(returncode=int(code or 0), stdout=out.getvalue(), stderr=err.getvalue())

    _run.docs_dir = docs_dir  # type: ignore[attr-defined]
    return _run


def _make_seed_db(tmp_path: Path) -> str:
    from sqlalchemy import create_engine, text

    from app.models.base import Base

    db_path = tmp_path / "seed.db"
    url = f"sqlite:///{db_path}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc).isoformat()

    def _insert_question(conn, qid, content, qtype, opts, status):
        conn.execute(
            text(
                "INSERT INTO questions (id, content, subject, grade, type, options, status, kp_verified, created_at) "
                "VALUES (:id, :content, '数学', 3, :type, :opts, :status, 1, :now)"
            ),
            {
                "id": qid,
                "content": content,
                "type": qtype,
                "opts": _json.dumps(opts, ensure_ascii=False) if opts is not None else None,
                "status": status,
                "now": now,
            },
        )

    def _insert_answer(conn, qid, ans):
        conn.execute(
            text(
                "INSERT INTO answers (id, question_id, correct_answer, distractor_analysis) "
                "VALUES (:id, :qid, :ans, '{}')"
            ),
            {"id": str(uuid.uuid4()), "qid": qid, "ans": ans},
        )

    with engine.begin() as conn:
        # --- 4 道死题（应被归档）---
        # 1) 丢题型：type=NULL，完全抽不到
        _insert_question(conn, "10000000-0000-0000-0000-000000000001", "（ ）比40大比60小", None, None, "active")
        _insert_answer(conn, "10000000-0000-0000-0000-000000000001", "对")
        # 2) 缺答案：有题型但 no answers 行
        _insert_question(conn, "20000000-0000-0000-0000-000000000002", "填空无答案", "填空", None, "active")
        # 3) 选择答案无法解析：选项完整但答案是 'F'
        _insert_question(conn, "30000000-0000-0000-0000-000000000003", "选择答案坏", "选择",
                         {"A": "a", "B": "b", "C": "c", "D": "d"}, "active")
        _insert_answer(conn, "30000000-0000-0000-0000-000000000003", "F")
        # 4) 判断答案无法解析：'也许是' 归不出布尔
        _insert_question(conn, "40000000-0000-0000-0000-000000000004", "判断答案坏", "判断", None, "active")
        _insert_answer(conn, "40000000-0000-0000-0000-000000000004", "也许是")

        # --- 3 道健康题（应保留）---
        # 完整选择 + 合法答案 B
        _insert_question(conn, "50000000-0000-0000-0000-000000000005", "完整选择", "选择",
                         {"A": "a", "B": "b", "C": "c", "D": "d"}, "active")
        _insert_answer(conn, "50000000-0000-0000-0000-000000000005", "B")
        # 完整判断 + 合法答案 对
        _insert_question(conn, "60000000-0000-0000-0000-000000000006", "完整判断", "判断", None, "active")
        _insert_answer(conn, "60000000-0000-0000-0000-000000000006", "对")
        # 填空 + 有答案（走 AI 判分，不算死题）
        _insert_question(conn, "70000000-0000-0000-0000-000000000007", "填空有答案", "填空", None, "active")
        _insert_answer(conn, "70000000-0000-0000-0000-000000000007", "12")

        # 1 道已 archived 死题（不应再被触碰）
        _insert_question(conn, "80000000-0000-0000-0000-000000000008", "已归档死题", None, None, "archived")
        _insert_answer(conn, "80000000-0000-0000-0000-000000000008", "对")

    engine.dispose()
    return str(db_path)


@pytest.fixture
def seed_db_url(tmp_path: Path) -> str:
    return f"sqlite:///{_make_seed_db(tmp_path)}"


def _count_active(db_url: str) -> int:
    from sqlalchemy import create_engine, text

    eng = create_engine(db_url, future=True)
    try:
        return eng.connect().execute(
            text("SELECT COUNT(*) FROM questions WHERE status='active'")
        ).scalar()
    finally:
        eng.dispose()


def _count_archived(db_url: str) -> int:
    from sqlalchemy import create_engine, text

    eng = create_engine(db_url, future=True)
    try:
        return eng.connect().execute(
            text("SELECT COUNT(*) FROM questions WHERE status='archived'")
        ).scalar()
    finally:
        eng.dispose()


class TestDeadInventoryDryRun:
    def test_dry_run_reports_four_dead_no_write(self, run_script, seed_db_url, tmp_path):
        res = run_script(_DEAD_SCRIPT, ["--dry-run", "--expect", "4"], seed_db_url)
        assert res.returncode == 0, res.stderr
        assert "命中归档 4" in res.stdout
        # dry-run 不写库：active 仍为 7
        assert _count_active(seed_db_url) == 7
        # CSV 未写出
        assert not (run_script.docs_dir / "dead-inventory-removed-20260812.csv").exists()


class TestDeadInventoryExpectFuse:
    def test_expect_mismatch_aborts(self, run_script, seed_db_url):
        # 期望 100，实际 4，偏差远超 5% → exit 2
        res = run_script(_DEAD_SCRIPT, ["--expect", "100"], seed_db_url)
        assert res.returncode == 2
        assert _count_active(seed_db_url) == 7  # 未写库


class TestDeadInventoryArchive:
    def test_archive_four_preserves_three(self, run_script, seed_db_url, tmp_path):
        res = run_script(_DEAD_SCRIPT, ["--expect", "4"], seed_db_url)
        assert res.returncode == 0, res.stderr
        # 4 死题归档 → active 7→3；archived 原 1 → 5
        assert _count_active(seed_db_url) == 3
        assert _count_archived(seed_db_url) == 5

        # 备份表存在且 4 行
        from sqlalchemy import create_engine, text

        eng = create_engine(seed_db_url, future=True)
        try:
            n = eng.connect().execute(
                text("SELECT COUNT(*) FROM questions_removed_backup_20260812_opt")
            ).scalar()
            reasons = [r[0] for r in eng.connect().execute(
                text("SELECT reason FROM questions_removed_backup_20260812_opt")
            ).all()]
        finally:
            eng.dispose()
        assert n == 4
        assert "dead_no_type" in reasons
        assert "dead_no_answer" in reasons
        assert "dead_broken_answer" in reasons

        # CSV 落盘且 4 行数据
        csv_path = run_script.docs_dir / "dead-inventory-removed-20260812.csv"
        assert csv_path.exists()
        lines = csv_path.read_text(encoding="utf-8-sig").splitlines()
        assert len(lines) == 5  # 表头 + 4 行
        assert lines[0] == "question_id,old_status,old_kp_id,reason,removed_at"

    def test_healthy_questions_survive(self, run_script, seed_db_url):
        run_script(_DEAD_SCRIPT, ["--expect", "4"], seed_db_url)
        from sqlalchemy import create_engine, text

        eng = create_engine(seed_db_url, future=True)
        try:
            active_ids = [r[0] for r in eng.connect().execute(
                text("SELECT id FROM questions WHERE status='active'")
            ).all()]
        finally:
            eng.dispose()
        # 3 道健康题全部存活
        assert "50000000-0000-0000-0000-000000000005" in active_ids
        assert "60000000-0000-0000-0000-000000000006" in active_ids
        assert "70000000-0000-0000-0000-000000000007" in active_ids


class TestIntegrityGate:
    def test_integrity_pure_function(self):
        from app.services.importers.quality_gate import validate_question_integrity as vqi

        # 缺题型
        assert vqi(None, None, "B") == ["题型缺失"]
        # 缺答案
        assert vqi("填空", None, None) == ["正确答案缺失"]
        # 选择答案无法解析
        assert vqi("选择", {"A": "a", "B": "b", "C": "c", "D": "d"}, "F") == ["选择题正确答案无法解析"]
        # 判断答案无法解析
        assert vqi("判断", None, "也许是") == ["判断题正确答案无法解析"]
        # 合法
        assert vqi("选择", {"A": "a", "B": "b", "C": "c", "D": "d"}, "B") == []
        assert vqi("判断", None, "对") == []
        assert vqi("填空", None, "12") == []  # 有答案即可，走 AI 判分


class TestRealDocsUntouched:
    """兜底：真实交付物在跑完测试后，哈希与测试前一致（未被假数据覆盖）。"""

    @pytest.fixture(autouse=True)
    def _snapshot(self):
        self._before = {
            p: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in _REAL_ARTIFACTS
            if p.exists()
        }
        yield
        for p, digest in self._before.items():
            assert p.exists(), f"真实交付物被删除：{p}"
            assert hashlib.sha256(p.read_bytes()).hexdigest() == digest, f"真实交付物被测试污染：{p}"
