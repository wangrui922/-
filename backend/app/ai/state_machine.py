"""第三层防答案机器：教学状态机（T04B）。

把辅导进度持久化到 ``ai_sessions.session_state`` + ``step``，并在每轮开始时
读回注入 tutor 提示，让模型知道「当前处于哪个阶段第几步」；同时驱动答案检查器
的严格度（引导 / 开场阶段严格，练习 / 总结阶段宽松）。

本模块为纯逻辑（只依赖标准库），不触碰 DB，是阶段枚举的权威来源。
"""

from __future__ import annotations

#: 阶段枚举
SESSION_STATE_INTRO = "intro"            # 刚开场，提出第一个引导问题
SESSION_STATE_GUIDING = "guiding"        # 多轮引导中
SESSION_STATE_PRACTICING = "practicing"  # 已出题，孩子在做练习
SESSION_STATE_SUMMARIZING = "summarizing"  # 收尾总结
SESSION_STATE_CLOSED = "closed"          # 会话结束

SESSION_STATE_VALUES = (
    SESSION_STATE_INTRO,
    SESSION_STATE_GUIDING,
    SESSION_STATE_PRACTICING,
    SESSION_STATE_SUMMARIZING,
    SESSION_STATE_CLOSED,
)

#: 各阶段注入 tutor 系统提示的后缀
_STATE_PROMPTS = {
    SESSION_STATE_INTRO: (
        "【教学阶段】当前是开场，请提出第一个引导问题，不要直接给答案。"
    ),
    SESSION_STATE_GUIDING: (
        "【教学阶段】当前处于引导阶段（第 {step} 步），请继续用提问引导孩子自己"
        "思考，一次只说一件事，不要直接给答案。"
    ),
    SESSION_STATE_PRACTICING: (
        "【教学阶段】当前是练习阶段，孩子正在做题，可以针对他的作答给讲解和"
        "必要时的正确答案。"
    ),
    SESSION_STATE_SUMMARIZING: (
        "【教学阶段】当前是总结阶段，可以给孩子一个简洁的回顾与结论。"
    ),
    SESSION_STATE_CLOSED: (
        "【教学阶段】本次辅导已结束。"
    ),
}


def state_prompt(state: str, step: int = 0) -> str:
    """返回追加到 TUTOR_PROMPT 的阶段提示后缀。

    Args:
        state: 阶段值（见 SESSION_STATE_*）。非法值回退到 guiding。
        step: 当前步数（仅 guiding 阶段用于提示）。
    """
    template = _STATE_PROMPTS.get(state, _STATE_PROMPTS[SESSION_STATE_GUIDING])
    try:
        return template.format(step=step)
    except (KeyError, IndexError):
        return template


def is_strict_guard(state: str) -> bool:
    """该阶段是否严格禁止泄答案。

    intro / guiding -> True（严格）；practicing / summarizing / 其他 -> False（宽松）。
    """
    return state in (SESSION_STATE_INTRO, SESSION_STATE_GUIDING)


__all__ = [
    "SESSION_STATE_INTRO",
    "SESSION_STATE_GUIDING",
    "SESSION_STATE_PRACTICING",
    "SESSION_STATE_SUMMARIZING",
    "SESSION_STATE_CLOSED",
    "SESSION_STATE_VALUES",
    "state_prompt",
    "is_strict_guard",
]
