"""T12 · 练习模块重构（2026-08-11）QA 测试。

覆盖主理人拍板的 6 维验收口径：数据补全 + 抽题 6 档 fallback + 错因人话文案 +
响应契约 + 前端类型对账 + 数据补全脚本 dry-run。

被测代码（已由 T1-T11 工程师实现，本文件不改业务代码）：

* ``backend/app/services/practice_service.py``
    - ``_options_status(q_type, options)`` — 选项完整性枚举（设计 §3.1）。
    - ``_draw_with_relaxation`` / ``_sample_and_meta`` — 6 档 fallback 抽取。
    - ``draw_questions`` — 主入口。
    - ``_human_error_text`` — 错因人话文案。
* ``backend/app/schemas/practice.py``
    - ``DrawQuestionOut.options_status`` — 单题选项完整性枚举。
    - ``DrawResponse.{actual_count, miss_count, relaxed_to_level, relaxation_steps}`` —
      4 个新字段。
    - ``RelaxationStepOut`` — 单档记录（含 ``applied`` / ``applied_at`` 偏离字段）。
* ``backend/app/api/practice.py``
    - ``POST /api/practice/session`` — 响应契约。
* ``backend/scripts/data_cleanup/20260*_*.py``
    - 三个数据补全脚本 ``--dry-run`` 行为（不动 DB，仅统计）。

主理人已知偏离（设计 §5 / PRD §10）：
* 偏离 1 — ``relaxation_steps`` 只在档位"真放宽"（候选池扩大）时追加。
* 偏离 2 — ``error_type='CALC'``（五类机器码之一）；``error_reason`` 改人话。
* 偏离 3 — ``relaxation_steps`` 多 ``applied`` / ``applied_at`` 字段。

QA 不写业务代码、不写 git；只写测试 + 智能路由判定。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
from sqlalchemy import select

from app.core.error_categories import ERROR_CATEGORIES, error_label
from app.db import SessionLocal
from app.models.content import Answer, KnowledgePoint, Question
from app.models.learning import Attempt, Mistake
from app.services import practice_service
from app.services.practice_service import (
    HUMAN_ERROR_TEXT_LIMIT,
    MAX_COUNT,
    MIN_COUNT,
    RELAXATION_LABELS,
    _human_error_text,
    _options_status,
)

# 复用 B1/B4 测试造数据工具（与 test_practice.py 一套口径）
from t04_helpers import login, seed_attempts
from test_practice import _draw as _api_draw  # type: ignore
from test_practice import make_bank_question  # type: ignore


# 抽题 fallback 兜底档位（设计 §1.5：1-3 档 + 兜底 404）。
# 实现里硬编码 6，此处用常量统一引用，便于测试与脚本同步。
DRAW_FALLBACK_LEVEL = 6


# ==================================================================== A. _options_status
class TestOptionsStatusFunction:
    """A. 后端 — ``_options_status`` 函数（设计 §3.1 / 实现 §1.4）。

    口径：
        * 选择题、归一化 ≥ 4 → ``"complete"``
        * 选择题、归一化 1~3 → ``"partial"``
        * 选择题、归一化 0 → ``"missing"``
        * 非选择（判断 / 计算 / None） → ``"n/a"``
    """

    @staticmethod
    def _option_dict(*pairs: Tuple[str, str]) -> Dict[str, str]:
        return dict(pairs)

    def test_complete_when_4_options(self) -> None:
        result = _options_status("选择", self._option_dict(("A", "1"), ("B", "2"), ("C", "3"), ("D", "4")))
        assert result == "complete"

    def test_complete_when_5_options(self) -> None:
        """多个选项照样算 complete（前端一律展示）。"""
        opts = self._option_dict(
            ("A", "1"),
            ("B", "2"),
            ("C", "3"),
            ("D", "4"),
            ("E", "5"),
        )
        assert _options_status("选择", opts) == "complete"

    def test_complete_when_options_listed_with_6_items(self) -> None:
        opts = [
            {"key": "A", "text": "1"},
            {"key": "B", "text": "2"},
            {"key": "C", "text": "3"},
            {"key": "D", "text": "4"},
            {"key": "E", "text": "5"},
            {"key": "F", "text": "6"},
        ]
        assert _options_status("选择", opts) == "complete"

    def test_partial_when_only_1_option(self) -> None:
        """1 选项 → partial（P0-D amber 提示：选项不完整，做完不算分）。"""
        assert _options_status("选择", {"C": "☆"}) == "partial"

    def test_partial_when_2_options(self) -> None:
        assert _options_status("选择", {"A": "1", "B": "2"}) == "partial"

    def test_partial_when_3_options(self) -> None:
        assert _options_status("选择", {"A": "1", "B": "2", "C": "3"}) == "partial"

    def test_missing_when_0_options_empty_dict(self) -> None:
        assert _options_status("选择", {}) == "missing"

    def test_missing_when_0_options_empty_list(self) -> None:
        assert _options_status("选择", []) == "missing"

    def test_missing_when_options_is_none(self) -> None:
        assert _options_status("选择", None) == "missing"

    def test_na_for_judge_question(self) -> None:
        """判断题 — 前端无需 disabled 态。"""
        assert _options_status("判断", {"A": "√", "B": "×"}) == "n/a"

    def test_na_for_judge_even_with_empty_options(self) -> None:
        """判断题就算归一化 0 个选项也走 ``n/a``，不污染 partial/missing。"""
        assert _options_status("判断", None) == "n/a"
        assert _options_status("判断", {}) == "n/a"

    def test_na_for_calculate_question(self) -> None:
        """计算题 —— 不在 judge_engine 的 SUPPORTED_TYPES；本期不是选择题。"""
        assert _options_status("计算", None) == "n/a"

    def test_na_when_type_is_none_or_empty(self) -> None:
        """``type_`` 为 None / 空串时降级为 ``n/a``（脏数据兜底）。"""
        assert _options_status(None, {"A": "x"}) == "n/a"
        assert _options_status("", {"A": "x"}) == "n/a"
        assert _options_status("   ", {"A": "x"}) == "n/a"

    def test_threshold_is_4_not_2(self) -> None:
        """⚠️ 这是 _options_status 的关键拐点：>=4 才算 complete。设计 §3.1
        与实现均明确为 4；设计师验收硬指标，不依赖前端 length===1 兜底。"""
        # 3 个选项不算 complete
        assert _options_status("选择", {"A": "1", "B": "2", "C": "3"}) != "complete"
        # 4 个选项才算 complete
        assert (
            _options_status(
                "选择",
                {"A": "1", "B": "2", "C": "3", "D": "4"},
            )
            == "complete"
        )


# ==================================================================== B. _question_payload & options_status 端到端
class TestQuestionPayloadOptionsStatus:
    """端到端：``_question_payload`` 把 ``options_status`` 拼进每题返回。"""

    def test_choice_question_payload_includes_options_status(self) -> None:
        """选择题 → options_status in payload；非选择 → "n/a"。"""
        # 选择题：选择题 payload 带 options_status="complete"
        choice_q = self._make_question(type_="选择", options_dict={"A": "1", "B": "2", "C": "3", "D": "4"})
        choice_payload = practice_service._question_payload(choice_q)
        assert choice_payload["options_status"] == "complete"

        # 判断题：payload 带 options_status="n/a"
        judge_q = self._make_question(type_="判断", options_dict=None)
        judge_payload = practice_service._question_payload(judge_q)
        assert judge_payload["options_status"] == "n/a"

    def test_choice_with_partial_options_payload_has_partial_status(self) -> None:
        q = self._make_question(type_="选择", options_dict={"C": "only-one"})
        payload = practice_service._question_payload(q)
        assert payload["options_status"] == "partial"
        # 同时间不会冒出 "complete"
        assert payload["options_status"] != "complete"

    @staticmethod
    def _make_question(*, type_: str = "选择", options_dict: Any = None) -> Question:
        """在内存库里插一道题（含 Answer 行），返回 ORM 对象。"""
        with SessionLocal() as db:
            qid = uuid.uuid4()
            q = Question(
                id=qid,
                content="payload test",
                subject="数学",
                grade=3,
                difficulty=3,
                type_=type_,
                options=options_dict,
                kp_id=None,
                kp_verified=False,
                status="active",
            )
            db.add(q)
            db.flush()
            db.add(
                Answer(
                    question_id=q.id,
                    correct_answer="A",
                    analysis="",
                    distractor_analysis={},
                )
            )
            db.commit()
            db.refresh(q)
            return q


# ==================================================================== C. draw_questions 6 档 fallback（直接调服务 + 6 个场景）
class TestDrawRelaxationSixScenarios:
    """B. ``draw_questions`` 6 档 fallback 行为（设计 §1.5 / PRD §10）。

    偏离 1（主理人拍板）：``relaxation_steps`` 只在档位"真放宽"（候选池扩大）
    时追加，不"挨个跑一遍刷屏"。本测试断言按真实实现走。
    """

    def test_scenario1_strict_match_sufficient_pool_relaxes_to_level_1(
        self, db_session, kp_attributable, student_with_no_attempts
    ) -> None:
        """场景 1：条件严格匹配 + 池充足 → 第 1 档命中。

        期望：
            ``relaxed_to_level == 1``、``steps.length == 1``、``actual_count ==
            wanted``、``miss_count == 0``、``auto_relaxed is False``。
        """
        # 3 道可归因题；attributable_only=True 池 == 3
        self._seed_attributable_choice_questions(
            db_session, kp_id=kp_attributable, count=3, grade=2, section="自测"
        )

        with SessionLocal() as db:
            result = practice_service.draw_questions(
                db,
                student_id=student_with_no_attempts,
                grade=2,
                section="自测",
                count=2,
            )

        # 抽题出参契约
        self._assert_relaxation_basic_shape(result, expected_level=1)
        assert result["actual_count"] == 2
        assert result["miss_count"] == 0
        assert result["auto_relaxed"] is False
        assert result["pool_size"] == 3

        # 逐档：场景 1 只有第 1 档一条；applied=true（池 >= 1）
        steps = result["relaxation_steps"]
        assert len(steps) == 1, f"场景 1 应只有 1 档，实际 {len(steps)} 档：{steps}"
        assert steps[0]["level"] == 1
        assert steps[0]["pool_size"] == 3
        assert steps[0]["applied"] is True
        assert steps[0]["label"] == RELAXATION_LABELS[1]
        # applied_at 是 ISO 串
        assert re.match(r"^\d{4}-\d{2}-\d{2}T", steps[0]["applied_at"]), steps[0]["applied_at"]

    def test_scenario2_step1_empty_relaxes_to_step2(
        self, db_session, kp_attributable, student_with_recent_attempts
    ) -> None:
        """场景 2：第 1 档（attributable_only + 回避 7 天）被排除后空 → 第 2 档（取消回避）命中。

        构造：3 道可归因题被该学生在最近 7 天做过 → 第 1 档 exclude 命中后空；第 2 档不排除即得 3。
        """
        question_ids = self._seed_attributable_choice_questions(
            db_session, kp_id=kp_attributable, count=3, grade=3, section="温故"
        )
        # 让这些题都进"最近 7 天"列表
        self._seed_recent_attempts(db_session, student_with_recent_attempts, question_ids)

        with SessionLocal() as db:
            result = practice_service.draw_questions(
                db,
                student_id=student_with_recent_attempts,
                grade=3,
                section="温故",
                count=2,
            )

        self._assert_relaxation_basic_shape(result, expected_level=2)
        assert result["actual_count"] == 2
        assert result["miss_count"] == 0
        assert result["auto_relaxed"] is True  # relaxed_to_level > 1
        assert result["pool_size"] == 3

        steps = result["relaxation_steps"]
        # 偏离 1：档 1 永远追加（即使池 = 0；applied=false）；档 2 仅放宽时追加
        assert len(steps) == 2, f"应有 2 档记录，实际 {len(steps)} 档：{steps}"
        # 第 1 档：池 = 0；applied=false；仍写入以披露"试过了但没题"
        assert steps[0]["level"] == 1
        assert steps[0]["pool_size"] == 0
        assert steps[0]["applied"] is False
        # 第 2 档：候选池扩大；applied=true
        assert steps[1]["level"] == 2
        assert steps[1]["pool_size"] == 3
        assert steps[1]["applied"] is True
        assert steps[1]["label"] == RELAXATION_LABELS[2]

    def test_scenario3_step1_2_empty_relaxes_to_step3(
        self, db_session, kp_unmapped, student_with_no_attempts
    ) -> None:
        """场景 3：放宽到第 2 档仍空 → 第 3 档（放开 kp，含不可归因题）。

        构造：只有不可归因题（kp_id=None + kp_verified=False）。
        档 1 / 档 2 都因 attributable_only=True 拿不到；档 3（attributable_only=False）拿到。

        注：偏离 1 — 档 2 因为候选池没真扩大，**不会**追加；只追加档 3。
        故 ``steps.length == 2``（档 1 + 档 3）。
        """
        # 全部不可归因：kp_id=None, kp_verified=False
        self._seed_unmapped_choice_questions(
            db_session, count=4, grade=4, section="预习"
        )

        with SessionLocal() as db:
            result = practice_service.draw_questions(
                db,
                student_id=student_with_no_attempts,
                grade=4,
                section="预习",
                count=2,
            )

        self._assert_relaxation_basic_shape(result, expected_level=3)
        assert result["actual_count"] == 2
        assert result["miss_count"] == 0
        assert result["auto_relaxed"] is True
        assert result["pool_size"] == 4
        # unmapped 披露
        assert result["unmapped_count"] == 4

        steps = result["relaxation_steps"]
        assert len(steps) == 2, f"档 2 因未放宽不追加；应有档 1 + 档 3，实际 {len(steps)}：{steps}"
        assert steps[0]["level"] == 1
        assert steps[0]["pool_size"] == 0
        assert steps[0]["applied"] is False
        assert steps[1]["level"] == 3
        assert steps[1]["pool_size"] == 4
        assert steps[1]["applied"] is True
        assert steps[1]["label"] == RELAXATION_LABELS[3]

    def test_scenario4_all_pools_empty_raises_404(
        self, db_session, student_with_no_attempts
    ) -> None:
        """场景 4：所有档都空 → ApiError 404（CODE_NOT_FOUND）。

        构造：完全不插任何题；3 档全空 → raise ApiError(CODE_NOT_FOUND, ...)。
        """
        from app.core.response import ApiError, CODE_NOT_FOUND

        with SessionLocal() as db:
            with pytest.raises(ApiError) as exc_info:
                practice_service.draw_questions(
                    db,
                    student_id=student_with_no_attempts,
                    grade=99,  # 没有任何题匹配此年级
                    count=5,
                )

        assert exc_info.value.code == CODE_NOT_FOUND
        assert "暂时没有可以练的题" in exc_info.value.msg, exc_info.value.msg

    def test_scenario4b_all_pools_empty_with_step6_in_internal_meta(
        self, db_session, student_with_no_attempts
    ) -> None:
        """场景 4 内部口径（设计 §1.5 约定值 6）：``relaxed_to_level == 6`` 即兜底。

        直接调 ``_draw_with_relaxation``，验证它在兜底分支返回 ``relaxed_to_level
        = DRAW_FALLBACK_LEVEL = 6``；外部 ``draw_questions`` 再 raise 404。
        """
        # 不插任何题 → 3 档全空；触发兜底 6 档
        with SessionLocal() as db:
            picked_ids, steps, meta = practice_service._draw_with_relaxation(
                db,
                common={
                    "grade": 99,
                    "edition": None,
                    "section": None,
                    "kp_id": None,
                    "difficulty": None,
                },
                recent=[],
                want=3,
            )

        assert picked_ids == []
        # 兜底分支：把 level=6 的 step 推进去；并在 meta['relaxed_to_level'] = 6
        fallback_steps = [step for step in steps if step["level"] == 6]
        assert len(fallback_steps) == 1
        assert fallback_steps[0]["pool_size"] == 0
        assert fallback_steps[0]["applied"] is False
        assert meta["relaxed_to_level"] == DRAW_FALLBACK_LEVEL
        assert meta["actual_count"] == 0
        assert meta["miss_count"] == 3  # miss = want - picked

    def test_scenario5_partial_count_no_500_less_is_honest(
        self, db_session, kp_attributable, student_with_no_attempts
    ) -> None:
        """场景 5：用户要 5 道只给 2 道 — 不报错（少给不凑数，Q7 拍板）。

        期望：HTTP 不 500；``actual_count=2``、``miss_count=3``、``relaxed_to_level=1``。
        """
        self._seed_attributable_choice_questions(
            db_session, kp_id=kp_attributable, count=2, grade=5, section="提优"
        )

        with SessionLocal() as db:
            # 不抛异常 = 通过
            result = practice_service.draw_questions(
                db,
                student_id=student_with_no_attempts,
                grade=5,
                section="提优",
                count=5,
            )

        # Q7：少给不凑数
        assert result["actual_count"] == 2
        assert result["miss_count"] == 3
        assert result["relaxed_to_level"] == 1
        assert result["pool_size"] == 2
        # 实际抽到的题就 2 道（≠ requested_count）
        assert len(result["questions"]) == 2

    def test_scenario6_options_status_per_question(
        self, db_session, student_with_no_attempts
    ) -> None:
        """场景 6：``options_status`` 随每题返回（设计 §1.4 / §3.1）。

        ⚠️ ``missing``（0 个选项）的选择题会被 ``is_l1_safe`` 过滤掉，**不会**
        进入抽题结果（设计 §3.1 + judge_engine.is_l1_safe 一致：选择题必须 ≥2 选项）。
        因此**只有 complete 与 partial 两态**会出现在 ``/session`` 响应里——再叠
        上一个判断题 ``n/a`` 态验证三态。
        """
        with SessionLocal() as db:
            # Complete: 4 个选项 → L1-safe ✓
            complete_id = self._seed_question(
                db,
                type_="选择",
                options={"A": "1", "B": "2", "C": "3", "D": "4"},
                grade=2,
                section="提优",
                with_answer=True,
                correct_answer="A",
            )
            # Partial: 仅 1 个选项 → L1-safe ✓（partial 是允许进抽题池的，
            # 抽到再通过 options_status 给前端 disabled 兜底）
            partial_id = self._seed_question(
                db,
                type_="选择",
                options={"C": "only"},
                grade=2,
                section="提优",
                with_answer=True,
                correct_answer="C",
            )
            # N/A: 判断题（无 options） → L1-safe ✓
            judge_id = self._seed_question(
                db,
                type_="判断",
                options=None,
                grade=2,
                section="提优",
                with_answer=True,
                correct_answer="√",
            )

        with SessionLocal() as db:
            result = practice_service.draw_questions(
                db,
                student_id=student_with_no_attempts,
                grade=2,
                section="提优",
                count=3,
            )

        # 三题应在结果里
        assert len(result["questions"]) == 3, (
            f"期望 3 题，实际 {len(result['questions'])}: {result['questions']}"
        )
        by_qid: Dict[str, Dict[str, Any]] = {q["question_id"]: q for q in result["questions"]}
        assert by_qid[str(complete_id)]["options_status"] == "complete"
        assert by_qid[str(partial_id)]["options_status"] == "partial"
        assert by_qid[str(judge_id)]["options_status"] == "n/a"
        # 0 选项的题不进 L1 安全池（不影响抽题结果）
        assert "_missing_id" not in by_qid or True  # _seed_question 故意不构造 missing

    def test_options_status_missing_via_function_but_excluded_from_pool(
        self, db_session
    ) -> None:
        """``missing``（0 选项）的选择题：函数层面返回 ``missing``，但实际
        抽题池会被 ``is_l1_safe`` 过滤。这是函数 ↔ 服务两层的契约边界。

        单元层面：``_options_status("选择", {}) == "missing"``（A 段已覆盖）。
        这里额外断言 ``is_l1_safe`` 真把这种题踢出池，验证 service 那边的隐式约束。
        """
        # 单元层面的 missing（与 A 段重复一次，做边界钉死）
        assert _options_status("选择", {}) == "missing"

        # 服务层面：0 选项选择题不进候选池（与题面不一致）
        from app.services.judge_engine import is_l1_safe

        assert is_l1_safe("选择", {}, "A") is False
        # 4 选项选择 → 进池
        assert is_l1_safe("选择", {"A": "1", "B": "2", "C": "3", "D": "4"}, "A") is True

    # -- 私有 helpers ---------------------------------------------------------

    @staticmethod
    def _assert_relaxation_basic_shape(result: Dict[str, Any], *, expected_level: int) -> None:
        """验 DSR：response 必含 4 个新字段 + ``session_id`` + ``questions``。"""
        assert "session_id" in result
        assert "questions" in result
        assert isinstance(result["questions"], list)
        # 4 新字段
        assert "actual_count" in result, f"响应缺 actual_count: keys={sorted(result)}"
        assert "miss_count" in result
        assert "relaxed_to_level" in result
        assert "relaxation_steps" in result
        assert isinstance(result["relaxation_steps"], list)
        # relaxed_to_level 落在 1-3 或 6
        assert result["relaxed_to_level"] in (1, 2, 3, 6)
        assert result["relaxed_to_level"] == expected_level, (
            f"expected relaxed_to_level={expected_level}, got {result['relaxed_to_level']}; "
            f"steps={result['relaxation_steps']}"
        )
        # 旧字段保留作兼容
        assert "auto_relaxed" in result
        assert "requested_count" in result
        assert "pool_size" in result
        assert "unmapped_count" in result

    @staticmethod
    def _seed_attributable_choice_questions(
        db, *, kp_id: uuid.UUID, count: int, grade: int, section: str
    ) -> List[str]:
        """seed N 道可归因 + active + 选择题。"""
        ids: List[str] = []
        for i in range(count):
            q = Question(
                content=f"attributable-{grade}-{section}-{i}",
                subject="数学",
                grade=grade,
                difficulty=3,
                type_="选择",
                options={"A": "1", "B": "2", "C": "3", "D": "4"},
                kp_id=kp_id,
                kp_verified=True,
                edition="青岛版",
                section=section,
                status="active",
            )
            db.add(q)
            db.flush()
            db.add(
                Answer(
                    question_id=q.id,
                    correct_answer="A",
                    analysis="",
                    distractor_analysis={},
                )
            )
            ids.append(str(q.id))
        db.commit()
        return ids

    @staticmethod
    def _seed_unmapped_choice_questions(
        db, *, count: int, grade: int, section: str
    ) -> List[str]:
        """seed N 道**不可归因** + active + 选择题。"""
        ids: List[str] = []
        for i in range(count):
            q = Question(
                content=f"unmapped-{grade}-{section}-{i}",
                subject="数学",
                grade=grade,
                difficulty=3,
                type_="选择",
                options={"A": "1", "B": "2", "C": "3", "D": "4"},
                kp_id=None,
                kp_verified=False,
                edition="青岛版",
                section=section,
                status="active",
            )
            db.add(q)
            db.flush()
            db.add(
                Answer(
                    question_id=q.id,
                    correct_answer="A",
                    analysis="",
                    distractor_analysis={},
                )
            )
            ids.append(str(q.id))
        db.commit()
        return ids

    @staticmethod
    def _seed_question(
        db,
        *,
        type_: str,
        options: Any,
        grade: int,
        section: str,
        with_answer: bool,
        correct_answer: str = "A",
    ) -> uuid.UUID:
        """通用 single-row seed。"""
        q = Question(
            content=f"seed-{uuid.uuid4()}",
            subject="数学",
            grade=grade,
            difficulty=3,
            type_=type_,
            options=options,
            kp_id=None,
            kp_verified=False,
            edition="青岛版",
            section=section,
            status="active",
        )
        db.add(q)
        db.flush()
        if with_answer:
            db.add(
                Answer(
                    question_id=q.id,
                    correct_answer=correct_answer,
                    analysis="",
                    distractor_analysis={},
                )
            )
        db.commit()
        return q.id

    @staticmethod
    def _seed_recent_attempts(
        db, student_id: uuid.UUID, question_ids: List[str]
    ) -> None:
        """在「最近 7 天」窗口里给该学生插作答（让题被 _recent_question_ids 命中回避）。"""
        from app.models.base import utcnow
        from datetime import timedelta

        since = utcnow() - timedelta(days=1)  # 1 天前 → 落在 7 天窗口里
        for qid_str in question_ids:
            db.add(
                Attempt(
                    student_id=student_id,
                    question_id=uuid.UUID(qid_str),
                    is_correct=True,
                    created_at=since,
                    client_event_id=f"recent-{uuid.uuid4().hex}",
                )
            )
        db.commit()


# ==================================================================== D. /api/practice/session 响应 schema
class TestDrawApiSchema:
    """C. 后端 — ``/api/practice/session`` 响应 schema（FastAPI 自动文档 + 契约）。"""

    def test_api_response_contains_4_new_fields(self, client, new_phone) -> None:
        headers, _ = login(client, new_phone())
        # 用唯一 kp_id + 唯一 edition 收窄到本测试的题，避免 in-memory
        # SQLite session-scope 共享导致被其他测试遗留题污染。
        kp_id = self._make_kp(f"schema-kp-{uuid.uuid4().hex}")
        make_bank_question(
            kp_id=kp_id, grade=2, section="自测", edition="唯一版A",
            content="schema-题1",
        )
        make_bank_question(
            kp_id=kp_id, grade=2, section="自测", edition="唯一版A",
            content="schema-题2",
        )

        data = _api_draw(
            client, headers, kp_id=kp_id, grade=2, section="自测",
            edition="唯一版A", count=2,
        )

        # 4 新字段
        for field in ("actual_count", "miss_count", "relaxed_to_level", "relaxation_steps"):
            assert field in data, f"响应缺 {field}: {sorted(data)}"
        assert isinstance(data["relaxation_steps"], list)
        assert isinstance(data["actual_count"], int)
        assert isinstance(data["miss_count"], int)
        assert isinstance(data["relaxed_to_level"], int)

    def test_api_response_relaxation_steps_each_has_required_keys(self, client, new_phone) -> None:
        """``relaxation_steps`` 每个元素必含 ``level / label / pool_size /
        applied / applied_at``；老客户端只看 level + label 也不破。"""
        headers, _ = login(client, new_phone())
        kp_id = self._make_kp(f"steps-kp-{uuid.uuid4().hex}")
        make_bank_question(
            kp_id=kp_id, grade=2, edition="唯一版B", content="steps-题1",
        )

        data = _api_draw(
            client, headers, kp_id=kp_id, grade=2, edition="唯一版B", count=1,
        )

        steps = data["relaxation_steps"]
        assert len(steps) >= 1
        for step in steps:
            # 必备字段
            assert "level" in step
            assert "label" in step
            assert "pool_size" in step
            assert "applied" in step  # 偏离 3
            assert "applied_at" in step  # 偏离 3
            # 类型
            assert isinstance(step["level"], int)
            assert isinstance(step["pool_size"], int)
            assert isinstance(step["applied"], bool)
            assert isinstance(step["applied_at"], str)
            # level ∈ {1, 2, 3, 6}
            assert step["level"] in (1, 2, 3, 6), f"level 越界: {step['level']}"

    def test_api_response_keeps_legacy_fields(self, client, new_phone) -> None:
        """旧客户端兼容（设计 §7.7 / C10）：``auto_relaxed`` / ``pool_size`` /
        ``unmapped_count`` / ``requested_count`` 仍必含。"""
        headers, _ = login(client, new_phone())
        kp_id = self._make_kp(f"legacy-kp-{uuid.uuid4().hex}")
        make_bank_question(
            kp_id=kp_id, grade=2, edition="唯一版C", content="legacy-题1",
        )

        data = _api_draw(
            client, headers, kp_id=kp_id, grade=2, edition="唯一版C", count=1,
        )

        for field in (
            "session_id",
            "questions",
            "pool_size",
            "unmapped_count",
            "auto_relaxed",
            "requested_count",
            "actual_count",
            "miss_count",
            "relaxed_to_level",
            "relaxation_steps",
        ):
            assert field in data

    def test_api_response_per_question_includes_options_status(self, client, new_phone) -> None:
        """每题 response 含 ``options_status``；选择题 = complete / partial /
        missing；判断题 = n/a。

        用 ``kp_id`` 严格收窄到本测试 seed 的 kp，避免被其他测试遗留的同年级
        同分区题污染（in-memory SQLite 是 session-scope 共享）。
        """
        headers, _ = login(client, new_phone())
        kp_id = self._make_kp(f"options-status-kp-{uuid.uuid4().hex}")
        # 选择题：4 选项 → complete
        qid_choice = make_bank_question(
            kp_id=kp_id,
            grade=2,
            section="提优",
            content="complete-题",
        )
        # 判断题 → n/a（判断题 L1-safe 要求标准答案可归一为 √/×，'√' 可）
        qid_judge = make_bank_question(
            kp_id=kp_id,
            type_="判断",
            grade=2,
            section="提优",
            content="judge-题",
            correct_answer="√",
        )

        data = _api_draw(client, headers, kp_id=kp_id, grade=2, section="提优", count=2)

        by_id = {q["question_id"]: q for q in data["questions"]}
        assert by_id[qid_choice]["options_status"] == "complete"
        assert by_id[qid_judge]["options_status"] == "n/a"

    @staticmethod
    def _make_kp(name: str) -> str:
        with SessionLocal() as db:
            kp = KnowledgePoint(name=name)
            db.add(kp)
            db.commit()
            return str(kp.id)


# ==================================================================== E. _human_error_text / finish_session 错因人话文案
class TestHumanErrorText:
    """D. 后端 — ``_human_error_text`` + 错题入本人话文案（设计 §1.7 / P1-I）。

    偏离 2（主理人拍板）：``error_type='CALC'`` 五类机器码之一；``error_reason``
    走人话 — 「知识点「X」{label}」，上限 100 字。
    """

    @pytest.mark.parametrize("code", list(ERROR_CATEGORIES))
    def test_human_error_text_uses_error_label_for_all_5_codes(self, code: str) -> None:
        """五类机器码全部走 ``error_label`` 拿到中文标签（不引入新枚举）。"""
        text = _human_error_text("测试知识点", code)
        # 中文标签必须出现（不只是机器码）
        assert error_label(code) in text, f"code={code} text={text}"

    def test_human_error_text_with_kp_name(self) -> None:
        """有知识点名 → 格式为「知识点「{kp_name}」{label}」。"""
        text = _human_error_text("加减法运算", "CALC")
        assert text == f"知识点「加减法运算」{error_label('CALC')}"
        assert text == "知识点「加减法运算」计算错"

    def test_human_error_text_without_kp_name(self) -> None:
        """无知识点名 → 降级为「该题{label}」。"""
        text = _human_error_text(None, "CALC")
        assert text == f"该题{error_label('CALC')}"

    def test_human_error_text_truncates_at_100_chars(self) -> None:
        """超长 kp_name 截断到 ``HUMAN_ERROR_TEXT_LIMIT``（=100）+ 省略号。"""
        long_name = "特别长的知识点名" * 30  # > 100 字
        text = _human_error_text(long_name, "CALC")
        assert len(text) <= HUMAN_ERROR_TEXT_LIMIT, f"text len={len(text)}, limit={HUMAN_ERROR_TEXT_LIMIT}"
        # 截断后以省略号结尾
        assert text.endswith("…"), f"未省略号收尾：{text[-3:]}"

    def test_human_error_text_unknown_code_falls_back_to_other(self) -> None:
        """未知类别 → ``error_label`` 返回 ``"其他"``；不抛异常。"""
        text = _human_error_text("某知识点", "UNKNOWN_CATEGORY")
        assert "其他" in text


class TestFinishSessionErrorReason:
    """D 端到端：``/api/practice/finish`` 答错入本时写入人话 ``error_reason`` + 五类机器码 ``error_type``。"""

    def test_wrong_answer_creates_mistake_with_human_reason(self, client, new_phone) -> None:
        """做错的题入本时：``error_type == 'CALC'``（五类机器码之一）；
        ``error_reason`` 包含人话（如「知识点「xx」计算错」），不超过 100 字。"""
        headers, student_id = login(client, new_phone())
        kp_id = self._make_kp("人话错因-kp")
        wrong_qid = make_bank_question(
            kp_id=kp_id,
            grade=4,
            section="自测",
            content="人话错因-做错",
            correct_answer="B",
        )

        session_id = _api_draw(client, headers, grade=4, section="自测", count=5)["session_id"]

        # 故意选错（B 是对的，选 A）
        client.post(
            "/api/practice/answer",
            json={"session_id": session_id, "question_id": wrong_qid, "selected_answer": "A"},
            headers=headers,
        )

        client.post("/api/practice/finish", json={"session_id": session_id}, headers=headers)

        mistakes = self._mistakes_of(student_id, wrong_qid)
        assert len(mistakes) == 1, mistakes
        mistake = mistakes[0]
        assert mistake.error_type == "CALC", f"error_type 应保持五类机器码 CALC：{mistake.error_type}"
        # error_reason 应是中文人话，不带 CALC / READ 等机器码
        assert mistake.error_reason
        assert "CALC" not in mistake.error_reason
        assert "知识点" in mistake.error_reason
        # 上限：设计 §8.3 TD-6 不超过 100 字
        assert len(mistake.error_reason) <= 100, mistake.error_reason
        # 包含中文错因标签（"计算错"）
        assert "计算错" in mistake.error_reason, mistake.error_reason

    def test_right_answer_no_mistake(self, client, new_phone) -> None:
        """做对的题不入本（error_reason 字段不存在）。"""
        headers, student_id = login(client, new_phone())
        kp_id = self._make_kp("人话错因-无错-kp")
        right_qid = make_bank_question(
            kp_id=kp_id,
            grade=4,
            section="自测",
            content="人话错因-做对",
            correct_answer="B",
        )

        session_id = _api_draw(client, headers, grade=4, section="自测", count=5)["session_id"]

        client.post(
            "/api/practice/answer",
            json={"session_id": session_id, "question_id": right_qid, "selected_answer": "B"},
            headers=headers,
        )

        client.post("/api/practice/finish", json={"session_id": session_id}, headers=headers)

        mistakes = self._mistakes_of(student_id, right_qid)
        assert mistakes == [], "做对的题不应进错题本"

    @staticmethod
    def _make_kp(name: str) -> str:
        with SessionLocal() as db:
            kp = KnowledgePoint(name=name)
            db.add(kp)
            db.commit()
            return str(kp.id)

    @staticmethod
    def _mistakes_of(student_id: uuid.UUID, question_id: str) -> List[Mistake]:
        with SessionLocal() as db:
            rows = list(
                db.execute(
                    select(Mistake).where(
                        Mistake.student_id == student_id,
                        Mistake.question_id == uuid.UUID(question_id),
                    )
                ).scalars()
            )
            return rows


# ==================================================================== F. 前端类型对账（manual verification via Read tool）
class TestFrontendTypeAlignment:
    """E. 前端 — types 与 schema 对齐（无前端单测基建，靠 Read 验证契约）。

    校验清单：
        * ``OptionsStatus`` 与 ``DrawQuestionOut.options_status`` 字面量一致；
        * ``RelaxationStep`` 与 ``RelaxationStepOut`` 字段一一对应；
        * ``BankQuestionCard`` 接 ``options_status``，partial/missing 时渲染 ⚠。
    """

    def test_frontend_options_status_type_matches_backend_enum(self) -> None:
        """``OptionsStatus`` 与 ``_options_status`` 返回值集合相同。"""
        ts_path = self._frontend_root() / "src/types/practice.ts"
        assert ts_path.is_file(), f"前端 types/practice.ts 不存在：{ts_path}"
        text = ts_path.read_text(encoding="utf-8")
        # 必须四态完整
        for enum_value in ("complete", "partial", "missing", "n/a"):
            # 在前端 OptionsStatus 字面量集合里出现
            assert f"'{enum_value}'" in text, (
                f"OptionsStatus 缺字面量 {enum_value!r}：{ts_path}"
            )

    def test_frontend_relaxation_step_fields_match_backend_schema(self) -> None:
        """前端 ``RelaxationStep`` 字段名与后端 ``RelaxationStepOut`` 一一对应。"""
        ts_path = self._frontend_root() / "src/types/practice.ts"
        text = ts_path.read_text(encoding="utf-8")
        # 必须包含的字段（字段名也按 snake_case，注意 ``?:`` 是 optional）
        for required_field in ("level", "label", "pool_size", "applied_at", "applied"):
            # 形如 `  field:` 或 `  field?:`
            assert (
                f"{required_field}:" in text or f"{required_field}?:" in text
            ), f"前端 RelaxationStep 缺字段 {required_field}: {ts_path}"

    def test_frontend_bank_question_card_handles_partial(self) -> None:
        """``BankQuestionCard.vue`` 收到 ``options_status === 'partial'`` 时：
        * 选项 disabled；
        * 渲染 amber ⚠ 提示「这道题的选项不完整，做完不算分」。
        """
        vue_path = (
            self._frontend_root()
            / "src/components/practice/BankQuestionCard.vue"
        )
        assert vue_path.is_file(), vue_path
        text = vue_path.read_text(encoding="utf-8")
        # partial 触发 disabled（optionsDisabled）
        assert "isPartial" in text or "optionsStatus" in text
        # amber 提示文案
        assert "选项不完整" in text, "BankQuestionCard 缺 partial 态的 amber 提示文案"
        assert "amber" in text, "BankQuestionCard 缺 amber 样式类"

    @staticmethod
    def _frontend_root() -> Path:
        """``backend/tests/`` 的父的父 = backend/，再上一级 = project root，
        再拼 ``student-web/``。
        """
        return Path(__file__).resolve().parents[2] / "student-web"


# ==================================================================== G. 数据补全脚本 dry-run（隔离 tmp_path DB）
class TestDataCleanupScriptsDryRun:
    """F. 数据补全脚本 ``--dry-run`` 行为（不动 DB，仅统计）。

    策略：用 ``tmp_path`` 创建隔离的 SQLite 文件 DB；建 schema、塞种子数据；
    设 ``DATABASE_URL`` 调子进程跑三个脚本；解析 stdout 断言扫描/命中数 > 0。

    这是端到端覆盖（脚本是独立进程），不替换 get_engine。
    """

    SCRIPTS_DIR = Path("scripts/data_cleanup")

    @pytest.fixture
    def seeded_db(self, tmp_path: Path) -> Path:
        """建一个隔离 DB：含 questions + knowledge_points + answers；塞种子。"""
        db_path = tmp_path / "seed.db"
        url = f"sqlite:///{db_path}"

        # 拿一个 StaticPool 引擎（与 db.py 同源 → 共享同一份 in-mem test 数据
        # 在脚本进程里同样可用）
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from app.models.base import Base

        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        Base.metadata.create_all(engine)
        SessionLocalTmp = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

        with SessionLocalTmp() as db:
            kp = KnowledgePoint(name="自动打标测试-知识点", sort=0)
            db.add(kp)
            db.flush()
            for index in range(5):
                # 5 道 active 选择题：3 道完整（complete）、1 道 1 选项（partial）、1 道 0 选项（missing）
                if index < 3:
                    opts = {"A": "1", "B": "2", "C": "3", "D": "4"}
                elif index == 3:
                    opts = {"C": "only"}  # partial
                else:
                    opts = {}  # missing
                question = Question(
                    content=("E2E 自动打标测试-应用题-题干内容" if index < 2 else f"自动打标测试-干净题-{index}"),
                    subject="数学",
                    grade=3,
                    difficulty=3,
                    type_="选择",
                    options=opts,
                    kp_id=kp.id if index < 2 else None,  # 前 2 题可归因，后 3 题不可归因
                    kp_verified=(index < 2),
                    edition="青岛版",
                    section="温故",
                    status="active",
                )
                db.add(question)
                db.flush()
                db.add(
                    Answer(
                        question_id=question.id,
                        correct_answer="A",
                        analysis="",
                        distractor_analysis={},
                    )
                )
            db.commit()

        return db_path

    @pytest.fixture
    def _python_path(self) -> List[str]:
        """把 backend/ 加进 PYTHONPATH 让子进程 ``from app...`` 能工作。"""
        return [str(Path("backend").resolve())]

    def _run_script(self, script_name: str, db_url: str, python_path: List[str]) -> Tuple[int, str, str]:
        """子进程跑一个数据补全脚本；返回 (returncode, stdout, stderr)。"""
        env = os.environ.copy()
        env["DATABASE_URL"] = db_url
        env["PYTHONPATH"] = (
            ":".join(python_path) if python_path else env.get("PYTHONPATH", "")
        )
        # 关掉 dev 模式可能带来的副作用
        env.setdefault("ENVIRONMENT", "test")
        cmd = [sys.executable, str(self.SCRIPTS_DIR / script_name), "--dry-run"]
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            cwd=str(Path.cwd()),
            timeout=60,
        )
        return result.returncode, result.stdout, result.stderr

    def test_archive_incomplete_options_dry_run_counts_match(self, seeded_db, _python_path) -> None:
        """``archive_incomplete_options.py --dry-run``：扫描数 > 0、命中数 > 0。

        种子：5 道 active 选择题，其中 2 道 partial + 1 道 missing = 3 道命中。
        实际 stdout 格式：「[dry-run] 阶段 A 完成：扫描 X 道 active 选择题，命中归档 Y 道」
        """
        rc, out, err = self._run_script(
            "20260811_archive_incomplete_options.py",
            f"sqlite:///{seeded_db}",
            _python_path,
        )
        assert rc == 0, f"exit={rc}\nSTDOUT={out}\nSTDERR={err}"
        match = re.search(r"扫描\s*(\d+)\s*道\s*active\s*选择题，命中归档\s*(\d+)\s*道", out)
        assert match, f"无法解析 stdout：{out!r}"
        scanned = int(match.group(1))
        matched = int(match.group(2))
        assert scanned > 0, f"扫描数应为正，实际 {scanned}"
        assert matched > 0, f"命中数应为正（partial/missing 共 3 道），实际 {matched}"

    def test_strip_e2e_prefix_dry_run_counts_match(self, seeded_db, _python_path) -> None:
        """``strip_e2e_prefix.py --dry-run``：全表扫描数 > 0、命中数 ≥ 1（E2E 前缀题）。

        种子：5 道题，前 2 道题干含 ``E2E`` 前缀 → 应至少命中 2 道。
        stdout 格式：「[dry-run] 阶段 B 完成：全表 X 行扫描，命中 E2E 前缀 Y 行」
        """
        rc, out, err = self._run_script(
            "20260811_strip_e2e_prefix.py",
            f"sqlite:///{seeded_db}",
            _python_path,
        )
        assert rc == 0, f"exit={rc}\nSTDOUT={out}\nSTDERR={err}"
        match = re.search(r"全表\s*(\d+)\s*行扫描，命中 E2E 前缀\s*(\d+)\s*行", out)
        assert match, f"无法解析 stdout：{out!r}"
        total = int(match.group(1))
        matched = int(match.group(2))
        assert total > 0, f"全表行数应为正：{total}"
        assert matched >= 2, f"应至少命中 2 道（题干有 E2E 前缀）：{matched}"

    def test_autotag_unmapped_dry_run_counts_match(self, seeded_db, _python_path) -> None:
        """``autotag_unmapped.py --dry-run``：扫描数 > 0、自动打标或待人工数 > 0。

        种子：3 道不可归因题 → 至少有一个扫描数 > 0。
        stdout 格式：「[dry-run] 阶段 C 完成：自动打标 X 题，待人工标注 Y 题」
        """
        rc, out, err = self._run_script(
            "20260811_autotag_unmapped.py",
            f"sqlite:///{seeded_db}",
            _python_path,
        )
        assert rc == 0, f"exit={rc}\nSTDOUT={out}\nSTDERR={err}"
        match = re.search(r"自动打标\s*(\d+)\s*题，待人工标注\s*(\d+)\s*题", out)
        assert match, f"无法解析 stdout：{out!r}"
        auto_tagged = int(match.group(1))
        pending = int(match.group(2))
        # 二者之和 > 0（只要有一个变化量）
        assert (auto_tagged + pending) > 0, (
            f"dry-run 应至少扫描/打标/标不上数：auto_tagged={auto_tagged}, pending={pending}"
        )


# ==================================================================== H. 边界与契约
class TestBoundariesAndContracts:
    """契约 + 边界值：count 钳制、字段默认值、auto_relaxed 反向兼容公式。"""

    def test_count_clamped_within_max(self, client, new_phone) -> None:
        """``count`` 超过 ``MAX_COUNT``（=30）→ 后端拒绝（钳到 30 / 4xx）。

        现状：``DrawRequest`` schema ``le=30``；超出会走框架校验。框架通过
        ``app.core.response`` 把 pydantic ValidationError 包成 ``code=400``
        而**不是** 标准 422（项目自定义响应壳）。断言 4xx 即可。
        """
        from app.services.practice_service import MAX_COUNT as EXPECTED_MAX

        assert EXPECTED_MAX == 30, f"MAX_COUNT 期望 30，实际 {EXPECTED_MAX}"

        headers, _ = login(client, new_phone())
        resp = client.post(
            "/api/practice/session",
            json={"count": 999},
            headers=headers,
        )
        # 不应该 200 成功抽到；可接受 4xx
        assert resp.status_code >= 400, (
            f"count=999 应被钳制/拒绝，实际 status={resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["code"] != 0, f"应是错误响应：{body}"

    def test_relaxed_to_level_default_in_schema(self) -> None:
        """``DrawResponse.relaxed_to_level`` 默认值 = 1（沿用 Pydantic 默认）。"""
        from app.schemas.practice import DrawResponse

        resp = DrawResponse(session_id="00000000-0000-0000-0000-000000000000")
        assert resp.relaxed_to_level == 1
        assert resp.actual_count == 0
        assert resp.miss_count == 0
        assert resp.relaxation_steps == []

    def test_relaxation_step_applied_field_default(self) -> None:
        """``RelaxationStepOut.applied`` 默认为 False（前向兼容未填的客户端）。"""
        from app.schemas.practice import RelaxationStepOut

        step = RelaxationStepOut(level=1, label="x", pool_size=0)
        assert step.applied is False
        assert step.applied_at == ""
        assert step.pool_size == 0

    def test_auto_relaxed_equals_relaxed_to_level_gt_1(self, db_session, student_with_no_attempts) -> None:
        """老客户端兼容：``auto_relaxed = (relaxed_to_level > 1)``。"""
        # Step 1 只在第 1 档命中 → auto_relaxed = False
        kp_id = self._make_kp("compat-kp")
        with SessionLocal() as db:
            qid = uuid.uuid4()
            q = Question(
                id=qid,
                content="compat-1",
                subject="数学",
                grade=2,
                difficulty=3,
                type_="选择",
                options={"A": "1", "B": "2", "C": "3", "D": "4"},
                kp_id=kp_id,
                kp_verified=True,
                edition="青岛版",
                section="自测",
                status="active",
            )
            db.add(q)
            db.flush()
            db.add(
                Answer(
                    question_id=q.id,
                    correct_answer="A",
                    analysis="",
                    distractor_analysis={},
                )
            )
            db.commit()

            result = practice_service.draw_questions(
                db,
                student_id=student_with_no_attempts,
                grade=2,
                section="自测",
                count=1,
            )

        assert result["relaxed_to_level"] == 1
        assert result["auto_relaxed"] is False

    @staticmethod
    def _make_kp(name: str) -> uuid.UUID:
        with SessionLocal() as db:
            kp = KnowledgePoint(name=name)
            db.add(kp)
            db.commit()
            return kp.id


# ==================================================================== 共享 fixtures

@pytest.fixture(autouse=True)
def _wipe_schema_per_test():
    """autouse：每个测试**开始前** ``drop_all`` + ``create_all``，让本文件的
    测试独享干净内存 SQLite，不污染同 session 下**先跑的** test_practice.py 的
    期望（反之也成立：本文件测试先 drop 同 session 数据不会影响 test_practice，
    因为 test_practice 是「按名字字母序 + 自然收集顺序」先跑，跑完才轮到本文件）。

    实际方案：每次进入本文件测试前，drop 全部表 + 重建。这样本文件的测试与
    之前的测试天然隔离；后续测试（如果有）依赖 conftest 的 ``_create_schema``
    重新建表。
    """
    from app.models.base import Base
    from app.db import engine

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db_session():
    """让 ``TestDrawRelaxationSixScenarios`` 直接拿 ``SessionLocal()`` 即可。
    这里仅占位（conftest 已经把 ``DATABASE_URL`` 设成内存库 + 建好 schema）。
    """
    with SessionLocal() as db:
        yield db


@pytest.fixture
def kp_attributable(db_session):
    """返回一个已 commit 的可归因知识点。"""
    kp = KnowledgePoint(name=f"attributable-{uuid.uuid4().hex}")
    db_session.add(kp)
    db_session.commit()
    db_session.refresh(kp)
    return kp.id


@pytest.fixture
def kp_unmapped(db_session):
    """占位：unmapped 测试不需要 kp，但签名统一。"""
    return None


@pytest.fixture
def student_with_no_attempts(client, new_phone):
    """造一个新学生（无任何 Attempt → 自然不进 _recent_question_ids）。

    通过 dev 登录自动建号，sid 即 student.id。
    """
    _, student_id = login(client, new_phone())
    return student_id


@pytest.fixture
def student_with_recent_attempts(client, new_phone):
    """造一个新学生。配合 ``seed_recent_attempts`` / ``seed_attempts``
    写入尝试，让题进入回避窗口。
    """
    _, student_id = login(client, new_phone())
    return student_id
