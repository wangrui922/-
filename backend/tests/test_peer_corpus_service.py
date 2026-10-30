# -*- coding: utf-8 -*-
"""A-2 · 同伴视角语料库（``peer_view_corpus`` / 026 迁移）服务层测试。

护栏重点（对齐规格-同伴视角语料库-20261029.md + 工程铁律）：
    1. **禁止项机器可扫**：``forbidden_scan`` 对每条红线都能稳定命中（手算 golden）；
    2. **入库即过滤**：干净句 → ``archived``（待人工抽检）；命中红线 → ``draft``（拦截，
       留痕 ``forbidden_scan``）；
    3. **未放行 0 例外**：``retrieve_released`` 只 SELECT ``status='released'``，
       draft / archived / rejected 行**天然取不到**（变异自证）；
    4. **年级窗口**：``max_grade`` 上限外的行取不到；
    5. **错因 / 场景**优先匹配但不强拦；
    6. **迁移文件可用**：真实 026 迁移 SQL 能在空库上幂等建表。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.services import peer_corpus_service as svc

MIGRATION_FILE = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "026_peer_view_corpus_sqlite.sql"
)

CLEAN_SENTENCE = "好多同学都会把余数算错"          # 11 字，全绿
KP = "KP_CALC_DIV_REMAIN"                          # 任意合法 kp_code（仅字符串校验）


# ================================================================== 工具
def _migration_sql() -> str:
    return MIGRATION_FILE.read_text(encoding="utf-8")


def _make_db() -> object:
    """造独立内存库（与共享测试库隔离，计数确定），已建好 peer_view_corpus。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    raw = engine.raw_connection()
    try:
        raw.executescript(_migration_sql())  # 真实迁移文件 → 同时证明迁移可用
        raw.commit()
    finally:
        raw.close()
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    return maker()


# ================================================================== 1. 禁止项扫描（手算 golden）
def test_forbidden_scan_too_long():
    s = "一" * 41
    assert svc.forbidden_scan(s) == ["too_long"]


def test_forbidden_scan_latex():
    assert svc.forbidden_scan(r"用\frac{1}{2}算一半") == ["latex"]
    assert svc.forbidden_scan(r"根号用\sqrt{3}这样") == ["latex"]
    assert svc.forbidden_scan("金额 $5 元") == ["latex"]


def test_forbidden_scan_self_ref():
    assert svc.forbidden_scan("老师说这题很简单") == ["self_ref"]
    assert svc.forbidden_scan("客服同学也常错") == ["self_ref"]
    assert svc.forbidden_scan("AI 也会算错") == ["self_ref"]


def test_forbidden_scan_self_accuse():
    assert svc.forbidden_scan("你错了就别做了") == ["self_accuse"]


def test_forbidden_scan_derogatory():
    assert svc.forbidden_scan("这么简单还笨") == ["derogatory"]


def test_forbidden_scan_grade_too_high():
    assert svc.forbidden_scan("二次函数要配方法", grade=7) == ["grade_too_high"]
    # 不传 grade 或 grade<=6 不报
    assert svc.forbidden_scan("二次函数要配方法") == []
    assert svc.forbidden_scan("配方法", grade=6) == []


def test_forbidden_scan_multi_exclaim():
    assert svc.forbidden_scan("快做！！") == ["multi_exclaim"]
    assert svc.forbidden_scan("快做!!") == ["multi_exclaim"]
    assert svc.forbidden_scan("快做！快做！") == ["multi_exclaim"]
    assert svc.forbidden_scan("慢慢想就好") == []


def test_forbidden_scan_clean_is_empty():
    assert svc.forbidden_scan(CLEAN_SENTENCE) == []
    assert svc.forbidden_scan(CLEAN_SENTENCE, grade=3) == []


# ================================================================== 2. 入库即过滤
def test_ingest_clean_goes_to_archived():
    db = _make_db()
    res = svc.ingest_sentence(db, kp_code=KP, grade=3, sentence=CLEAN_SENTENCE,
                              scene="购物", error_type="计算错")
    db.commit()
    assert res["status"] == "archived"
    assert res["violations"] == []
    # 落库确认
    row = db.execute(
        text("SELECT status, forbidden_scan FROM peer_view_corpus WHERE id=:i"),
        {"i": res["id"]},
    ).fetchone()
    assert row[0] == "archived"
    assert row[1] == "[]"


def test_ingest_violation_goes_to_draft_with_scan():
    db = _make_db()
    bad = "你错了，这道题好难！！"
    res = svc.ingest_sentence(db, kp_code=KP, grade=3, sentence=bad)
    db.commit()
    assert res["status"] == "draft"
    assert set(res["violations"]) == {"self_accuse", "multi_exclaim"}
    row = db.execute(
        text("SELECT forbidden_scan FROM peer_view_corpus WHERE id=:i"),
        {"i": res["id"]},
    ).fetchone()
    assert "self_accuse" in row[0] and "multi_exclaim" in row[0]


def test_ingest_validates_params():
    db = _make_db()
    with pytest.raises(ValueError):
        svc.ingest_sentence(db, kp_code="", grade=3, sentence=CLEAN_SENTENCE)
    with pytest.raises(ValueError):
        svc.ingest_sentence(db, kp_code=KP, grade=9, sentence=CLEAN_SENTENCE)
    with pytest.raises(ValueError):
        svc.ingest_sentence(db, kp_code=KP, grade=3, sentence=CLEAN_SENTENCE,
                            error_type="不存在的错因")


# ================================================================== 3. 人工放行闸门 + 0 例外
def test_release_then_retrieve():
    db = _make_db()
    res = svc.ingest_sentence(db, kp_code=KP, grade=3, sentence=CLEAN_SENTENCE,
                              scene="购物", error_type="计算错")
    db.commit()
    assert svc.retrieve_released(db, kp_code=KP, max_grade=6) == []  # archived 取不到

    ok = svc.release(db, res["id"], "reviewer-1")
    db.commit()
    assert ok is True

    hits = svc.retrieve_released(db, kp_code=KP, max_grade=6)
    assert hits == [CLEAN_SENTENCE]


def test_draft_never_released_zero_exception():
    """变异自证：draft 行**绝不能**进入检索池（即使同 kp_code / 同年级）。"""
    db = _make_db()
    # 一条已放行
    good = svc.ingest_sentence(db, kp_code=KP, grade=3, sentence=CLEAN_SENTENCE)
    db.commit()
    svc.release(db, good["id"], "r")
    db.commit()
    # 一条命中红线被拦截（draft）
    bad = svc.ingest_sentence(db, kp_code=KP, grade=3, sentence="你错了！！")
    db.commit()
    assert bad["status"] == "draft"

    hits = svc.retrieve_released(db, kp_code=KP, max_grade=6)
    assert hits == [CLEAN_SENTENCE]          # 只取到了 released
    assert "你错了" not in "".join(hits)     # draft 不在结果里


def test_rejected_never_released():
    db = _make_db()
    res = svc.ingest_sentence(db, kp_code=KP, grade=4, sentence="同学习惯先算乘法")
    db.commit()
    svc.reject(db, res["id"], "r")
    db.commit()
    assert svc.retrieve_released(db, kp_code=KP, max_grade=6) == []


def test_release_unknown_id_is_false():
    db = _make_db()
    assert svc.release(db, "nonexistent-id", "r") is False


# ================================================================== 4. 年级窗口
def test_grade_window_filters_above_max():
    db = _make_db()
    res = svc.ingest_sentence(db, kp_code=KP, grade=5, sentence=CLEAN_SENTENCE)
    db.commit()
    svc.release(db, res["id"], "r")
    db.commit()
    # max_grade=4 不含 grade=5 → 取不到
    assert svc.retrieve_released(db, kp_code=KP, max_grade=4) == []
    # max_grade=5 含 → 取到
    assert svc.retrieve_released(db, kp_code=KP, max_grade=5) == [CLEAN_SENTENCE]
    # 不传年级上限（默认封顶 6）→ 取到
    assert svc.retrieve_released(db, kp_code=KP, max_grade=6) == [CLEAN_SENTENCE]


# ================================================================== 5. 错因 / 场景优先匹配但不强拦
def test_error_type_prefers_match_but_not_mandatory():
    db = _make_db()
    a = svc.ingest_sentence(db, kp_code=KP, grade=3, sentence="先算括号里的",
                            error_type="方法错")
    b = svc.ingest_sentence(db, kp_code=KP, grade=3, sentence="余数别忘了写",
                            error_type="计算错")
    db.commit()
    svc.release(db, a["id"], "r")
    svc.release(db, b["id"], "r")
    db.commit()
    # 指定 error_type=方法错 → 只返回方法错那句
    hits = svc.retrieve_released(db, kp_code=KP, max_grade=6, error_type="方法错")
    assert hits == ["先算括号里的"]
    # 不指定 → 两条都可能（ORDER BY RANDOM），但都是本表已放行句
    hits_all = svc.retrieve_released(db, kp_code=KP, max_grade=6)
    assert set(hits_all) <= {"先算括号里的", "余数别忘了写"}


# ================================================================== 6. 计数 / 覆盖
def test_count_and_coverage():
    db = _make_db()
    for g in (1, 2, 3):
        r = svc.ingest_sentence(db, kp_code=KP, grade=g, sentence=f"同伴句{g}")
        db.commit()
        svc.release(db, r["id"], "r")
    db.commit()
    assert svc.count_released(db) == 3
    stats = svc.coverage_stats(db)
    assert stats["released"] == 3
    assert stats["kp_count"] == 1


def test_retrieve_released_fail_safe_on_bad_args():
    db = _make_db()
    assert svc.retrieve_released(db, kp_code="", max_grade=6) == []
    assert svc.retrieve_released(db, kp_code=None, max_grade=6) == []
