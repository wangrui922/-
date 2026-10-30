# -*- coding: utf-8 -*-
"""接地气素材注入守卫（A-1 期 · 第 3 段）。

守的是 PM 写死的四条验收判据：

1. **只在 ``DEMO`` / ``JUDGE`` 注入** —— ``GUIDE`` / ``HINT`` 零注入。
   原因（写在这里防止将来被「优化」掉）：``app/ai/intent.py`` 的
   ``UNGUARDED_MODES = (MODE_DEMO, MODE_JUDGE)`` 整段跳过答案泄漏检查；
   而 ``GUIDE`` / ``HINT`` 严格查 ``has_answer_leak`` —— 素材段一旦把答案
   带出来就会触发重生成，白白多一次模型往返，甚至退化成兜底话术。
2. **开关关闭 → 输出与改造前逐字一致**（不是「差不多」，是 ``==``）。
3. **答案泄漏守卫拦截率仍 100%** —— 加了素材之后，守卫该拦还得拦。
4. **核心 PROMPT 是受保护资产** —— 素材只能作为**后缀**追加，
   ``app/ai/prompts/tutor.py`` 的 VERSION 与核心 PROMPT 一个字都不许动。

守卫自证（变异测试）
--------------------
本文件的每条断言都对着**可执行代码**（不是注释、不是常量名）。
变异测试记录见交付报告：改动 ``tutor._grounding_suffix`` 的模式判断 /
``grounding_service`` 的开关判断后，对应用例立刻变红。

环境铁律
--------
**绝不设 ``DATABASE_URL``**（conftest 默认 ``:memory:``）。
"""

from __future__ import annotations

import uuid
from typing import Optional

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.agents import tutor
from app.ai.answer_guard import (
    LEAK_RETRY_HINT,
    generate_guarded_reply,
    has_answer_leak,
)
from app.ai.intent import (
    MODE_DEMO,
    MODE_GUIDE,
    MODE_HINT,
    MODE_JUDGE,
    MODE_VALUES,
    UNGUARDED_MODES,
)
from app.ai.prompts.tutor import PROMPT as TUTOR_PROMPT
from app.ai.prompts.tutor import VERSION as TUTOR_PROMPT_VERSION
from app.ai.state_machine import (
    SESSION_STATE_GUIDING,
    diagnostic_tactic_prompt,
    mode_prompt,
    state_prompt,
)
from app.models.content import KnowledgePoint, Question
from app.models.system_config import SystemConfig
from app.services import grounding_service as gs
from rag_tagging import new_tag, rag_cleanup, track_config_key, track_question

# =============================================================================
# 常量与工具
# =============================================================================

#: 一段「已渲染好的素材段」样例（由服务层产出，本模块不关心它怎么来的）。
_SEGMENT = (
    "（内部提示，不要念给孩子听）\n"
    "下面是可以参考的接地气素材，缺哪段就不说哪段。\n"
    "不要把素材原样念给孩子，也不要把素材里的说法当成结论。\n"
    "【生活类比】就像去文具店买文具那样，先想好要买哪些文具，再慢慢挑。\n"
    "【知识点】两位数乘除法"
)

#: 全局唯一中文标记（**跨测试文件共享**，见 ``tests/rag_tagging.py``）。
_tag = new_tag


def _legacy_system(
    *,
    state: Optional[str],
    mode: Optional[str],
    error_type: Optional[str] = None,
) -> str:
    """**改造前**的 system 提示拼法（逐字复刻，用来做「逐字一致」比对）。

    这段拼法与本分支改动前的 ``tutor._build_request`` 完全一致 ——
    核心提示 → 阶段后缀 → 模式后缀 → 诊断式追问后缀，**没有素材段**。
    """
    suffixes = [
        part
        for part in (
            state_prompt(state) if state else "",
            mode_prompt(mode),
            diagnostic_tactic_prompt(mode, error_type=error_type) if error_type else "",
        )
        if part
    ]
    return "\n\n".join([TUTOR_PROMPT, *suffixes]) if suffixes else TUTOR_PROMPT


def _build(mode: Optional[str], segment: str = "", **kwargs):
    """调 ``tutor._build_request`` 的薄封装。"""
    params = {
        "session_messages": [{"role": "user", "content": "这题我不会"}],
        "current_error": None,
        "state": SESSION_STATE_GUIDING,
        "mode": mode,
    }
    params.update(kwargs)
    return tutor._build_request(grounding_segment=segment, **params)


# =============================================================================
# 1. 模式闸门：只在 DEMO / JUDGE 注入
# =============================================================================


@pytest.mark.parametrize("mode", [MODE_GUIDE, MODE_HINT])
def test_guide_and_hint_never_inject_grounding(mode):
    """``GUIDE`` / ``HINT`` 零注入（材料里带着答案也绝不能进去）。"""
    _messages, system, _strict = _build(mode, _SEGMENT)
    assert _SEGMENT not in system
    assert "内部提示" not in system
    assert "【生活类比】" not in system
    assert "【知识点】" not in system


@pytest.mark.parametrize("mode", [MODE_DEMO, MODE_JUDGE])
def test_demo_and_judge_inject_grounding(mode):
    """``DEMO`` / ``JUDGE`` 必须注入素材段。"""
    _messages, system, _strict = _build(mode, _SEGMENT)
    assert _SEGMENT in system
    assert "【生活类比】" in system


def test_unguarded_modes_are_exactly_demo_and_judge():
    """注入白名单与 ``answer_guard`` 的跳过白名单**必须一致**。

    两边不一致就会出现两种事故：
    * 白名单比守卫宽 → ``GUIDE`` 拿到素材，泄漏检查被诱发（多一次往返）；
    * 白名单比守卫窄 → ``DEMO`` 讲不出接地气的话，素材白做。
    """
    assert set(UNGUARDED_MODES) == {MODE_DEMO, MODE_JUDGE}
    assert set(UNGUARDED_MODES) == set(MODE_VALUES) - {MODE_GUIDE, MODE_HINT}


def test_grounding_is_the_last_suffix():
    """素材段必须是**第 5 段**（核心提示 → 阶段 → 模式 → 诊断 → 素材）。

    顺序即语义：错因结论先立住，素材只是「讲的时候可以借的比喻」，
    放在诊断前面会让素材盖过错因。
    """
    _messages, system, _strict = _build(
        MODE_DEMO,
        _SEGMENT,
        error_type="unit_mismatch",
    )
    assert system.startswith(TUTOR_PROMPT)
    assert system.index(state_prompt(SESSION_STATE_GUIDING)) < system.index(_SEGMENT)
    assert system.index(mode_prompt(MODE_DEMO)) < system.index(_SEGMENT)
    assert (
        system.index(diagnostic_tactic_prompt(MODE_DEMO, error_type="unit_mismatch"))
        < system.index(_SEGMENT)
    )
    assert system.rindex(_SEGMENT) == len(system) - len(_SEGMENT)


def test_empty_segment_injects_nothing_even_in_demo():
    """素材为空（开关关 / 检索不到）→ 一律不追加。"""
    _messages, system, _strict = _build(MODE_DEMO, "")
    assert _SEGMENT not in system
    assert "【生活类比】" not in system


def test_whitespace_only_segment_injects_nothing():
    """只有空白的素材段也当空串处理（不许拼出「\\n\\n」这种脏后缀）。"""
    _messages, system, _strict = _build(MODE_DEMO, "   \n  ")
    assert "内部提示" not in system
    assert system == _legacy_system(state=SESSION_STATE_GUIDING, mode=MODE_DEMO)


# =============================================================================
# 2. 开关关闭 → 输出与改造前逐字一致
# =============================================================================


@pytest.mark.parametrize("mode", list(MODE_VALUES) + [None])
def test_switch_off_output_is_byte_identical(mode):
    """开关关闭（素材为空）→ system 提示与改造前**逐字一致**。

    比对方式：与本文件里的 ``_legacy_system``（改造前拼法的逐字复刻）
    做 ``==`` 比较 —— 不是「差不多」，不是「包含关系」。
    """
    _messages, system, _strict = _build(mode, "")
    assert system == _legacy_system(state=SESSION_STATE_GUIDING, mode=mode)


@pytest.mark.parametrize("mode", list(MODE_VALUES) + [None])
def test_legacy_call_without_new_argument_is_byte_identical(mode):
    """不传 ``grounding_segment``（老调用方）→ 与显式传空串**逐字一致**。

    这条证明：新增参数有默认值，老调用方**不需要改**，
    而且行为与改动前完全相同。
    """
    legacy_messages, legacy_system, legacy_strict = tutor._build_request(
        session_messages=[{"role": "user", "content": "这题我不会"}],
        current_error=None,
        state=SESSION_STATE_GUIDING,
        mode=mode,
    )
    new_messages, new_system, new_strict = tutor._build_request(
        session_messages=[{"role": "user", "content": "这题我不会"}],
        current_error=None,
        state=SESSION_STATE_GUIDING,
        mode=mode,
        grounding_segment="",
    )
    assert legacy_system == new_system
    assert legacy_messages == new_messages
    assert legacy_strict == new_strict


def test_core_tutor_prompt_is_untouched():
    """核心 PROMPT 是受保护资产：VERSION 不变，且素材**没有**写进核心提示。"""
    assert TUTOR_PROMPT_VERSION == "1.4.0"
    assert "【生活类比】" not in TUTOR_PROMPT
    assert "内部提示" not in TUTOR_PROMPT
    assert "接地气" not in TUTOR_PROMPT


# =============================================================================
# 3. 服务层闸门：开关 + 模式（集成层）
# =============================================================================


@pytest.fixture(autouse=True)
def _auto_cleanup(rag_cleanup):
    """每个用例结束后清掉本模块造的题 / 配置行（共享内存库不清理会污染别人）。"""
    yield


@pytest.fixture(autouse=True)
def _reset_caches():
    """清空检索缓存与配置缓存（进程内缓存会跨用例串味）。"""
    from app.core.system_config import reload_config

    gs.clear_grounding_cache()
    reload_config()
    yield
    gs.clear_grounding_cache()
    reload_config()


def _set_switch(db: Session, value: str) -> None:
    """写 ``RAG_GROUNDING_ENABLED`` 配置行（同事务内可见）。"""
    row = db.execute(
        select(SystemConfig).where(SystemConfig.key == gs.GROUNDING_CONFIG_KEY)
    ).scalar_one_or_none()
    if row is None:
        db.add(SystemConfig(key=gs.GROUNDING_CONFIG_KEY, value=value))
    else:
        row.value = value
    db.flush()
    track_config_key(gs.GROUNDING_CONFIG_KEY)


def _make_question(db: Session, *, content: str, grade: int = 3) -> Question:
    """造一道 active 题。"""
    question = Question(
        content=content,
        grade=grade,
        status="active",
        difficulty=3,
        type_="应用",
    )
    db.add(question)
    db.flush()
    return track_question(question)


def test_service_switch_off_returns_empty_segment(db: Session):
    """开关关闭 → 服务层返回空串（= 上游完全不追加后缀）。"""
    tag = _tag()
    question = _make_question(db, content=f"小明在文具店买了 3 支铅笔。{tag}")
    db.flush()
    _set_switch(db, "false")

    segment = gs.render_session_grounding_segment(
        db, mode=MODE_DEMO, question_id=question.id
    )
    assert segment == ""


def test_service_switch_on_returns_segment(db: Session):
    """开关打开 + 有题 → 服务层吐出素材段（带内部提示标注）。"""
    tag = _tag()
    question = _make_question(db, content=f"小明在文具店买了 3 支铅笔。{tag}")
    db.flush()
    _set_switch(db, "true")

    segment = gs.render_session_grounding_segment(
        db, mode=MODE_DEMO, question_id=question.id
    )
    assert segment, "开关打开却没吐出素材"
    assert "内部提示" in segment


@pytest.mark.parametrize("mode", [MODE_GUIDE, MODE_HINT, None])
def test_service_guide_hint_never_returns_segment(db: Session, mode):
    """模式闸门在**服务层**也要拦一道（双保险）。

    即使开关打开，``GUIDE`` / ``HINT`` 也不该做任何检索 ——
    少一次查库，少一份延迟。
    """
    tag = _tag()
    question = _make_question(db, content=f"小明在文具店买了 3 支铅笔。{tag}")
    db.flush()
    _set_switch(db, "true")

    segment = gs.render_session_grounding_segment(
        db, mode=mode, question_id=question.id
    )
    assert segment == ""


def test_service_tolerates_missing_question(db: Session):
    """会话没关联题 / 题已删 → 不崩，返回空串。"""
    _set_switch(db, "true")
    segment = gs.render_session_grounding_segment(
        db,
        mode=MODE_DEMO,
        question_id=uuid.uuid4(),  # 不存在的题
        student_id=uuid.uuid4(),  # 不存在的学生
    )
    assert segment == ""


# =============================================================================
# 4. 答案泄漏守卫拦截率仍 100%
# =============================================================================


@pytest.mark.parametrize(
    "leaky",
    [
        "答案：x=5",
        "最终答案是 12",
        "所以 x = 5",
        "解得 y=3",
        "解为 4",
    ],
)
def test_answer_leak_still_detected(leaky):
    """加了素材之后，答案泄漏的**判定**一个字都不能松。"""
    assert has_answer_leak(leaky)
    assert not has_answer_leak("你先想想第一步该处理哪部分？")


def test_guarded_reply_still_retries_on_leak(monkeypatch):
    """``GUIDE`` 模式下泄漏仍然触发重生成（拦截率 100%）。"""
    calls = []

    def fake(*, messages, system=None, image_b64=None, timeout=60.0):
        calls.append(list(messages))
        return "答案：x=5" if len(calls) == 1 else "你先想想先移项对不对？"

    monkeypatch.setattr("app.ai.ark_client.ark_respond", fake)
    monkeypatch.setattr("app.ai.answer_guard.ark_respond", fake)

    out = generate_guarded_reply(
        messages=[{"role": "user", "content": "这题咋做"}],
        system="SYS",
        strict=True,
        mode=MODE_GUIDE,
    )
    assert out == "你先想想先移项对不对？"
    assert len(calls) == 2
    assert any(LEAK_RETRY_HINT in m.get("content", "") for m in calls[1])


def test_guarded_reply_exhausted_falls_back(monkeypatch):
    """一直泄漏 → 退回兜底语，**绝不**把答案给孩子。"""

    def fake(*, messages, system=None, image_b64=None, timeout=60.0):
        return "答案：x=5"

    monkeypatch.setattr("app.ai.ark_client.ark_respond", fake)
    monkeypatch.setattr("app.ai.answer_guard.ark_respond", fake)

    out = generate_guarded_reply(
        messages=[{"role": "user", "content": "x"}],
        system="SYS",
        strict=True,
        mode=MODE_GUIDE,
        fallback="兜底引导语",
    )
    assert out == "兜底引导语"


def test_grounding_segment_never_enters_guide_system_prompt():
    """即使素材里混进了答案，``GUIDE`` 的 system 提示也**不会**带上它。

    这条是「GUIDE 零注入」的意义所在：守卫不被诱发 → 不多花一次模型往返。
    """
    leaky_segment = "（内部提示，不要念给孩子听）\n【生活类比】答案：x=5"
    _messages, system, _strict = _build(MODE_GUIDE, leaky_segment)
    assert "x=5" not in system
    assert leaky_segment not in system


def test_demo_mode_skips_leak_check_but_marks_material_as_internal():
    """``DEMO`` 跳过泄漏检查（既有行为），但素材必须带「不要念给孩子听」。"""
    _messages, system, _strict = _build(MODE_DEMO, _SEGMENT)
    assert "不要念给孩子听" in system
