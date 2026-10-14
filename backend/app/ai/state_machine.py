"""第三层防答案机器：教学状态机（T04B）。

把辅导进度持久化到 ``ai_sessions.session_state`` + ``step``，并在每轮开始时
读回注入 tutor 提示，让模型知道「当前处于哪个阶段第几步」；同时驱动答案检查器
的严格度（引导 / 开场阶段严格，练习 / 总结阶段宽松）。

本模块为纯逻辑（只依赖标准库），不触碰 DB，是阶段枚举的权威来源。

T2 增补：引入**响应模式** ``mode``（``app.ai.intent`` 是其唯一真相源），
把「严格度」从「只看教学阶段」升级为「阶段 × 模式」二维：

* ``mode_prompt(mode)``：给 tutor 系统提示追加一段模式行为说明；
* ``is_strict_guard(state, *, mode=None)``：``DEMO`` / ``JUDGE`` 恒不严格，
  其余保持原有的按阶段判定。

⚠️ 依赖方向是单向的：``state_machine`` → ``intent``，**禁止反向 import**。
"""

from __future__ import annotations

from typing import Dict, Optional

from app.ai.intent import (  # mode 常量的唯一定义处，禁止在本文件重写字面量
    MODE_DEMO,
    MODE_GUIDE,
    MODE_HINT,
    MODE_JUDGE,
    normalize_mode,
)
from app.ai.prompts import mathdial  # 方向八：MathDial 诊断式话术库
from app.core.error_categories import error_label, normalize_error

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


#: 各响应模式注入 tutor 系统提示的后缀（T2 · 与 ``_STATE_PROMPTS`` 同级同性质）
#:
#: 这一段是「三重锁死」的破法之一：模型此前只收到阶段提示（永远是「不要直接
#: 给答案」），现在再叠一层模式提示，明确告诉它这一轮到底该引导还是该讲。
_MODE_PROMPTS: Dict[str, str] = {
    MODE_GUIDE: (
        "【响应模式】引导模式：继续用提问带孩子自己想，一次只说一件事，"
        "不要直接给答案。"
    ),
    MODE_HINT: (
        "【响应模式】提示模式：孩子卡住了在要线索。给**一条**方向性提示"
        "（贴着这道题的数字和情境），点到为止，然后抛一个他够得着的小问题，"
        "仍然不要把结论说出来。"
    ),
    MODE_DEMO: (
        "【响应模式】讲解模式：孩子已经明确请你讲，请**从头完整演示**这道题的"
        "解题过程，把每一步为什么这么做讲清楚，并且**说出最终结果**——这一轮"
        "不需要藏答案。讲完必须回扣一句追问（例如「这里面哪一步你想再听一遍？」），"
        "把孩子拉回思考。"
    ),
    MODE_JUDGE: (
        "【响应模式】评判模式：孩子给出了他的答案或结论。请**明确说对还是错**，"
        "错的话指出是哪一步出的问题（读题 / 方法 / 计算 / 知识点 / 检查），"
        "对的话追问一句「你是怎么想出来的」确认他真懂。这一轮可以说出正确结果。"
    ),
}


def mode_prompt(mode: Optional[str]) -> str:
    """返回追加到 TUTOR_PROMPT 的**响应模式**提示后缀。

    Args:
        mode: 响应模式（见 ``app.ai.intent.MODE_*``）。``None`` 或非法值返回空串，
            此时调用方不追加任何模式提示，行为与改造前完全一致。

    Returns:
        模式提示文本；无需追加时为空串。

    Examples:
        >>> mode_prompt(None)
        ''
        >>> mode_prompt("nonsense")
        ''
        >>> mode_prompt("DEMO").startswith("【响应模式】讲解模式")
        True
    """
    normalized = normalize_mode(mode)
    if normalized is None:
        return ""
    return _MODE_PROMPTS.get(normalized, "")


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


def is_strict_guard(state: str, *, mode: Optional[str] = None) -> bool:
    """该阶段 × 该模式下是否严格禁止泄答案。

    判定顺序（**模式优先于阶段**）：

    1. ``mode`` 为 ``DEMO`` / ``JUDGE`` → 恒 ``False``。这两个模式的正确行为就是
       把结果说出来，再拿严格闸门去拦就是「写死的模板」的根因；
    2. 其余（``None`` / ``GUIDE`` / ``HINT`` / 非法值）→ 保持原有按阶段判定：
       intro / guiding -> ``True``（严格）；practicing / summarizing / 其他 -> ``False``。

    Args:
        state: 阶段值（见 ``SESSION_STATE_*``）。
        mode: 响应模式（见 ``app.ai.intent.MODE_*``）。**关键字参数且默认 None**，
            ``None`` 语义 = 「未指定」= 走老逻辑，保证存量调用与存量测试零改动。

    Returns:
        需要严格检查答案泄漏时返回 ``True``。

    Examples:
        >>> is_strict_guard("guiding")
        True
        >>> is_strict_guard("guiding", mode="DEMO")
        False
        >>> is_strict_guard("guiding", mode="HINT")
        True
        >>> is_strict_guard("practicing")
        False
    """
    if normalize_mode(mode) in (MODE_DEMO, MODE_JUDGE):
        return False
    return state in (SESSION_STATE_INTRO, SESSION_STATE_GUIDING)


# ==================================================================== 诊断式追问
#: 方向八新增：按「响应模式 × 错因」挑选的**教学环节**（先诊断再教学）。
#:
#: * GUIDE / JUDGE → 诊断：先让孩子说出思路，定位卡点再干预；
#: * HINT → 诊断 + 提示：快速确认卡点后，给一条贴着题目的方向性线索；
#: * DEMO → 讲解：孩子已明确请讲，重点是把每一步「为什么」讲透，讲完回扣追问。
#:
#: JUDGE 虽属 UNGUARDED_MODES（可以说结果），但「先诊断」仍适用：先让孩子
#: 确认「你是怎么想出来的」，避免他蒙对就当掌握。
_MODE_TO_TACTIC: Dict[str, str] = {
    MODE_GUIDE: mathdial.TACTIC_DIAGNOSE,
    MODE_HINT: mathdial.TACTIC_HINT,
    MODE_DEMO: mathdial.TACTIC_ELABORATE,
    MODE_JUDGE: mathdial.TACTIC_DIAGNOSE,
}


def diagnostic_tactic_prompt(
    mode: Optional[str],
    *,
    error_type: Optional[str] = None,
) -> str:
    """返回追加到 tutor 系统提示的**诊断式追问**后缀（方向八 · 先诊断再教学）。

    这是 L2（状态机）→ L3（话术生成）的诊断注入点：把 MathDial 教学属性和
    universal-diagnostic-tutor-skill 的「先诊断再教学」翻译成一段机器指令，
    让模型的引导话术从「固定模板」升级为「先让孩子暴露思路，再按错因干预」。

    Args:
        mode: 本轮响应模式（``app.ai.intent.MODE_*``）。``None`` / 非法值
            返回空串——调用方不追加任何诊断指令，行为与改造前完全一致。
        error_type: 错因机器码（``app.core.error_categories`` 五码之一，
            或中文标签等任意脏输入，本函数内部统一收敛）。``None`` / 收敛
            失败时给通用诊断指令（不绑定具体错因）。

    Returns:
        诊断式追问指令文本；无需追加时为空串。

    Examples:
        >>> diagnostic_tactic_prompt(None)
        ''
        >>> diagnostic_tactic_prompt("nonsense")
        ''
        >>> diagnostic_tactic_prompt("GUIDE").startswith("【诊断式追问】")
        True
        >>> "读题错" in diagnostic_tactic_prompt("HINT", error_type="READ")
        True
    """
    normalized = normalize_mode(mode)
    if normalized is None:
        return ""
    tactic = _MODE_TO_TACTIC.get(normalized, mathdial.TACTIC_DIAGNOSE)

    code = normalize_error(error_type)
    label = error_label(code) if code else ""
    lines = [
        "【诊断式追问】" + mathdial.DIAGNOSE_FIRST_INSTRUCTION,
    ]
    if label and label != "其他":
        lines.append(f"本轮错因（内部参考，不要念给孩子）：{label}。")
    lines.append("本轮教学动作（" + mathdial.tactic_label(tactic) + "）：")
    lines.append(mathdial.tactic_instruction(code, tactic))
    phrase = mathdial.pick_phrase(code, tactic)
    if phrase:
        lines.append(f"参考问法（数字 / 情境必须换成当前题的真实对象）：「{phrase}」")
    return "\n".join(lines)


__all__ = [
    "SESSION_STATE_INTRO",
    "SESSION_STATE_GUIDING",
    "SESSION_STATE_PRACTICING",
    "SESSION_STATE_SUMMARIZING",
    "SESSION_STATE_CLOSED",
    "SESSION_STATE_VALUES",
    "state_prompt",
    "mode_prompt",
    "is_strict_guard",
    "diagnostic_tactic_prompt",
    # 从 intent 转出，方便下游只 import 一个模块（真相源仍是 app.ai.intent）
    "MODE_GUIDE",
    "MODE_HINT",
    "MODE_DEMO",
    "MODE_JUDGE",
]
