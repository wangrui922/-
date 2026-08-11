"""第二层防答案机器：输出检查器（T04B）。

职责：tutor 生成回复后，扫描是否「直接给出了最终答案 / 解方程结果」。命中则
触发重生成（追加更严厉的内部提示），最多重试若干次仍命中则退回安全兜底引导
语，绝不把答案吐给孩子。

设计要点：
    * 纯函数、无 DB 依赖，便于离线单测；
    * 严格度按教学阶段切换（intro/guiding 严格，practicing/summarizing 宽松），
      由调用方经 ``strict`` 参数传入；
    * 复用 ``ark_respond``，调用点 ``app.ai.answer_guard.ark_respond`` 已被
      ``t03_helpers.ARK_PATCH_TARGETS`` 覆盖，离线测试可正常插桩。

T3 增补 —— ``mode`` 直通闸门：
    ``mode`` 为 ``DEMO`` / ``JUDGE`` 时**整段跳过**泄漏检查（不扫描、不重生成、
    不替兜底），在 ``ark_respond`` 之后立刻原样返回。

    ⚠️ 为什么是「完全跳过」而不是「降级到 LOOSE」：LOOSE 仍拦「答案：」
    「最终答案」，而 DEMO 的正确行为恰恰就是要说出「所以答案是 12」。半放开
    等于没放开 —— 这正是用户投诉「对面是写死的模板」的直接根因。
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

from app.ai.ark_client import ark_respond
from app.ai.intent import UNGUARDED_MODES, normalize_mode  # mode 常量唯一真相源

logger = logging.getLogger("app.ai.answer_guard")

#: 严格模式下的答案泄漏特征（低误报优先）：
#   - 显式「答案：/答案=/最终答案/正确答案」
#   - 方程结论：「所以 x=5」「解得 y=3」「结果为 12」
_ANSWER_LEAK_PATTERNS_STRICT: List[str] = [
    r"答案\s*[:：=＝]\s*\S+",
    r"最终答案",
    r"正确答案\s*[:：=＝]",
    r"所以[，,。.\s]*[a-z一-龥0-9]+\s*[=＝]\s*\S+",
    r"解得\s*[a-z一-龥0-9]+\s*[=＝]",
    r"解为\s*[a-z一-龥0-9]+",
]

#: 宽松模式（练习 / 总结阶段）只拦最露骨的答案宣告
_ANSWER_LEAK_PATTERNS_LOOSE: List[str] = [
    r"答案\s*[:：=＝]\s*\S+",
    r"最终答案",
    r"正确答案\s*[:：=＝]",
]

_ANSWER_LEAK_RE_STRICT = [re.compile(p) for p in _ANSWER_LEAK_PATTERNS_STRICT]
_ANSWER_LEAK_RE_LOOSE = [re.compile(p) for p in _ANSWER_LEAK_PATTERNS_LOOSE]

#: 命中泄漏后追加给模型的内部 system 提示（不念给孩子听）
LEAK_RETRY_HINT: str = (
    "（内部提示，不要念给孩子听）你上一轮的回复直接给出了最终答案或计算结果，"
    "这违反了教学原则。请改为：只用提问引导孩子自己思考，绝对不要出现"
    "「答案 / 最终答案 / 结果=」这类直接给结论的表述。"
)

#: 最多重生成次数（总尝试 = max_retries + 1）
MAX_GUARD_RETRIES: int = 2

#: 默认安全兜底（tutor 会传入自己的 FALLBACK_REPLY 覆盖它）
#:
#: ⚠️ **仅 strict 路径可达**：``mode`` 为 DEMO / JUDGE 时整段跳过泄漏检查，
#: 永远不会走到「重生成耗尽 → 替兜底」这一步。GUIDE / HINT 仍需要它兜底，
#: 所以不能删。改动前请先确认没有把讲解模式又拖回这条路径。
_DEFAULT_FALLBACK: str = "你先跟我说说，这道题你读下来，卡在哪一步了？"


def has_answer_leak(text: str, *, strict: bool = True) -> bool:
    """判断一段回复是否直接泄露了答案。

    Args:
        text: 模型回复文本。
        strict: True=严格（引导 / 开场阶段，几乎不允许任何结论式表述）；
                False=宽松（练习 / 总结阶段，只拦最露骨的答案宣告）。

    Returns:
        命中泄漏特征返回 True。
    """
    if not text:
        return False
    patterns = _ANSWER_LEAK_RE_STRICT if strict else _ANSWER_LEAK_RE_LOOSE
    return any(p.search(text) for p in patterns)


def generate_guarded_reply(
    *,
    messages: List[dict],
    system: Optional[str] = None,
    strict: bool = True,
    mode: Optional[str] = None,
    max_retries: int = MAX_GUARD_RETRIES,
    fallback: str = _DEFAULT_FALLBACK,
) -> str:
    """带答案泄漏检查的回复生成。

    流程：调用 ``ark_respond`` 生成回复 → **若 ``mode`` 在 ``UNGUARDED_MODES``
    里则立刻原样返回** → 否则若 ``has_answer_leak`` 命中且仍有重试额度 →
    追加 ``LEAK_RETRY_HINT`` 重新生成 → 重复；额度用尽仍命中则返回 ``fallback``。

    Args:
        messages: 会话消息（与 ``ark_respond`` 一致）。
        system: 系统提示词。
        strict: 是否严格检查（``mode`` 命中直通时该参数被忽略）。
        mode: 本轮响应模式（见 ``app.ai.intent.MODE_*``）。**关键字参数且默认
            None**，``None`` = 未指定 = 走老逻辑，存量调用与存量测试零改动。
            为 ``DEMO`` / ``JUDGE`` 时整段跳过泄漏检查，``ark_respond``
            **只被调用 1 次**。
        max_retries: 泄漏后最大重生成次数。
        fallback: 兜底引导语（tutor 传入其 ``FALLBACK_REPLY``）。

    Returns:
        经检查的最终回复文本；或安全兜底语。
    """
    reply = (ark_respond(messages=messages, system=system) or "").strip()

    # ---- T3 直通闸门：讲解 / 评判模式不做任何扫描，原样放行 ------------------
    normalized_mode = normalize_mode(mode)
    if normalized_mode in UNGUARDED_MODES:
        logger.info("mode=%s 跳过答案泄漏检查，原样返回模型回复", normalized_mode)
        return reply or fallback

    attempts = 0
    while has_answer_leak(reply, strict=strict) and attempts < max_retries:
        attempts += 1
        logger.warning("检测到答案泄漏，第 %s/%s 次重新生成", attempts, max_retries)
        messages = list(messages) + [{"role": "system", "content": LEAK_RETRY_HINT}]
        reply = (ark_respond(messages=messages, system=system) or "").strip()

    if has_answer_leak(reply, strict=strict):
        logger.error("重生成 %s 次后仍泄漏答案，退回安全兜底引导语", max_retries)
        return fallback

    return reply or fallback


__all__ = [
    "has_answer_leak",
    "generate_guarded_reply",
    "LEAK_RETRY_HINT",
    "MAX_GUARD_RETRIES",
]
