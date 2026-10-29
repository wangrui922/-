# -*- coding: utf-8 -*-
"""``engines.gap_ranking`` 的 N1 golden 真值守卫。

约定（与 ``tests/engines/test_priority_golden.py`` 一致）：
**期望值全部手工推导，不调用被测实现**——本文件里的名次、排序键、理由文本
都是按「错因命中数 > 重要度 > 难度 > 零题量时长」的字典序人工排出来的。

真值数据集（11 个候选）
------------------------
===========  ========  ====  ====  ========  ===  ===  ==========================
id           学段      imp  diff  零题量天  题量  err  说明
===========  ========  ====  ====  ========  ===  ===  ==========================
``kp-a``     primary   5    3     100       0    0    与 kp-e 完全同分 → 按 id 升序
``kp-b``     primary   5    5     10        0    0    难度更高 → 排在 kp-a 前
``kp-c``     primary   4    3     200       0    0    重要度低一档
``kp-d``     primary   5    3     100       0    **2**  错因命中最多 → **第 1**
``kp-e``     primary   5    3     100       0    0    与 kp-a 同分，id 更大
``kp-f``     junior    5    5     999       0    0    初中段 → 暂缓
``kp-g``     senior    5    5     500       0    0    HS_ 前缀 → 暂缓
``kp-h``     primary   5    3     100       12   0    已有题量 → 排除
``kp-i``     primary   3    1     5         0    0    年级 NULL，KP_ 前缀兜底
``kp-j``     unknown   2    1     1         0    0    学段识别不出 → 暂缓
``kp-k``     primary   4    5     999       0    **1**  错因命中 1 次 → **第 2**
===========  ========  ====  ====  ========  ===  ===  ==========================

手工推演的最终名次::

    1. kp-d   错因 2（压倒一切）
    2. kp-k   错因 1（压过 imp5 / diff5）
    3. kp-b   err0 imp5 diff5
    4. kp-a   err0 imp5 diff3 z100（id < kp-e）
    5. kp-e   err0 imp5 diff3 z100（id > kp-a）
    6. kp-c   err0 imp4
    7. kp-i   err0 imp3
    ---
    暂缓：kp-f（初中）、kp-g（高中）、kp-j（学段未识别）
    排除：kp-h（已有 12 道题）
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from typing import Tuple

import pytest

from app.engines.gap_ranking import (
    ENABLED_FACTOR_KEYS,
    FACTOR_CHAIN,
    GapCandidate,
    band_of_candidate,
    candidate_from_mapping,
    grade_band_of,
    rank_gaps,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


# ============================================================ 真值数据集
def _candidate(
    kp_id: str,
    *,
    code: str = "",
    name: str = "",
    grade: int | None = None,
    importance: int = 0,
    difficulty: int = 0,
    question_count: int = 0,
    zero_duration_days: int = 0,
    error_hit_count: int = 0,
) -> GapCandidate:
    """构造一个候选（测试侧的小工厂，便于一眼看清每个字段）。"""
    return GapCandidate(
        knowledge_point_id=kp_id,
        kp_code=code or f"KP_{kp_id.upper()}",
        kp_name=name or kp_id,
        grade=grade,
        importance=importance,
        difficulty=difficulty,
        question_count=question_count,
        zero_duration_days=zero_duration_days,
        error_hit_count=error_hit_count,
    )


#: 11 个候选的真值数据集
CANDIDATES: Tuple[GapCandidate, ...] = (
    _candidate("kp-a", grade=3, importance=5, difficulty=3, zero_duration_days=100),
    _candidate("kp-b", grade=3, importance=5, difficulty=5, zero_duration_days=10),
    _candidate("kp-c", grade=4, importance=4, difficulty=3, zero_duration_days=200),
    _candidate(
        "kp-d", grade=3, importance=5, difficulty=3, zero_duration_days=100,
        error_hit_count=2,
    ),
    _candidate("kp-e", grade=4, importance=5, difficulty=3, zero_duration_days=100),
    _candidate("kp-f", grade=7, importance=5, difficulty=5, zero_duration_days=999),
    _candidate(
        "kp-g", code="HS_ABC", grade=None, importance=5, difficulty=5,
        zero_duration_days=500,
    ),
    _candidate(
        "kp-h", grade=3, importance=5, difficulty=3, zero_duration_days=100,
        question_count=12,
    ),
    _candidate(
        "kp-i", code="KP_ONLY", grade=None, importance=3, difficulty=1,
        zero_duration_days=5,
    ),
    _candidate("kp-j", code="XX_MYSTERY", grade=None, importance=2, difficulty=1),
    _candidate(
        "kp-k", grade=5, importance=4, difficulty=5, zero_duration_days=999,
        error_hit_count=1,
    ),
)

#: 手工推演的补题队列名次
EXPECTED_RANKED: Tuple[str, ...] = ("kp-d", "kp-k", "kp-b", "kp-a", "kp-e", "kp-c", "kp-i")
#: 手工推演的暂缓条目（按 sort_key 排序）
EXPECTED_DEFERRED: Tuple[str, ...] = ("kp-f", "kp-g", "kp-j")
#: 手工推演的排除条目（已有题量）
EXPECTED_EXCLUDED: Tuple[str, ...] = ("kp-h",)


@pytest.fixture()
def ranking():
    """跑一次排序（被测实现）。"""
    return rank_gaps(CANDIDATES)


# ============================================================ 结构真值
def test_factor_chain_shape_golden():
    """因子链结构写死：4 个生效 + 1 个停用的接口位。"""
    assert len(FACTOR_CHAIN) == 5
    assert ENABLED_FACTOR_KEYS == (
        "error_hit_count",
        "importance",
        "difficulty",
        "zero_duration_days",
    )
    by_key = {factor.key: factor for factor in FACTOR_CHAIN}
    assert set(by_key) == {
        "error_hit_count",
        "importance",
        "difficulty",
        "zero_duration_days",
        "prerequisite_count",
    }
    # 次序即优先级
    assert by_key["error_hit_count"].order == 1
    assert by_key["importance"].order == 2
    assert by_key["difficulty"].order == 3
    assert by_key["zero_duration_days"].order == 4


def test_prerequisite_factor_is_disabled_with_explanation():
    """「前置被依赖」必须停用，且必须写清为什么停用。"""
    prerequisite = next(
        factor for factor in FACTOR_CHAIN if factor.key == "prerequisite_count"
    )
    assert prerequisite.enabled is False
    assert "knowledge_relations" in prerequisite.note
    assert "knowledge_prerequisite" in prerequisite.note
    # 停用的因子不得出现在生效链里
    assert "prerequisite_count" not in ENABLED_FACTOR_KEYS


def test_error_hit_factor_exists_even_though_always_zero():
    """错因命中数因子必须存在（本轮数据恒为 0，但因子位要留着）。"""
    factor = next(f for f in FACTOR_CHAIN if f.key == "error_hit_count")
    assert factor.enabled is True
    assert factor.order == 1


# ============================================================ 排序真值
def test_ranked_order_golden(ranking):
    """名次逐个写死（手工推演，见模块 docstring）。"""
    assert tuple(item.knowledge_point_id for item in ranking.ranked) == EXPECTED_RANKED
    assert [item.rank for item in ranking.ranked] == [1, 2, 3, 4, 5, 6, 7]


def test_deferred_and_excluded_golden(ranking):
    """暂缓与排除各自写死，且三段合起来 = 入参条数（一条都不丢）。"""
    assert tuple(item.knowledge_point_id for item in ranking.deferred) == EXPECTED_DEFERRED
    assert tuple(item.knowledge_point_id for item in ranking.excluded) == EXPECTED_EXCLUDED
    assert ranking.total == len(CANDIDATES) == 11
    for item in ranking.deferred:
        assert item.rank is None
        assert item.deferred is True
        assert item.deferred_reason


def test_error_hit_count_dominates_importance_and_difficulty(ranking):
    """错因命中数压倒重要度与难度：kp-d(imp5,diff3) 排在 kp-k(imp4,diff5) 前。"""
    ids = [item.knowledge_point_id for item in ranking.ranked]
    assert ids.index("kp-d") < ids.index("kp-k")
    assert ids.index("kp-k") < ids.index("kp-b")


def test_sort_key_golden(ranking):
    """排序键逐个写死（降序因子取负 + id 升序稳定收尾）。"""
    by_id = {item.knowledge_point_id: item for item in ranking.ranked}
    assert by_id["kp-d"].sort_key == (-2, -5, -3, -100, "kp-d")
    assert by_id["kp-k"].sort_key == (-1, -4, -5, -999, "kp-k")
    assert by_id["kp-b"].sort_key == (0, -5, -5, -10, "kp-b")
    assert by_id["kp-a"].sort_key == (0, -5, -3, -100, "kp-a")
    assert by_id["kp-e"].sort_key == (0, -5, -3, -100, "kp-e")
    assert by_id["kp-c"].sort_key == (0, -4, -3, -200, "kp-c")
    assert by_id["kp-i"].sort_key == (0, -3, -1, -5, "kp-i")


def test_tie_break_is_by_id_ascending(ranking):
    """完全同分（kp-a / kp-e）按 id 升序，保证结果稳定可复现。"""
    ids = [item.knowledge_point_id for item in ranking.ranked]
    assert ids.index("kp-a") < ids.index("kp-e")


def test_reason_text_golden(ranking):
    """中文理由文本写死（只含**生效**因子，顺序按因子链）。"""
    by_id = {item.knowledge_point_id: item for item in ranking.ranked}
    assert by_id["kp-d"].reason == "错因命中数 2；重要度 5；难度 3；零题量时长 100"
    assert by_id["kp-b"].reason == "错因命中数 0；重要度 5；难度 5；零题量时长 10"
    # 停用因子不得出现在理由里
    assert "前置被依赖" not in by_id["kp-d"].reason


def test_factor_snapshot_includes_disabled_factor(ranking):
    """因子快照里要保留停用因子的值（报告留痕：这一维有值但没参与排序）。"""
    by_id = {item.knowledge_point_id: item for item in ranking.ranked}
    assert by_id["kp-d"].factors == {
        "error_hit_count": 2,
        "importance": 5,
        "difficulty": 3,
        "zero_duration_days": 100,
        "prerequisite_count": 0,
    }


# ============================================================ 学段真值
def test_grade_band_golden():
    """年级 → 学段：口径是「升入年级」，**不做 grade-1** 换算。"""
    assert grade_band_of(1) == "primary"
    assert grade_band_of(6) == "primary"
    assert grade_band_of(7) == "junior"
    assert grade_band_of(9) == "junior"
    assert grade_band_of(10) == "senior"
    assert grade_band_of(12) == "senior"
    assert grade_band_of(None) == "unknown"
    assert grade_band_of(0) == "unknown"


def test_band_fallback_to_code_prefix_golden():
    """年级为 NULL 时用 code 前缀兜底（CZ_ / HS_ / KP_）。"""
    by_id = {item.knowledge_point_id: item for item in CANDIDATES}
    assert band_of_candidate(by_id["kp-i"]) == "primary"  # KP_ 前缀
    assert band_of_candidate(by_id["kp-g"]) == "senior"  # HS_ 前缀
    assert band_of_candidate(by_id["kp-j"]) == "unknown"  # 前缀也认不出
    assert band_of_candidate(by_id["kp-f"]) == "junior"  # 年级优先（7 → 初中）


def test_junior_and_senior_are_deferred_not_dropped(ranking):
    """初中 / 高中段标记暂缓，但**不丢弃**。"""
    deferred_ids = {item.knowledge_point_id for item in ranking.deferred}
    assert {"kp-f", "kp-g", "kp-j"} <= deferred_ids
    reasons = {item.knowledge_point_id: item.deferred_reason for item in ranking.deferred}
    assert "初中段" in reasons["kp-f"]
    assert "高中段" in reasons["kp-g"]


# ============================================================ 开关真值
def test_only_zero_question_switch_golden():
    """``only_zero_question=False`` 时已有题量的 kp-h 也进队列。"""
    ranking = rank_gaps(CANDIDATES, only_zero_question=False)
    ids = [item.knowledge_point_id for item in ranking.ranked]
    assert "kp-h" in ids
    assert ranking.excluded == ()
    # kp-h 是 err0 imp5 diff3 z100 → 与 kp-a/kp-e 同分；id 升序（kp-a < kp-e < kp-h）
    ids.index("kp-a") < ids.index("kp-h")


def test_empty_input_golden():
    """空输入 → 空结果，不抛异常。"""
    ranking = rank_gaps([])
    assert ranking.ranked == ()
    assert ranking.deferred == ()
    assert ranking.excluded == ()
    assert ranking.total == 0


def test_all_deferred_input_golden():
    """全是初中段 → 队列为空，但条目一条不少。"""
    ranking = rank_gaps(
        [
            _candidate("x-1", grade=8, importance=5),
            _candidate("x-2", grade=8, importance=4),
        ]
    )
    assert ranking.ranked == ()
    assert len(ranking.deferred) == 2


# ============================================================ 适配器真值
def test_candidate_from_mapping_golden():
    """字典 → 数据类适配：字段别名与缺省值都写死。"""
    candidate = candidate_from_mapping(
        {
            "kp_id": "kp-001",
            "code": "KP_CALC_MUL",
            "name": "乘除法",
            "grade": 3,
            "importance": 5,
            "difficulty": 1,
            "question_count": 331,
            "zero_duration_days": 0,
        }
    )
    assert candidate.knowledge_point_id == "kp-001"
    assert candidate.kp_code == "KP_CALC_MUL"
    assert candidate.kp_name == "乘除法"
    assert candidate.grade == 3
    assert candidate.importance == 5
    assert candidate.difficulty == 1
    assert candidate.question_count == 331
    assert candidate.error_hit_count == 0
    assert candidate.prerequisite_count == 0
    assert candidate.band == "primary"


def test_candidate_from_mapping_handles_missing_and_bad_values():
    """缺省 / 脏值必须兜底，不能抛异常。"""
    candidate = candidate_from_mapping({"id": "kp-002", "grade": "not-a-number"})
    assert candidate.knowledge_point_id == "kp-002"
    assert candidate.grade is None
    assert candidate.importance == 0
    assert candidate.band == "unknown"


# ============================================================ 纯层守卫
def test_gap_ranking_module_is_pure():
    """engines 纯层铁律：不得 import sqlalchemy / 读 env（AST 判定 + 干净解释器自证）。"""
    module_path = BACKEND_DIR / "app" / "engines" / "gap_ranking.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "dataclasses", "typing"}, imported
    assert "sqlalchemy" not in imported
    assert "os" not in imported

    probe = (
        "import sys; sys.path.insert(0, %r);"
        "import app.engines.gap_ranking;"
        "print('sqlalchemy' in sys.modules)" % str(BACKEND_DIR)
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_DIR),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "False"


def test_gap_ranking_does_not_touch_config_specs():
    """阈值不得塞进 CONFIG_SPECS（31 键 golden 守卫）。"""
    from app.engines.config import CONFIG_SPECS

    assert len(CONFIG_SPECS) == 31
    keys = {spec.key for spec in CONFIG_SPECS}
    # 本轮不得新增任何与缺口排序 / 场景回填相关的配置键
    assert not any(
        marker in key for key in keys for marker in ("gap_ranking", "backfill", "scenario")
    )
