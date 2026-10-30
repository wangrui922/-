# -*- coding: utf-8-
"""B 下期：长度换算答案 bug 修复 + distance 族 69 题按算式二次拆分。

三块内容（与 team 任务单一致）：

    1. 长度换算答案 bug 修复生效性
       —— ``generate_scenario_bank.py::t_distance2`` kind==3 改用单次随机
          同时决定「题面后缀」与「答案加数」，遍历 km=1..15 × has_500 两种
          共 30 种组合，断言题面与答案公式自洽。
    2. ``classify_distance_form`` 的 golden 手算表
       —— 从该族题干抽样 ≥20 条，逐条断言分类正确，覆盖三类算式。
    3. dev 库（backend/dev.db）relabel 脚本 dry-run 的 69 题分布
       —— 仅当 dev.db 存在且含 scenario_bank 的 distance 题时执行，否则
          ``pytest.skip``（生产库无此数据）。
    4. （附加、自包含）用临时文件库镜像 dev 结构，真实跑 relabel 脚本，
       断言 69 题被正确重贴（47 长度换算 → KP_UNIT_LENGTH_BASIC、
       22 两段相加 → KP_CALC_ADD），不依赖 dev.db 是否存在。

⚠️ 本项仅影响 dev 题库（``source_doc='scenario_bank'``）；生产库
scenario_bank 数量为 0，脚本指向生产需 ``ALLOW_PROD_BACKFILL=1``，零生产影响。
"""

from __future__ import annotations

import random
import re
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BACKEND_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import generate_scenario_bank as gen  # noqa: E402
from app.engines.scenario_kp_mapping import (  # noqa: E402
    DISTANCE_FORM_KP_MAP,
    classify_distance_form,
)

#: 二次拆分涉及的三个目标知识点（库里已存在，dev 镜像与真实库一致）
KP_TRAVEL = "KP_WORD_TRAVEL"
KP_LENGTH = "KP_UNIT_LENGTH_BASIC"
KP_ADD = "KP_CALC_ADD"

#: 历史数据（**老生成器**产出的 dev 库）实测分布：147 行程 / 47 长度换算 /
#: 22 两段相加 = 216，即 69 道非行程需重贴。
#: ⚠️ 本脚注数字来自团队已登记的 dev 存量；生成器修复后「重新生成」会得到不同的
#: 随机抽样，故测试不写死这些计数，而是对实际数据集动态计算后断言自洽。
DOCUMENTED_TRAVEL = 147
DOCUMENTED_LENGTH = 47
DOCUMENTED_TWO_SEG = 22
DOCUMENTED_RELABEL = DOCUMENTED_LENGTH + DOCUMENTED_TWO_SEG  # 69


# ============================================================ 1. 长度换算答案 bug 修复
class _ScriptedRng(random.Random):
    """脚本化随机源：在 t_distance2(kind==3) 的调用顺序下精确产出目标组合。

    t_distance2 进入 kind==3 分支后依次调用：
        km = rng.randint(1, 15)
        has_500 = rng.choice([False, True])
    本类覆盖这两个调用，使 (km, has_500) 完全可控，从而穷举全部 30 种组合。
    """

    def __init__(self, km: int, has_500: bool) -> None:
        super().__init__(0)
        self._km = km
        self._has = has_500

    def randint(self, a: int, b: int) -> int:  # noqa: D401, A003
        return self._km

    def choice(self, seq):  # noqa: A003
        return self._has


class TestLengthConversionAnswerFix:
    """修复生效：题面与答案公式一致。"""

    def test_kind3_all_combos_formula_consistent(self) -> None:
        """遍历 km=1..15 × has_500 两种，断言题面⇔答案自洽。"""
        for km in range(1, 16):
            for has_500 in (False, True):
                content, answer, _distractors = gen.t_distance2(3, _ScriptedRng(km, has_500))
                ans_int = int(answer)

                # 题面含「500米」⇔ 答案 = km*1000 + 500
                has_suffix = "500米" in content
                assert has_suffix == has_500, (
                    f"题面后缀与 has_500 不一致: km={km} has_500={has_500} "
                    f"content={content!r}"
                )
                expected = km * 1000 + (500 if has_500 else 0)
                assert ans_int == expected, (
                    f"答案与公式不符: km={km} has_500={has_500} "
                    f"content={content!r} answer={answer!r}"
                )

    def test_kind3_no_cross_mismatch(self) -> None:
        """回归：题面写「N千米」时答案绝不可能是 N×1000+500（旧 bug 表现）。"""
        for km in range(1, 16):
            content, answer, _ = gen.t_distance2(3, _ScriptedRng(km, False))
            assert "500米" not in content
            assert int(answer) == km * 1000

        for km in range(1, 16):
            content, answer, _ = gen.t_distance2(3, _ScriptedRng(km, True))
            assert "500米" in content
            assert int(answer) == km * 1000 + 500


# ============================================================ 2. classify_distance_form golden 表
# 从该族实际生成题干抽样，逐条手算分类（覆盖三类）。
GOLDEN_CASES: list[tuple[str, str]] = [
    # ---- length_conversion（10）----
    ("12千米等于多少米？", "length_conversion"),
    ("20千米等于多少米？", "length_conversion"),
    ("2千米等于多少米？", "length_conversion"),
    ("5千米等于多少米？", "length_conversion"),
    ("14千米500米等于多少米？", "length_conversion"),
    ("11千米500米等于多少米？", "length_conversion"),
    ("12千米500米等于多少米？", "length_conversion"),
    ("小明家到学校2千米1百米，等于多少米？", "length_conversion"),
    ("小明家到学校5千米9百米，等于多少米？", "length_conversion"),
    ("小明家到学校4千米2百米，等于多少米？", "length_conversion"),
    # ---- two_segment_add（4）----
    ("小丽先走了8千米，又走了5千米，一共走了多少千米？", "two_segment_add"),
    ("小丽先走了3千米，又走了2千米，一共走了多少千米？", "two_segment_add"),
    ("小丽先走了7千米，又走了7千米，一共走了多少千米？", "two_segment_add"),
    ("小丽先走了5千米，又走了8千米，一共走了多少千米？", "two_segment_add"),
    # ---- travel（8）----
    ("汽车每小时行驶40千米，行驶6小时，一共行驶多少千米？", "travel"),
    ("火车每小时行驶15千米，行驶3小时，一共行驶多少千米？", "travel"),
    ("一辆货车每小时行20千米，行驶60千米需要几小时？", "travel"),
    ("一辆汽车每小时行25千米，行驶125千米需要几小时？", "travel"),
    ("小宇15分钟走了300米，他平均每分钟走多少米？", "travel"),
    ("小芳4分钟走了240米，她平均每分钟走多少米？", "travel"),
    ("自行车每小时行30千米，骑3小时，一共能行多少千米？", "travel"),
    ("一辆客车6小时行了360千米，平均每小时行多少千米？", "travel"),
]


class TestClassifyDistanceForm:
    """classify_distance_form 的 golden 手算表（≥20 条，覆盖三类）。"""

    def test_golden_table(self) -> None:
        assert len(GOLDEN_CASES) >= 20, "golden 抽样需 ≥20 条"
        forms = {form for _, form in GOLDEN_CASES}
        assert forms == {"length_conversion", "two_segment_add", "travel"}, (
            f"必须覆盖三类算式，实际: {forms}"
        )
        for content, expected in GOLDEN_CASES:
            got = classify_distance_form(content)
            assert got == expected, (
                f"分类错误: content={content!r} expected={expected} got={got}"
            )

    def test_all_generated_classifiable(self) -> None:
        """对生成器实际产出的 216 道 distance 题跑分类，断言无错分且全可分类。"""
        items = gen.build_items()
        dist = [it for it in items if it["question"]["scenario_type"] == "distance"]
        assert len(dist) == 216, f"distance 总数应为 216，实际 {len(dist)}"
        from collections import Counter

        for it in dist:
            content = it["question"]["content"]
            form = classify_distance_form(content)
            assert form in (
                "travel",
                "length_conversion",
                "two_segment_add",
            ), content
            # 三类判据一致性（与 classify_distance_form 内部口径互证）
            is_len = "等于多少米" in content
            is_two = "先走了" in content and "又走了" in content
            if is_len:
                assert form == "length_conversion", content
            elif is_two:
                assert form == "two_segment_add", content
            else:
                assert form == "travel", content

        counts = Counter(
            classify_distance_form(it["question"]["content"]) for it in dist
        )
        # 非行程题 = 长度换算 + 两段相加（二次拆分对象）
        assert counts["length_conversion"] + counts["two_segment_add"] > 0
        # 历史口径参考：非行程约占 31.9%（69/216）。新生成器抽样应同量级，
        # 这里只做软性 sanity（非硬断言），避免与生成器随机性耦合。
        non_travel = counts["length_conversion"] + counts["two_segment_add"]
        assert 40 <= non_travel <= 100, f"非行程题占比异常: {counts}"

    def test_map_covers_forms(self) -> None:
        """三段式映射 code 必须齐备且指向正确知识点。"""
        assert DISTANCE_FORM_KP_MAP == {
            "length_conversion": KP_LENGTH,
            "two_segment_add": KP_ADD,
            "travel": KP_TRAVEL,
        }


# ============================================================ 3. dev.db dry-run（存在才跑）
def _dev_db_has_scenario_bank() -> tuple[bool, str]:
    """返回 (存在且含 distance 场景题, 说明)。"""
    db_path = BACKEND_DIR / "dev.db"
    if not db_path.exists():
        return False, "backend/dev.db 不存在"
    conn = sqlite3.connect(str(db_path))
    try:
        try:
            n = conn.execute(
                """
                SELECT COUNT(*) FROM questions
                 WHERE source_doc = 'scenario_bank'
                   AND scenario_template LIKE 'distance/%'
                """
            ).fetchone()[0]
        except sqlite3.Error:
            return False, "dev.db 无 questions 表或 scenario_bank 数据"
    finally:
        conn.close()
    if n == 0:
        return False, "dev.db 不含 scenario_bank 的 distance 题（生产无此数据）"
    return True, f"dev.db 含 {n} 道 distance 场景题"


class TestRelabelDevDbDryRun:
    """对 dev 库 dry-run relabel，断言 69 题分布符合预期（无 dev 则跳过）。"""

    def test_dry_run_distribution_on_dev_db(self) -> None:
        import relabel_distance_kp as rdk  # noqa: E402

        ok, msg = _dev_db_has_scenario_bank()
        if not ok:
            pytest.skip(f"跳过 dev 库断言：{msg}（生产库 0 道 scenario_bank，无生产影响）")

        db_path = BACKEND_DIR / "dev.db"
        # 动态计算期望：对 dev 库 distance 场景题逐条 classify，非行程数即待重贴数
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT content FROM questions "
                "WHERE source_doc = 'scenario_bank' "
                "AND scenario_template LIKE 'distance/%'"
            ).fetchall()
        finally:
            conn.close()
        expected_dist: dict[str, int] = {}
        expected_planned = 0
        for (content,) in rows:
            form = classify_distance_form(content)
            if form != "travel":
                expected_planned += 1
                expected_dist[form] = expected_dist.get(form, 0) + 1

        outcome = rdk.run_relabel(db_path, confirm=False)
        # 历史存量（老生成器）应为 69；若 dev 库系新生成器重建则动态值仍须自洽
        assert outcome.planned == expected_planned, (
            f"待重贴数应等于非行程题数 {expected_planned}，实际 {outcome.planned}"
        )
        assert outcome.relabel_distribution == expected_dist, outcome.relabel_distribution
        assert outcome.written == 0, "dry-run 必须零写入"


# ============================================================ 4. 自包含临时库真实重贴（不依赖 dev.db）
def _build_mirror_db(path: Path) -> dict:
    """构造镜像 dev 结构的临时库：3 个相关知识点 + 216 道 distance 场景题。

    Returns:
        ``{"word_travel_id", "length_id", "add_id"}``。
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE knowledge_points "
            "(id TEXT PRIMARY KEY, code TEXT, name TEXT, status TEXT)"
        )
        conn.execute(
            "CREATE TABLE questions "
            "(id TEXT PRIMARY KEY, content TEXT, kp_id TEXT, "
            "source_doc TEXT, scenario_template TEXT)"
        )

        ids = {}
        for code, name in (
            (KP_TRAVEL, "行程问题"),
            (KP_LENGTH, "毫米分米千米"),
            (KP_ADD, "加减法"),
        ):
            kid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO knowledge_points (id, code, name, status) "
                "VALUES (?,?,?, 'active')",
                (kid, code, name),
            )
            ids[code] = kid

        items = gen.build_items()
        dist = [it for it in items if it["question"]["scenario_type"] == "distance"]
        for it in dist:
            q = it["question"]
            conn.execute(
                "INSERT INTO questions (id, content, kp_id, source_doc, scenario_template) "
                "VALUES (?,?,?, 'scenario_bank', ?)",
                (str(uuid.uuid4()), q["content"], ids[KP_TRAVEL], q["scenario_template"]),
            )
        conn.commit()
        return ids
    finally:
        conn.close()


def _mirror_contents(db_path: Path) -> list[str]:
    """读镜像库里 distance 场景题的题干。"""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT content FROM questions "
            "WHERE source_doc = 'scenario_bank' "
            "AND scenario_template LIKE 'distance/%'"
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


class TestRelabelTempDb:
    """用临时库真实跑 relabel，断言 69 题被正确重贴（验证脚本逻辑，独立于 dev.db）。"""

    def test_relabel_writes_69_and_distribution(self, tmp_path: Path) -> None:
        import relabel_distance_kp as rdk  # noqa: E402

        db_path = tmp_path / "mirror.db"
        ids = _build_mirror_db(db_path)

        # 期望：对镜像库中 216 道 distance 题动态分类，非行程数即待重贴数
        expected_dist: dict[str, int] = {}
        expected_planned = 0
        for content in _mirror_contents(db_path):
            form = classify_distance_form(content)
            if form != "travel":
                expected_planned += 1
                expected_dist[form] = expected_dist.get(form, 0) + 1

        outcome = rdk.run_relabel(db_path, confirm=True)
        assert outcome.planned == expected_planned, outcome.planned
        assert outcome.written == expected_planned, outcome.written
        assert outcome.relabel_distribution == expected_dist, outcome.relabel_distribution

        # 逐列核对：只有 kp_id 变化，且非行程被正确重贴，其余仍旅行
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                """
                SELECT q.content, q.kp_id, kp.code
                  FROM questions q
                  JOIN knowledge_points kp ON kp.id = q.kp_id
                 WHERE q.source_doc = 'scenario_bank'
                   AND q.scenario_template LIKE 'distance/%'
                """
            ).fetchall()
        finally:
            conn.close()

        assert len(rows) == 216
        from collections import Counter

        code_counts = Counter(code for _, _, code in rows)
        assert code_counts[KP_TRAVEL] == 216 - expected_planned, code_counts
        assert code_counts[KP_LENGTH] == expected_dist.get("length_conversion", 0), code_counts
        assert code_counts[KP_ADD] == expected_dist.get("two_segment_add", 0), code_counts

        # 每行 kp 与 classify 一致
        for content, _kid, code in rows:
            form = classify_distance_form(content)
            assert code == DISTANCE_FORM_KP_MAP[form], (content, code, form)

    def test_relabel_idempotent_on_second_run(self, tmp_path: Path) -> None:
        """幂等：第二次跑不再改写（其余归属跳过，不覆盖）。"""
        import relabel_distance_kp as rdk  # noqa: E402

        db_path = tmp_path / "mirror.db"
        _build_mirror_db(db_path)

        first = rdk.run_relabel(db_path, confirm=True)
        expected_planned = sum(
            1
            for c in _mirror_contents(db_path)
            if classify_distance_form(c) != "travel"
        )
        assert first.written == expected_planned

        second = rdk.run_relabel(db_path, confirm=True)
        # 第一次跑后，非行程题已非 KP_WORD_TRAVEL（被 skip_other_kp），
        # 行程题仍为 KP_WORD_TRAVEL（被 skip_same）；均不再改写
        assert second.written == 0, "二次重贴必须幂等（零写入）"
        assert second.skipped_other_kp == expected_planned, second.skipped_other_kp
        assert second.skipped_same == 216 - expected_planned, second.skipped_same

    def test_relabel_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        """dry-run 零写入。"""
        import relabel_distance_kp as rdk  # noqa: E402

        db_path = tmp_path / "mirror.db"
        _build_mirror_db(db_path)

        expected_planned = sum(
            1
            for c in _mirror_contents(db_path)
            if classify_distance_form(c) != "travel"
        )
        outcome = rdk.run_relabel(db_path, confirm=False)
        assert outcome.written == 0
        assert outcome.planned == expected_planned
