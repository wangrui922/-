"""#70 真实试卷入库 (exam_yunnan_2025) — 双不变式闸门回归。

锁死:
- 批次 A (云南中考真题) ship_ready=1 + source_tag=green
- 所有选择题过 classify_question(clean) + validate_choice_options + is_l1_safe
- 27 道题入库齐全(15 选择 + 4 填空 + 8 解答)

取数口径（T15 收尾修复，勿改）
-----------------------------
* **只读断言走裸 sqlite3 直连 dev.db**：conftest 把测试会话的
  ``DATABASE_URL`` 默认设成 ``:memory:``（团队铁律：回归不碰文件库），
  app 会话查的是空内存库；本组要验证的是**冷母库 dev.db 里的批次数据**，
  只能直连文件。这些查询全部只读，不写 dev.db。
* **幂等断言走临时副本 + 子进程导入**：早期版本在测试进程内调 ``run()``，
  ``run()`` 写 ``:memory:`` 而断言读 dev.db——两边分叉，before/after 恒等，
  **幂等性从未被真正验证**（假绿）。现在把 dev.db 用备份 API 拷成临时库
  （禁 ``cp``：源库带 WAL，直接拷会出 malformed 快照），子进程以
  ``DATABASE_URL=临时库`` 跑导入，连跑两次验证 upsert 零新增。真 dev.db
  一个字节都不碰。
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.importers.quality_gate import (
    classify_question,
    validate_choice_options,
)
from app.services.judge_engine import is_l1_safe

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT
DEV_DB = ROOT / "dev.db"
SEED_FILE = ROOT / "seed" / "exam_yunnan_2025.json"

#: dev.db 是否在本机可用（gitignored，CI / 新 worktree 里可能没有）。
#: T15 实测：同一 commit 只差库在不在，全量从 963 passed/2 failed 变成
#: 947 passed/7 failed——多出的 5 条 FAILED 就是本组，环境差异伪装成新回归，
#: 是最难排查的假阳性，所以库不在时整组显式 skip（与 test_schema_guard 同款口径）。
_DEV_DB_EXISTS: bool = DEV_DB.is_file()
_SKIP_NO_DEV_DB = f"真实题库 dev.db 不存在，跳过 #70 试卷入库闸门：{DEV_DB}"

pytestmark = pytest.mark.skipif(not _DEV_DB_EXISTS, reason=_SKIP_NO_DEV_DB)


def _conn():
    return sqlite3.connect(str(DEV_DB))


def _copy_db_to_temp(tmp_path: Path) -> Path:
    """用 sqlite3 备份 API 把 dev.db 拷成临时库（WAL 安全，禁 cp）。"""
    target = tmp_path / "exam_idem.db"
    source = sqlite3.connect(str(DEV_DB))
    try:
        destination = sqlite3.connect(str(target))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return target


def _run_import_subprocess(db_path: Path) -> None:
    """子进程跑导入，``DATABASE_URL`` 指向临时库（与断言同一真源）。"""
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    env.pop("ALLOW_FILE_DB", None)
    proc = subprocess.run(
        [sys.executable, "-m", "app.scripts.import_exam_pdf", str(SEED_FILE)],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"导入子进程失败:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert "OK=27" in proc.stdout, f"导入统计异常: {proc.stdout}"


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

    def test_question_idempotency(self, tmp_path: Path):
        """同一 JSON 重跑应零新增(uuid5 派生 id 幂等)——临时副本上连跑两次。

        为什么必须子进程 + 临时库：测试进程的 app 会话被 conftest 指向
        ``:memory:``，进程内调 ``run()`` 写的是抛掷型内存库，与 dev.db
        分叉，before/after 恒等——那种写法证明不了任何事。这里让导入
        与断言打同一个临时库，且真 dev.db 不被触碰。
        """
        temp_db = _copy_db_to_temp(tmp_path)

        def _count() -> int:
            c = sqlite3.connect(str(temp_db))
            try:
                return c.execute(
                    "SELECT count(*) FROM questions WHERE source_doc='exam_yunnan_2025'"
                ).fetchone()[0]
            finally:
                c.close()

        before = _count()
        _run_import_subprocess(temp_db)
        after_first = _count()
        _run_import_subprocess(temp_db)
        after_second = _count()

        assert before == 27, f"临时库初始应含 27 题, 实为 {before}"
        assert after_first == 27, f"首次重入应幂等: 27 → {after_first}"
        assert after_second == 27, f"二次重入应幂等: 27 → {after_second}"

        c = sqlite3.connect(str(temp_db))
        try:
            dupes = c.execute(
                "SELECT count(*) FROM (SELECT id FROM questions "
                "WHERE source_doc='exam_yunnan_2025' GROUP BY id HAVING count(*) > 1)"
            ).fetchone()[0]
        finally:
            c.close()
        assert dupes == 0, f"uuid5 幂等失效: {dupes} 个重复 id"
