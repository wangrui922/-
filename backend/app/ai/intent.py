"""学生意图识别：把「孩子这一轮说的话」映射成响应模式 ``mode``（T2 · 后端地基）。

本模块是整条 ``mode`` 透传链路的**起点与唯一定稿处**（设计文档 §7.1 铁律 1）：

    前端(可选显式) → api/chat.ChatMessage.mode → orchestrator.continue_session(mode=)
       → intent.detect_mode(override=mode) → **定稿 mode**
       → tutor.next_question(mode=) → state_machine / answer_guard

硬约束（不许破）：

* **零 DB、零网络、零新依赖**：只用标准库 ``re`` / ``typing``，可离线单测；
* **不额外调 LLM**：短句意图识别用正则准确率已 >90%，多一次方舟往返等于把
  用户体感延迟翻倍（设计文档 §8.1 D2）；
* **mode 枚举的唯一真相源就是本文件**。``state_machine`` / ``answer_guard`` /
  ``orchestrator`` 一律 ``from app.ai.intent import MODE_DEMO, ...``，
  **禁止任何文件重新写一遍字符串字面量**。

识别优先级（先命中先返回，见设计文档 §3.2）::

    1. override（前端快捷按钮显式传值，合法即直接采用，不做任何识别）
    2. JUDGE   学生给出了答案 / 问对不对
    3. DEMO    学生明确请你讲 / 说不会
    4. HINT    学生要提示 / 要思路
    5. GUIDE   兜底默认

> JUDGE 优先于 DEMO：「我算出来是 12，对不对」同时含数值和「对不对」，
> 学生诉求是评判而不是重讲。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Pattern, Sequence, Tuple

# ==================================================================== mode 枚举
#: 引导（默认）：苏格拉底式一次一问，严格不给答案
MODE_GUIDE: str = "GUIDE"
#: 提示：给一条方向性线索 + 一个小问题，仍不给结论
MODE_HINT: str = "HINT"
#: 演示讲解：从头完整讲，**允许说出结果**，讲完回扣追问
MODE_DEMO: str = "DEMO"
#: 评判：接收学生给的答案，明确说对 / 错 + 错在哪一步
MODE_JUDGE: str = "JUDGE"

#: 全部合法取值（顺序即优先级，GUIDE 恒为兜底）
MODE_VALUES: Tuple[str, ...] = (MODE_GUIDE, MODE_HINT, MODE_DEMO, MODE_JUDGE)

#: 未识别 / 非法输入时的兜底 mode
DEFAULT_MODE: str = MODE_GUIDE

#: 完全跳过 ``answer_guard`` 泄漏检查的模式（设计文档 §3.1）
#:
#: ⚠️ 是**完全跳过**而不是「换 LOOSE 正则」：LOOSE 仍拦「答案：」「最终答案」，
#: 而 DEMO 的正确行为就是要说出「所以答案是 12」。半放开等于没放开。
UNGUARDED_MODES: Tuple[str, ...] = (MODE_DEMO, MODE_JUDGE)

#: 前端模式指示器用的展示标签（后端只做映射，前端不硬编码中文）
MODE_LABELS: Dict[str, str] = {
    MODE_GUIDE: "引导中",
    MODE_HINT: "提示中",
    MODE_DEMO: "讲解中",
    MODE_JUDGE: "评判中",
}

# ============================================================== 识别参数（可调）
#: 参与冷却判定的历史窗口（最近 N 条消息）
HISTORY_WINDOW: int = 6

#: 连续多少轮同一个非 GUIDE mode 后强制回切 GUIDE（PRD P1-1 防锁死）
COOLDOWN_ROUNDS: int = 2

#: 各来源的置信度（仅用于日志排障，不参与任何分支判断）
_CONFIDENCE: Dict[str, float] = {
    "override": 1.0,
    "regex": 0.9,
    "cooldown": 0.8,
    "default": 0.5,
}


class DetectResult(NamedTuple):
    """一次意图识别的完整结论（``detect_mode`` 只取其中的 ``mode``）。

    Attributes:
        mode: 定稿的响应模式，必为 ``MODE_VALUES`` 之一。
        confidence: 置信度 0.0~1.0，**仅供日志/埋点**，下游不得据此改写 mode。
        source: 来源，``override`` / ``regex`` / ``cooldown`` / ``default``。
        matched: 命中的规则片段，仅用于排障，可为空串。
    """

    mode: str
    confidence: float
    source: str
    matched: str


# ================================================================ 正则规则表
# 说明：全部在模块级预编译，避免每轮请求重复编译。
#      规则来源为设计文档 §3.2 表格 + PRD 1.2 节真实用户语料。

#: JUDGE 正向特征：学生给出了结论 / 主动要评判
_JUDGE_PATTERNS: Tuple[str, ...] = (
    r"(答案|结果|我算出来|我算的|我得出|我得到|我觉得是|我选|应该是)\s*[是为:：=＝]?\s*\S+",
    r"我(做|算|写|填)完了",
    r"对不对|对吗|对么|对不",
    r"是不是这样|这样对|这么做对|做得对",
    r"帮我(看看|检查|批改)",
    r"检查一下",
    r"我这样(做|算|想)",
)

#: JUDGE 反向否决：命中任意一条则**不判 JUDGE**（多半是在要答案 = DEMO）
#:
#: 反例保护：「你告诉我答案吧」会被 ``答案\s*\S+`` 误伤成 JUDGE，
#: 但学生诉求显然是要讲解。
_JUDGE_NEGATIVE_PATTERNS: Tuple[str, ...] = (
    r"(告诉|给|公布|说出|讲|写出)\s*(我|出)?\s*(正确)?答案",
    r"不知道(答案|结果)",
    r"答案是(多少|什么|啥|几)",
    r"结果是(多少|什么|啥|几)",
)

#: DEMO 正向特征：明确说不会 / 明确请老师讲
#:
#: 反例保护：要求「我」等主语前缀或「不会做/不会算」等动宾结构，
#: 把纯感叹的「不会吧」「不会真的吧」排除在外。
_DEMO_PATTERNS: Tuple[str, ...] = (
    r"我(真的|实在|就是|完全|一点儿?也|一点儿?都)?不会(?![吧呗嘛])",
    r"不会做|不会算|不会解|不会写",
    r"不知道(怎么|咋|如何)(做|算|解|下手|开始|入手)",
    r"(完全|根本)(不懂|没懂|看不懂)",
    r"(你|老师)?\s*(给我)?(讲|说|解释|演示|做|算|写)\s*(一)?(遍|下|次|个)",
    r"直接(告诉|给|说)\s*我",
    r"把(整道|这道|那道|这|那)?\s*题?\s*(给我)?(解答|讲解|讲|做|算|解)",
    r"完整(的)?(过程|步骤|解答|解题|思路)",
    r"看演示|演示(一)?下|做个示范|示范(一)?下",
    r"教我(怎么|怎样|如何)?(做|算|解)",
    r"(告诉|公布|给)\s*(我)?\s*(正确)?答案",
    r"答案是(多少|什么|啥|几)",
)

#: HINT 正向特征：要方向、要线索，但没要完整解答
_HINT_PATTERNS: Tuple[str, ...] = (
    r"给\s*(点|个|些|一点)?\s*提示",
    r"提示(一)?下|提醒(我)?(一)?下|点(拨|拨一)下",
    r"给\s*(个|点|条)?\s*思路",
    r"从哪(儿|里)?\s*(开始|下手|入手|想)",
    r"第一步(是什么|是啥|怎么|该|要|干什么)",
    r"有(没有|什么)\s*(提示|线索|思路|窍门)",
    r"能不能\s*(提示|点)",
    r"卡住了|卡在",
)


def _compile(patterns: Iterable[str]) -> Tuple[Pattern[str], ...]:
    """把规则字符串批量预编译成正则对象。"""
    return tuple(re.compile(p) for p in patterns)


_JUDGE_RE = _compile(_JUDGE_PATTERNS)
_JUDGE_NEGATIVE_RE = _compile(_JUDGE_NEGATIVE_PATTERNS)
_DEMO_RE = _compile(_DEMO_PATTERNS)
_HINT_RE = _compile(_HINT_PATTERNS)

#: 规则表：``(mode, 匹配函数)``，顺序即优先级（设计文档 §3.2）
#: 在下方 ``_match_judge`` / ``_match_demo`` / ``_match_hint`` 定义后完成装配。
_RULES: List[Tuple[str, Any]] = []


def _first_hit(text: str, regexes: Sequence[Pattern[str]]) -> str:
    """返回第一个命中的规则原文（用于日志），没命中返回空串。"""
    for regex in regexes:
        if regex.search(text):
            return regex.pattern
    return ""


def _match_judge(text: str) -> str:
    """是否为 JUDGE（学生给答案 / 要评判）。命中返回规则片段，否则空串。"""
    if _first_hit(text, _JUDGE_NEGATIVE_RE):
        return ""  # 是在「要答案」，交给 DEMO 处理
    return _first_hit(text, _JUDGE_RE)


def _match_demo(text: str) -> str:
    """是否为 DEMO（说不会 / 请老师完整讲）。命中返回规则片段，否则空串。"""
    return _first_hit(text, _DEMO_RE)


def _match_hint(text: str) -> str:
    """是否为 HINT（要提示 / 要思路）。命中返回规则片段，否则空串。"""
    return _first_hit(text, _HINT_RE)


_RULES = [
    (MODE_JUDGE, _match_judge),
    (MODE_DEMO, _match_demo),
    (MODE_HINT, _match_hint),
]


# ==================================================================== 对外函数
def normalize_mode(raw: Any) -> Optional[str]:
    """把任意脏输入收敛成合法 mode，收敛不了返回 ``None``。

    ``None`` 的语义是「未指定」，而不是「GUIDE」——调用方据此决定要不要走识别。

    Args:
        raw: 任意输入（前端传的字符串、``None``、枚举对象……）。

    Returns:
        ``MODE_VALUES`` 之一；无法识别时返回 ``None``。

    Examples:
        >>> normalize_mode("demo")
        'DEMO'
        >>> normalize_mode("  Judge ")
        'JUDGE'
        >>> normalize_mode("讲解") is None
        True
        >>> normalize_mode(None) is None
        True
    """
    value = str(raw or "").strip().upper()
    if not value:
        return None
    return value if value in MODE_VALUES else None


def _recent_user_texts(history: Optional[Sequence[Any]], limit: int) -> List[str]:
    """从会话历史里取最近 ``limit`` 条**学生原话**（按时间升序）。

    Args:
        history: ``[{"role": "user"|"assistant"|"system", "content": str}]``；
            允许为 ``None`` / 含脏元素，脏元素直接跳过。
        limit: 最多取几条。

    Returns:
        学生原话列表，最旧的在前；不足 ``limit`` 条时按实际条数返回。
    """
    if not history:
        return []

    texts: List[str] = []
    for item in list(history)[-HISTORY_WINDOW:]:
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        content = str(item.get("content") or "").strip()
        if content:
            texts.append(content)
    return texts[-limit:] if limit > 0 else []


def _match_mode(text: str) -> Tuple[str, str, str]:
    """纯规则匹配（**不含**冷却、不含 override），供 ``detect`` 与冷却复用。

    Args:
        text: 待识别文本。

    Returns:
        ``(mode, source, matched)``；没命中任何规则时为
        ``(MODE_GUIDE, "default", "")``。
    """
    content = (text or "").strip()
    if not content:
        return DEFAULT_MODE, "default", ""

    for mode, matcher in _RULES:
        matched = matcher(content)
        if matched:
            return mode, "regex", matched

    return DEFAULT_MODE, "default", ""


def _cooldown(history: Optional[Sequence[Any]], mode: str) -> bool:
    """是否触发冷却回切（连续 ``COOLDOWN_ROUNDS`` 轮同一非 GUIDE mode）。

    防的是「学生一句『不会』把整场对话锁死在 DEMO」（PRD P1-1）：
    前两轮都被判成同一个非 GUIDE mode，且本轮又是它 → 第 3 轮强制回 GUIDE，
    把孩子拉回思考主线。

    Args:
        history: 会话历史（**不含**本轮学生这句话）。
        mode: 本轮规则识别出的 mode。

    Returns:
        需要强制回切 GUIDE 时返回 ``True``。
    """
    if mode == MODE_GUIDE or COOLDOWN_ROUNDS <= 0:
        return False

    recent = _recent_user_texts(history, COOLDOWN_ROUNDS)
    if len(recent) < COOLDOWN_ROUNDS:
        return False

    return all(_match_mode(text)[0] == mode for text in recent)


def detect(
    *,
    text: str,
    history: Optional[Sequence[Any]] = None,
    state: Optional[str] = None,
    override: Optional[str] = None,
) -> DetectResult:
    """识别学生这一轮的响应模式，返回带来源与置信度的完整结论。

    Args:
        text: 学生本轮原话（必填，空串按 GUIDE 兜底）。
        history: 最近若干轮会话历史，元素形如
            ``{"role": "user"|"assistant"|"system", "content": str}``；
            **不含**本轮这句话。仅用于冷却回切判定。
        state: 当前教学阶段（``app.ai.state_machine.SESSION_STATE_*``）。
            当前版本不参与判定，保留入参以便后续按阶段调整规则，不改签名。
        override: 前端快捷按钮显式传来的 mode；合法时**直接采用**，
            不做任何识别、不受冷却影响（显式 > 猜测）。

    Returns:
        ``DetectResult(mode, confidence, source, matched)``，``mode`` 必合法。

    Examples:
        >>> detect(text="我不会，你给我讲一遍").mode
        'DEMO'
        >>> detect(text="我算出来是 12，对不对").mode
        'JUDGE'
        >>> detect(text="给点提示").mode
        'HINT'
        >>> detect(text="不会吧").mode
        'GUIDE'
        >>> detect(text="随便说点什么", override="demo").source
        'override'
    """
    forced = normalize_mode(override)
    if forced is not None:
        return DetectResult(forced, _CONFIDENCE["override"], "override", "override")

    mode, source, matched = _match_mode(text)

    if _cooldown(history, mode):
        return DetectResult(
            MODE_GUIDE,
            _CONFIDENCE["cooldown"],
            "cooldown",
            f"cooldown<{mode}>",
        )

    return DetectResult(mode, _CONFIDENCE.get(source, 0.5), source, matched)


def detect_mode(
    *,
    text: str,
    history: Optional[Sequence[Any]] = None,
    state: Optional[str] = None,
    override: Optional[str] = None,
) -> str:
    """``detect`` 的收敛版：只返回定稿的 mode 字符串。

    这是**唯一有权定稿 mode 的入口**（设计文档 §7.1 铁律 1）。下游任何一层
    （tutor / state_machine / answer_guard / prompt）都不许再猜、再改、再降级。

    Args:
        text: 学生本轮原话。
        history: 会话历史（不含本轮）。
        state: 当前教学阶段，可为空。
        override: 前端显式 mode，可为空。

    Returns:
        ``MODE_VALUES`` 之一。
    """
    return detect(text=text, history=history, state=state, override=override).mode


def mode_label(mode: Optional[str]) -> str:
    """取 mode 的中文展示标签，未知一律回落到「引导中」。"""
    return MODE_LABELS.get(normalize_mode(mode) or DEFAULT_MODE, MODE_LABELS[DEFAULT_MODE])


__all__ = [
    "MODE_GUIDE",
    "MODE_HINT",
    "MODE_DEMO",
    "MODE_JUDGE",
    "MODE_VALUES",
    "DEFAULT_MODE",
    "UNGUARDED_MODES",
    "MODE_LABELS",
    "HISTORY_WINDOW",
    "COOLDOWN_ROUNDS",
    "DetectResult",
    "detect",
    "detect_mode",
    "normalize_mode",
    "mode_label",
]
