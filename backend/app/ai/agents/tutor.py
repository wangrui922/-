"""Tutor Agent：苏格拉底式引导，产出"下一句对孩子说什么"（docs §C.1 第 3 个 Agent）。

输入整段会话历史，输出**一句**引导语（追问 / 轻提示 / 让他再试一次）。
铁律由 ``prompts.tutor.PROMPT`` 保证：不直接给答案、一次只说一件事。

``current_error`` 是 Diagnosis Agent 的结论，属于**内部信息**，只作为附加
system 提示喂给模型，用来让这一句引导贴着孩子真实卡住的那一步，不会原样念给孩子听。

T5 增补 —— ``mode`` 透传：
    ``mode`` 由 ``app.ai.intent.detect_mode()`` 在 orchestrator 里**定稿**，
    本模块只做纯透传，**不许再猜、再改、再降级**（设计文档 §7.1 铁律 1）：

    * 拼进 system 提示：``TUTOR_PROMPT + state_suffix + mode_suffix``；
    * 决定严格度：``is_strict_guard(state, mode=mode)``；
    * 透传给 ``generate_guarded_reply(mode=mode)``（DEMO/JUDGE 会整段跳过检查）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.ai.answer_guard import generate_guarded_reply
from app.ai.prompts.tutor import PROMPT as TUTOR_PROMPT
from app.ai.state_machine import (
    SESSION_STATE_GUIDING,
    is_strict_guard,
    mode_prompt,
    state_prompt,
)

#: 送进模型的历史对话最大条数（过长既费 token 也稀释当前重点）
MAX_HISTORY: int = 24

#: 允许出现在会话历史里的角色（与 ai_messages.role 的 CHECK 约束一致）
ALLOWED_ROLES = ("user", "assistant", "system")

#: 模型返回空内容时的兜底引导语
FALLBACK_REPLY: str = "你先跟我说说，这道题你读下来，卡在哪一步了？"

_ERROR_HINT_TEMPLATE: str = (
    "（内部提示，不要念给孩子听）刚才诊断出的问题是：{error}。"
    "下一句引导请贴着这个点，仍然只说一件事，不要直接给答案。"
)


def _sanitize(session_messages: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    """清洗会话历史：丢掉非法结构 / 空内容，角色收敛到枚举，并只保留最近若干条。

    Args:
        session_messages: 原始历史，元素形如 ``{"role": "user", "content": "..."}``。

    Returns:
        可直接交给 ``ark_respond`` 的干净消息列表。
    """
    cleaned: List[Dict[str, str]] = []
    for item in session_messages or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        role = str(item.get("role") or "user").strip().lower()
        if role not in ALLOWED_ROLES:
            role = "user"
        cleaned.append({"role": role, "content": content})
    return cleaned[-MAX_HISTORY:]


def next_question(
    *,
    session_messages: List[Dict[str, Any]],
    current_error: Optional[str] = None,
    state: Optional[str] = None,
    mode: Optional[str] = None,
) -> str:
    """生成下一句引导语（经答案泄漏检查器兜底）。

    Args:
        session_messages: 会话历史，元素形如
            ``{"role": "user"|"assistant"|"system", "content": str}``；可为空列表
            （空列表表示刚开场，由模型自行起头）。
        current_error: Diagnosis Agent 给出的错因摘要，可为空；非空时作为内部
            system 提示追加到历史末尾，引导模型对准这个点。
        state: 当前教学阶段（见 ``app.ai.state_machine.SESSION_STATE_*``），
            非空时注入阶段提示并据此决定答案检查器的严格度。
        mode: 本轮响应模式（见 ``app.ai.intent.MODE_*``），由 orchestrator 用
            ``intent.detect_mode()`` **定稿**后传入。**关键字参数且默认 None**，
            ``None`` = 未指定 = 行为与改造前完全一致。本函数只透传，不改写。

    Returns:
        一句引导语（已 strip）；命中泄漏且重生成耗尽时给 ``FALLBACK_REPLY`` 兜底。
        ``mode`` 为 DEMO / JUDGE 时不会被兜底替换（guard 整段跳过）。

    Raises:
        ArkError: 方舟调用失败或消息结构非法。
    """
    messages = _sanitize(session_messages)

    hint = (current_error or "").strip()
    if hint:
        messages.append({"role": "system", "content": _ERROR_HINT_TEMPLATE.format(error=hint)})

    if not messages:
        # 没有任何上下文时给个开场指令，避免空 input 直接被 ark_client 拒掉
        messages.append({"role": "user", "content": "我们开始吧，请你先问我第一个问题。"})

    # 系统提示拼接顺序固定：核心提示词 → 阶段后缀 → 模式后缀（模式最后，优先级最高）
    suffixes = [part for part in (state_prompt(state) if state else "", mode_prompt(mode)) if part]
    system = "\n\n".join([TUTOR_PROMPT, *suffixes]) if suffixes else TUTOR_PROMPT

    # 严格度：DEMO / JUDGE 恒 False；其余保持「开场/引导严格，练习/总结宽松」。
    # state 为空时按 guiding 兜底 —— 与改造前的 `strict=True` 完全等价。
    strict = is_strict_guard(state or SESSION_STATE_GUIDING, mode=mode)

    return generate_guarded_reply(
        messages=messages,
        system=system,
        strict=strict,
        mode=mode,
        fallback=FALLBACK_REPLY,
    )


__all__ = ["next_question", "MAX_HISTORY", "ALLOWED_ROLES", "FALLBACK_REPLY"]
