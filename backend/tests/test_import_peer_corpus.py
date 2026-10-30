# -*- coding: utf-8 -*-
"""A-2 种子语料导入脚本的回归测试：去重 + e2e（导入→放行→检索）。

通过真实调用 ``scripts/import_peer_corpus.py`` CLI（subprocess）来固化
运维流程行为，而非只测内部函数——这样「进容器跑同脚本」的链路被端到端守护。

覆盖：
  * dry-run 默认不落库；
  * --confirm 落库：干净→archived、命中红线→draft（0 例外）；
  * 去重：同 (kp_code,grade,sentence) 已存在则跳过，不翻倍；--no-dedup 可关；
  * 放行闸门：release-all 只放行 archived（draft 永远不进检索池）；
  * retrieve_released 真实返回已放行句（grounding 第 4 段接得住）。
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BACKEND = Path(__file__).resolve().parent.parent
SCRIPT = BACKEND / "scripts" / "import_peer_corpus.py"
MIGRATION = BACKEND / "migrations" / "026_peer_view_corpus_sqlite.sql"
CACHE = BACKEND / ".qa_cache"

# 一条干净可放行句 + 一条命中红线（self_accuse「你错了」）的句
VALID_ROW = "KP_CALC_ADD,2,购物,计算错,同学常把 23 加 18 算成 41。,seed,\n"
FORBIDDEN_ROW = "KP_UNIT_LENGTH_BASIC,3,距离,知识点缺,你错了！这题不该这样算！,seed,\n"
VALID_SENTENCE = "同学常把 23 加 18 算成 41。"


def _make_db(path: Path) -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    c = sqlite3.connect(str(path))
    try:
        c.executescript(sql)
        c.commit()
    finally:
        c.close()


def _run(args, db_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db_path), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _count(db_path: Path, status: str | None = None) -> int:
    c = sqlite3.connect(str(db_path))
    try:
        if status:
            return c.execute(
                "SELECT COUNT(*) FROM peer_view_corpus WHERE status=?", (status,)
            ).fetchone()[0]
        return c.execute("SELECT COUNT(*) FROM peer_view_corpus").fetchone()[0]
    finally:
        c.close()


def _write_csv(rows: list[str]) -> Path:
    CACHE.mkdir(exist_ok=True)
    fd, path = tempfile.mkstemp(dir=str(CACHE), suffix=".csv")
    os.close(fd)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write("kp_code,grade,scene,error_type,sentence,source,seed_batch\n")
        f.write("".join(rows))
    return Path(path)


@pytest.fixture
def fresh_db():
    CACHE.mkdir(exist_ok=True)
    fd, path = tempfile.mkstemp(dir=str(CACHE), suffix=".db")
    os.close(fd)
    _make_db(Path(path))
    try:
        yield Path(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_dry_run_writes_nothing(fresh_db):
    csv = _write_csv([VALID_ROW, FORBIDDEN_ROW])
    try:
        r = _run(["--csv", str(csv)], fresh_db)
        assert r.returncode == 0, r.stderr
        assert "[dry-run]" in r.stdout
        # 未落库
        assert _count(fresh_db) == 0
    finally:
        csv.unlink(missing_ok=True)


def test_confirm_writes_archived_and_forbidden_to_draft(fresh_db):
    csv = _write_csv([VALID_ROW, FORBIDDEN_ROW])
    try:
        r = _run(["--csv", str(csv), "--confirm"], fresh_db)
        assert r.returncode == 0, r.stderr
        assert _count(fresh_db) == 2
        assert _count(fresh_db, "archived") == 1   # 干净句
        assert _count(fresh_db, "draft") == 1      # 红线句拦截（0 例外）
    finally:
        csv.unlink(missing_ok=True)


def test_dedup_skips_existing_on_reimport(fresh_db):
    csv = _write_csv([VALID_ROW, FORBIDDEN_ROW])
    try:
        # 第 1 次落库
        r1 = _run(["--csv", str(csv), "--confirm"], fresh_db)
        assert r1.returncode == 0, r1.stderr
        assert _count(fresh_db) == 2

        # 第 2 次同文件（默认去重）应全跳过，不翻倍
        r2 = _run(["--csv", str(csv), "--confirm"], fresh_db)
        assert r2.returncode == 0, r2.stderr
        assert "去重跳过" in r2.stdout
        assert _count(fresh_db) == 2

        # --no-dedup 应禁用去重，重新插入 2 行
        r3 = _run(["--csv", str(csv), "--confirm", "--no-dedup"], fresh_db)
        assert r3.returncode == 0, r3.stderr
        assert _count(fresh_db) == 4
    finally:
        csv.unlink(missing_ok=True)


def test_release_then_retrieve_released_e2e(fresh_db):
    csv = _write_csv([VALID_ROW, FORBIDDEN_ROW])
    try:
        # 1) 落库（1 archived + 1 draft）
        _run(["--csv", str(csv), "--confirm"], fresh_db)
        assert _count(fresh_db, "archived") == 1
        assert _count(fresh_db, "draft") == 1

        # 2) 整批放行（只放行 archived，draft 不进检索池）
        rr = _run(["--release-all", "--reviewer", "tester"], fresh_db)
        assert rr.returncode == 0, rr.stderr
        assert "已放行" in rr.stdout
        assert _count(fresh_db, "released") == 1
        assert _count(fresh_db, "draft") == 1  # draft 仍留痕未放行

        # 3) 检索只取 released（grounding 第 4 段依赖）
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        import app.services.peer_corpus_service as svc

        eng = create_engine(f"sqlite:///{fresh_db.resolve()}")
        SM = sessionmaker(bind=eng)
        with SM() as db:
            got = svc.retrieve_released(db, kp_code="KP_CALC_ADD", max_grade=6)
        assert got == [VALID_SENTENCE]
    finally:
        csv.unlink(missing_ok=True)
