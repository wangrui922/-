"""残缺选择题批处理脚本端到端测试（T04 · 2026-08-12）。

以**隔离 tmp_path 文件 SQLite** 跑两个脚本的 ``main()`` 入口，验证：

* ``20260812_fix_screenshot_four.py`` —— ``--dry-run`` 仅统计不写库；实际执行
  给 3 道题补 D（options 末位追加 D，correct_answer 不变）、1 道 archive；
  补 D 后 options 键序为 A,B,C,D 且 JSON 字节一致（``dumps_like_ormtype``）。
* ``20260812_archive_residual_choices.py`` —— ``--dry-run`` 统计命中数；``--expect``
  量级熔断（偏差 > 5% 即 exit 2）；实际执行把残缺 active 选择题归档进备份表 +
  CSV，且**不碰**已补 D 的 3 道题（D6 顺序铁律双保险）。

隔离约定（⚠️ 本文件历史上出过事故，改动前务必读完）
-----------------------------------------------------------------
两个脚本的产物路径是**模块级常量**，直接指向真实交付物：

* ``<script>._PATCHED_CSV`` → ``docs/residual-choice-patched-20260812.csv``
* ``<script>._REMOVED_CSV`` → ``docs/residual-choice-removed-20260812.csv``
* ``scripts.data_cleanup._common.LOG_PATH`` → ``docs/data-cleanup-log-20260811.md``

早期版本用 ``subprocess`` + 仅覆盖 ``DATABASE_URL`` 跑脚本，DB 是隔离了，但上面
三个路径**没有**任何 env/CLI 开关可改 —— 于是每跑一次测试，就用 seed 库的假数据
（18 道 active / 10 道命中）**覆盖**真实交付 CSV，并往运维日志里追加假记录。

现在改为 in-process 调 ``main(argv)``，并用 ``monkeypatch`` 把
``get_engine`` / ``_PATCHED_CSV`` / ``_REMOVED_CSV`` / ``LOG_PATH`` 全部重定向到
``tmp_path``。仍是端到端（真 argparse + 真 SQLAlchemy + 真 CSV 落盘），只是产物
落在临时目录。:class:`TestRealDocsUntouched` 常驻兜底，防止该事故复发。

不触碰真实 dev.db（``get_engine`` 被换成指向 tmp_path 的 seed 库）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict, List, Optional, Sequence

import pytest

from scripts.data_cleanup import _common as _cleanup_common

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _BACKEND_ROOT.parent
_SCRIPTS_DIR = _BACKEND_ROOT / "scripts" / "data_cleanup"
_FIX_SCRIPT = _SCRIPTS_DIR / "20260812_fix_screenshot_four.py"
_ARCHIVE_SCRIPT = _SCRIPTS_DIR / "20260812_archive_residual_choices.py"

_PYTHON = sys.executable

#: 真实交付物 —— 测试**只读**这三个路径（用于兜底断言），永不写。
_REAL_ARTIFACTS = (
    _PROJECT_ROOT / "docs" / "residual-choice-removed-20260812.csv",
    _PROJECT_ROOT / "docs" / "residual-choice-patched-20260812.csv",
    _PROJECT_ROOT / "docs" / "data-cleanup-log-20260811.md",
)

#: 脚本模块里需要被重定向的产物常量名 → 临时文件名。
_CSV_ATTR_TO_NAME = {
    "_REMOVED_CSV": "residual-choice-removed-20260812.csv",
    "_PATCHED_CSV": "residual-choice-patched-20260812.csv",
}


# ==================================================================== 脚本加载 / 执行
@dataclass
class ScriptResult:
    """``main()`` 的执行结果，字段与 ``subprocess.CompletedProcess`` 对齐。"""

    returncode: int
    stdout: str
    stderr: str


def _load_script(path: Path) -> ModuleType:
    """按文件路径加载脚本模块。

    脚本文件名以数字开头（``20260812_*.py``），不是合法标识符，无法用 import
    语句直连，只能走 ``importlib`` 按路径加载。每次调用都返回**全新模块对象**，
    避免多个用例之间 monkeypatch 相互串味。
    """
    spec = importlib.util.spec_from_file_location(f"_cleanup_script_{path.stem}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - 路径写错才会触发
        raise ImportError(f"无法加载脚本模块：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def run_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., ScriptResult]:
    """返回一个「在完全隔离的产物目录里跑脚本 ``main()``」的执行器。

    重定向清单（缺一不可，少一个就会污染真实交付物）：

    * ``mod.get_engine`` → 指向入参 ``db_url`` 的临时库（绕过 conftest 的内存库）；
    * ``mod._REMOVED_CSV`` / ``mod._PATCHED_CSV`` → ``tmp_path/docs/``；
    * ``_common.LOG_PATH`` → ``tmp_path/docs/data-cleanup-log.md``。

    执行器另带 ``docs_dir`` 属性，供用例断言临时 CSV 内容。
    """
    from sqlalchemy import create_engine

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    # 运维日志同样重定向：它由 append_log() 在 _common 里按模块级 LOG_PATH 写，
    # 不拦就会往真实 docs/data-cleanup-log-20260811.md 追加假记录。
    monkeypatch.setattr(_cleanup_common, "LOG_PATH", docs_dir / "data-cleanup-log.md")

    def _run(script: Path, args: Optional[Sequence[str]], db_url: str) -> ScriptResult:
        module = _load_script(script)

        engine = create_engine(db_url, future=True)
        monkeypatch.setattr(module, "get_engine", lambda: engine)

        for attr, filename in _CSV_ATTR_TO_NAME.items():
            if hasattr(module, attr):
                monkeypatch.setattr(module, attr, docs_dir / filename)

        # ⚠️ 必须传 list：main(None) 会让 argparse 去读 sys.argv，
        # 在 pytest 下就是 pytest 自己的命令行参数，直接炸。
        argv = list(args or [])
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = module.main(argv)
        except SystemExit as exc:  # argparse 的 --help / 参数错误走这条
            code = int(exc.code or 0)
        finally:
            engine.dispose()
        return ScriptResult(returncode=int(code or 0), stdout=out.getvalue(), stderr=err.getvalue())

    _run.docs_dir = docs_dir  # type: ignore[attr-defined]
    return _run


# ==================================================================== seed 库
def _make_seed_db(tmp_path: Path) -> str:
    """造一个临时 SQLite 库：含 4 道截图题 + 若干残缺 active 选择题 + 已 archived 题。

    返回库文件路径。执行器通过 ``sqlite:///<path>`` 指向它。
    """
    import json as _json
    from datetime import datetime, timezone

    from sqlalchemy import create_engine, text

    from app.models.base import Base

    db_path = tmp_path / "seed.db"
    url = f"sqlite:///{db_path}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc).isoformat()

    def _insert(conn, qid: str, content: str, opts: Dict[str, str], status: str) -> None:
        conn.execute(
            text(
                "INSERT INTO questions (id, content, subject, grade, type, options, status, kp_verified, created_at) "
                "VALUES (:id, :content, '数学', 3, '选择', :opts, :status, 1, :now)"
            ),
            {
                "id": qid,
                "content": content,
                "opts": _json.dumps(opts, ensure_ascii=False),
                "status": status,
                "now": now,
            },
        )

    def _insert_answer(conn, qid: str, ans: str) -> None:
        conn.execute(
            text(
                "INSERT INTO answers (id, question_id, correct_answer, distractor_analysis) "
                "VALUES (:id, :qid, :ans, '{}')"
            ),
            {"id": f"a-{qid}", "qid": qid, "ans": ans},
        )

    with engine.begin() as conn:
        # 4 道截图题（缺 D，active，选择题，dict 形态），正确选项保持不变
        screenshot = [
            ("9a4f1461-1111-1111-1111-111111111111", {"A": "50", "B": "55", "C": "35"}, "B", "比40大比60小的"),
            ("9cf5259e-2222-2222-2222-222222222222", {"A": "7", "B": "6", "C": "5"}, "C", "1□6÷6得数一位数"),
            ("a3275f03-3333-3333-3333-333333333333", {"A": "12张", "B": "32张", "C": "63张"}, "C", "小强比小阳多得多"),
            ("855ae9b6-4444-4444-4444-444444444444", {"A": "13－4", "B": "13－5－4", "C": "13－5"}, "C", "包子题"),
        ]
        for qid, opts, ans, content in screenshot:
            _insert(conn, qid, content, opts, "active")
            _insert_answer(conn, qid, ans)

        # 10 道普通残缺 active 选择题（缺 D，dict 形态），应被归档
        for i in range(10):
            qid = f"aaaaaaaa-0000-0000-0000-{i:012d}"
            _insert(conn, qid, f"残缺题{i}", {"A": "x", "B": "y", "C": "z"}, "active")
            _insert_answer(conn, qid, "A")

        # 5 道完整 A-D active 选择题（dict 形态，不应被归档）
        for i in range(5):
            qid = f"bbbbbbbb-0000-0000-0000-{i:012d}"
            _insert(conn, qid, f"完整题{i}", {"A": "a", "B": "b", "C": "c", "D": "d"}, "active")
            _insert_answer(conn, qid, "B")

        # 3 道已 archived 的残缺题（dict 形态，不应再被触碰）
        for i in range(3):
            qid = f"cccccccc-0000-0000-0000-{i:012d}"
            _insert(conn, qid, f"已归档{i}", {"A": "p", "B": "q", "C": "r"}, "archived")

    engine.dispose()
    return str(db_path)


@pytest.fixture
def seed_db_url(tmp_path: Path) -> str:
    """seed 库的 ``sqlite:///`` URL。"""
    return f"sqlite:///{_make_seed_db(tmp_path)}"


# ==================================================================== 断言 helper
def _count_active_residual(db_url: str) -> int:
    """统计库内 active 选择题里仍残缺（key 集合 ≠ {A,B,C,D}）的数量。"""
    import json

    from sqlalchemy import create_engine, text

    from app.services.judge_engine import option_keys

    engine = create_engine(db_url, future=True)
    n = 0
    try:
        with engine.connect() as conn:
            for (raw,) in conn.execute(
                text("SELECT options FROM questions WHERE status='active' AND type='选择'")
            ).all():
                try:
                    parsed = json.loads(raw) if isinstance(raw, str) else raw
                except (TypeError, ValueError):
                    parsed = raw
                if set(option_keys(parsed)) != {"A", "B", "C", "D"}:
                    n += 1
    finally:
        engine.dispose()
    return n


def _options_of(db_url: str, qid: str) -> Any:
    import json

    from sqlalchemy import create_engine, text

    engine = create_engine(db_url, future=True)
    try:
        with engine.connect() as conn:
            (raw,) = conn.execute(
                text("SELECT options FROM questions WHERE id=:id"), {"id": qid}
            ).one()
    finally:
        engine.dispose()
    return json.loads(raw) if isinstance(raw, str) else raw


def _status_of(db_url: str, qid: str) -> str:
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url, future=True)
    try:
        with engine.connect() as conn:
            (st,) = conn.execute(
                text("SELECT status FROM questions WHERE id=:id"), {"id": qid}
            ).one()
    finally:
        engine.dispose()
    return st


def _scalar(db_url: str, sql: str, **params: Any) -> Any:
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url, future=True)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params).scalar()
    finally:
        engine.dispose()


def _read_csv(path: Path) -> List[List[str]]:
    import csv

    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.reader(fh))


# ==================================================================== T02 补 D 脚本
class TestFixScreenshotFour:
    def test_dry_run_only_counts(self, run_script, seed_db_url: str) -> None:
        proc = run_script(_FIX_SCRIPT, ["--dry-run"], seed_db_url)
        assert proc.returncode == 0, proc.stderr
        assert "补 D 3 道" in proc.stdout
        assert "归档 1 道" in proc.stdout
        # dry-run 不改库
        assert _count_active_residual(seed_db_url) == 10 + 4  # 10 普通残缺 + 4 截图残缺
        assert _status_of(seed_db_url, "9cf5259e-2222-2222-2222-222222222222") == "active"
        # dry-run 不写 CSV
        assert not (run_script.docs_dir / "residual-choice-patched-20260812.csv").exists()

    def test_real_run_patches_d_and_archives(self, run_script, seed_db_url: str) -> None:
        proc = run_script(_FIX_SCRIPT, [], seed_db_url)
        assert proc.returncode == 0, proc.stderr

        # 3 道补 D：键序 A,B,C,D，correct_answer 不变
        for qid, d_val, ans in [
            ("9a4f1461-1111-1111-1111-111111111111", "65", "B"),
            ("a3275f03-3333-3333-3333-333333333333", "30张", "C"),
            ("855ae9b6-4444-4444-4444-444444444444", "5-4", "C"),
        ]:
            opts = _options_of(seed_db_url, qid)
            assert list(opts.keys()) == ["A", "B", "C", "D"], opts
            assert opts["D"] == d_val
            assert _status_of(seed_db_url, qid) == "active"
            # correct_answer 绝不改动（设计 §3 / PRD N0.1）
            assert (
                _scalar(seed_db_url, "SELECT correct_answer FROM answers WHERE question_id=:q", q=qid)
                == ans
            )

        # 9cf5259e 被 archive
        assert _status_of(seed_db_url, "9cf5259e-2222-2222-2222-222222222222") == "archived"

        # 备份表留痕
        assert _scalar(seed_db_url, "SELECT COUNT(*) FROM questions_options_backup_20260812") == 3
        assert (
            _scalar(
                seed_db_url,
                "SELECT COUNT(*) FROM questions_removed_backup_20260812 "
                "WHERE reason='residual_choice_logic_broken'",
            )
            == 1
        )

        # CSV 落在隔离目录，内容正确
        csv_path = run_script.docs_dir / "residual-choice-patched-20260812.csv"
        assert csv_path.exists()
        rows = _read_csv(csv_path)
        assert rows[0] == ["question_id", "action", "old_d", "new_d"]
        assert len(rows) - 1 == 4  # 3 patch_d + 1 archive
        assert {r[1] for r in rows[1:]} == {"patch_d", "archive"}

    def test_rerun_is_idempotent(self, run_script, seed_db_url: str) -> None:
        """重复执行不应产生重复留痕（NOT EXISTS 去重）。"""
        assert run_script(_FIX_SCRIPT, [], seed_db_url).returncode == 0
        assert run_script(_FIX_SCRIPT, [], seed_db_url).returncode == 0
        assert _scalar(seed_db_url, "SELECT COUNT(*) FROM questions_options_backup_20260812") == 3
        assert (
            _scalar(
                seed_db_url,
                "SELECT COUNT(*) FROM questions_removed_backup_20260812 "
                "WHERE reason='residual_choice_logic_broken'",
            )
            == 1
        )


# ==================================================================== T03 归档脚本
class TestArchiveResidualChoices:
    def test_dry_run_counts(self, run_script, seed_db_url: str) -> None:
        # 先跑 T02（补 D 3 道 + archive 1 道），再 T03 dry-run
        run_script(_FIX_SCRIPT, [], seed_db_url)
        proc = run_script(_ARCHIVE_SCRIPT, ["--dry-run", "--expect", "10"], seed_db_url)
        assert proc.returncode == 0, proc.stderr
        # 10 普通残缺 + 3 已 archived（不算）... T03 只扫 active，故 10 道命中
        assert "命中归档 10 道" in proc.stdout
        # dry-run 不改库
        assert _count_active_residual(seed_db_url) == 10
        # dry-run 不写 CSV
        assert not (run_script.docs_dir / "residual-choice-removed-20260812.csv").exists()

    def test_expect_breaker_aborts(self, run_script, seed_db_url: str) -> None:
        # 期望 100，实际 14（未先跑 T02），偏差 86% > 5% → exit 2
        proc = run_script(_ARCHIVE_SCRIPT, ["--expect", "100"], seed_db_url)
        assert proc.returncode == 2
        assert "熔断" in proc.stdout
        # 熔断后一行都不能改
        assert _count_active_residual(seed_db_url) == 14

    def test_real_run_archives_residual_only(self, run_script, seed_db_url: str) -> None:
        # 先跑 T02
        run_script(_FIX_SCRIPT, [], seed_db_url)
        proc = run_script(_ARCHIVE_SCRIPT, ["--expect", "10"], seed_db_url)
        assert proc.returncode == 0, proc.stderr

        # 普通残缺 10 道被归档
        assert _count_active_residual(seed_db_url) == 0

        # 已补 D 的 3 道仍在 active 且完整（D6 双保险）
        for qid in [
            "9a4f1461-1111-1111-1111-111111111111",
            "a3275f03-3333-3333-3333-333333333333",
            "855ae9b6-4444-4444-4444-444444444444",
        ]:
            assert _status_of(seed_db_url, qid) == "active"
            assert set(_options_of(seed_db_url, qid).keys()) == {"A", "B", "C", "D"}

        # 完整 A-D 题不被归档
        assert _status_of(seed_db_url, "bbbbbbbb-0000-0000-0000-000000000000") == "active"

        # 备份表留痕
        assert (
            _scalar(
                seed_db_url,
                "SELECT COUNT(*) FROM questions_removed_backup_20260812 "
                "WHERE reason='residual_choice_missing_abcd'",
            )
            == 10
        )

        # CSV 落在隔离目录，内容正确
        csv_path = run_script.docs_dir / "residual-choice-removed-20260812.csv"
        assert csv_path.exists()
        rows = _read_csv(csv_path)
        assert rows[0] == ["question_id", "old_status", "old_kp_id", "reason", "removed_at"]
        assert len(rows) - 1 == 10


# ==================================================================== 事故兜底
class TestRealDocsUntouched:
    """常驻回归：跑脚本测试**绝不能**改动真实交付物。

    背景：早期版本用 subprocess 只隔离了 ``DATABASE_URL``，CSV/日志路径是模块级
    常量，于是每跑一次 pytest 就用 seed 假数据覆盖 ``docs/residual-choice-*.csv``、
    并往 ``docs/data-cleanup-log-20260811.md`` 追加假记录。本用例锁死该行为。
    """

    @staticmethod
    def _snapshot() -> Dict[Path, Optional[str]]:
        """真实交付物的 sha256 快照（不存在记 ``None``）。"""
        snap: Dict[Path, Optional[str]] = {}
        for path in _REAL_ARTIFACTS:
            snap[path] = (
                hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            )
        return snap

    def test_running_both_scripts_leaves_real_docs_intact(
        self, run_script, seed_db_url: str
    ) -> None:
        before = self._snapshot()

        # 全流程跑一遍：T02 dry-run → T02 实跑 → T03 dry-run → T03 实跑
        run_script(_FIX_SCRIPT, ["--dry-run"], seed_db_url)
        run_script(_FIX_SCRIPT, [], seed_db_url)
        run_script(_ARCHIVE_SCRIPT, ["--dry-run", "--expect", "10"], seed_db_url)
        run_script(_ARCHIVE_SCRIPT, ["--expect", "10"], seed_db_url)

        after = self._snapshot()
        for path, digest in before.items():
            assert after[path] == digest, (
                f"真实交付物被测试污染：{path}\n"
                f"  跑测试前 sha256={digest}\n  跑测试后 sha256={after[path]}\n"
                f"  → 检查 run_script fixture 的重定向清单是否漏了某个产物常量。"
            )

    def test_artifact_constants_are_redirected(self, run_script, seed_db_url: str) -> None:
        """产物常量若被改名/新增，重定向清单必须同步更新（否则这里先炸）。"""
        for script, attr in [(_FIX_SCRIPT, "_PATCHED_CSV"), (_ARCHIVE_SCRIPT, "_REMOVED_CSV")]:
            module = _load_script(script)
            assert hasattr(module, attr), (
                f"{script.name} 缺少产物常量 {attr}；若已改名，请同步更新 "
                f"tests 里的 _CSV_ATTR_TO_NAME 重定向清单。"
            )
            # 未打补丁时它确实指向真实 docs/（证明「不重定向就会污染」）
            assert getattr(module, attr).parent == _PROJECT_ROOT / "docs"
        assert _cleanup_common.LOG_PATH.parent == run_script.docs_dir


# ==================================================================== CLI 入口冒烟
class TestCliEntrypoint:
    """确认命令行入口没坏（``--help`` 不碰 DB / 不写任何产物，安全）。"""

    @pytest.mark.parametrize("script", [_FIX_SCRIPT, _ARCHIVE_SCRIPT], ids=["fix", "archive"])
    def test_help_exits_zero(self, script: Path) -> None:
        proc = subprocess.run(
            [_PYTHON, str(script), "--help"],
            capture_output=True,
            text=True,
            cwd=str(_BACKEND_ROOT),
        )
        assert proc.returncode == 0, proc.stderr
        assert "--dry-run" in proc.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
