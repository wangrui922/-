# -*- coding: utf-8 -*-
"""练习引导 · 对话状态机（四层策略自动切换）。

用户诉求
--------
引导对话曾经"连续三轮重复同一引导问题、无视学生答案、孩子没耐心仍自顾自提问"。
本模块把单层苏格拉底式提示词升级为**四层策略状态机**，由 Python 规则引擎
（非大模型自决）在每次调用前判定当前该用哪一层、注入哪份 system prompt：

    L1 GUIDE        引导探索   —— 现状教学法：苏格拉底式提问，一次一问
    L2 HALF_GUIDE   半引导     —— 给方向性小线索 + 一个很小的确认问题
    L3 TEACH_ASK    边讲边问   —— 讲当前这一步思路 + 问一句"对吗 / 下一步呢"
    L4 DIRECT_TELL  直接讲解   —— 完整解题过程 + 知识点 + 易错点 + 最终答案

设计要点
--------
1. **无状态推导**：策略层不写库、不占 Redis。每次调用从
   ``guide_history``（已持久化在 ``LearningTask.items[].guide_history`` 快照，
   见 :func:`practice_service._recent_guide_history`）推导——历史即状态，
   天然支持多实例与重启，前后端协议零改动。
2. **只升缓降**：升级条件满足立即升；降级要求"连续 2 轮有实质进展"才回退，
   防止在两层之间反复横跳。
3. **L4 放行 no-leak 闸门**：L1-L3 仍过 ``sanitize_guide_reply`` 的答案泄漏
   检查；L4 本来就是完整讲解，调用方应传 ``allow_answer=True`` 跳过拦截。
4. **防重复**：L2-L4 的 prompt 都强制要求"不得重复之前任何一轮的问题/话术"，
   且 L4 之后的轮次若孩子仍卡壳，直接延续直讲模式并换讲法。

触发信号（全部来自 Python 规则）：
- ``impatient``：孩子明确要答案/不耐烦（"直接告诉我答案"“别问了”“烦”…）→ 直上 L4。
- ``stuck``：沿用 ``_STUCK_TEXT_RE``（“不会/不知道/想不出来/看不懂”…）。
- ``repeat``：本轮回答与最近轮次高度相似（字符重叠率 ≥ 0.7 或完全一致）。
- ``no_progress``：回答为空/过短/纯语气词（“嗯”“哦”“…”）。
- 升级轨：stuck≥1→L2；stuck≥2 或 repeat≥2 或 no_progress≥2→L3；
  stuck≥3 / no_progress≥3 / 轮次≥5 / impatient→L4。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.ai.prompts import practice_guide
from app.core.system_config import get_config

#: 策略层级（对外枚举值，小写字符串，直接进 prompt 与响应字段）
L1_GUIDE = "guide"            # 引导探索
L2_HALF_GUIDE = "half_guide"  # 半引导
L3_TEACH_ASK = "teach_ask"    # 边讲边问
L4_DIRECT = "direct"          # 直接讲解

LEVEL_ORDER: List[str] = [L1_GUIDE, L2_HALF_GUIDE, L3_TEACH_ASK, L4_DIRECT]

LEVEL_LABEL: Dict[str, str] = {
    L1_GUIDE: "引导探索",
    L2_HALF_GUIDE: "半引导",
    L3_TEACH_ASK: "边讲边问",
    L4_DIRECT: "直接讲解",
}

#: 学生明确要答案 / 不耐烦关键词（命中直上 L4）
_IMPATIENT_RE = re.compile(
    r"(?:直接告诉我|告诉我答案|给我答案|直接给答案|直接讲|讲给我听|"
    r"别问了|别再问了|别绕弯子|烦死了|好烦|不想了|不想想|快点|赶紧|"
    r"急死|受不了|听不懂你在说什么|你直接|快说答案|答案是什么)"
)

#: 卡壳关键词
_STUCK_RE = re.compile(
    r"(?:不知道|不记得|想不出来|不会|没思路|没头绪|看不懂|卡住了|"
    r"不会做|不会算|不会解)"
)

#: 纯语气词 / 无进展短答
_NO_PROGRESS_RE = re.compile(
    r"^(?:嗯|哦|啊|唉|哈|好|行|对|是|不|没|…|\.\.|\.|？|\?|！|!|\s*)+$"
)

#: 字符重叠率阈值：≥ 此值视为"重复回答"
REPEAT_SIMILARITY: float = get_config("REPEAT_SIMILARITY")

#: 升级边界
STUCK_TO_L2: int = get_config("STUCK_TO_L2")
STUCK_TO_L3: int = get_config("STUCK_TO_L3")
STUCK_TO_L4: int = get_config("STUCK_TO_L4")
REPEAT_TO_L2: int = get_config("REPEAT_TO_L2")
REPEAT_TO_L3: int = get_config("REPEAT_TO_L3")
NOPROGRESS_TO_L3: int = get_config("NOPROGRESS_TO_L3")
NOPROGRESS_TO_L4: int = get_config("NOPROGRESS_TO_L4")
TURNS_TO_L4: int = get_config("TURNS_TO_L4")


# ==================================================================== 信号分析

def _clean_text(value: Optional[str]) -> str:
    return (value or "").strip()


def _is_impatient(text: str) -> bool:
    return bool(_IMPATIENT_RE.search(_clean_text(text)))


def _is_stuck(text: str) -> bool:
    return bool(_STUCK_RE.search(_clean_text(text)))


def _has_substance(text: str) -> bool:
    """回答是否有实质内容：数字 / 算式 / 逻辑词 / 较长描述。"""
    t = _clean_text(text)
    if not t:
        return False
    min_len: int = get_config("HAS_SUBSTANCE_MIN_LEN")
    if len(t) >= min_len:
        return True
    if re.search(r"\d|[+\-*/=①②③④⑤⑥⑦⑧⑨⑩]", t):
        return True
    if re.search(r"(因为|所以|先|然后|接着|等于|算出|得到|加起来|减去|乘|除)", t):
        return True
    return False


def _similarity(a: str, b: str) -> float:
    """字符重叠率（低配 Jaccard），用于重复回答判定。

    先剥离空白，避免"答案是 15"与"答案是15"因空格稀释重叠率。
    """
    a, b = a.replace(" ", "").replace("\u3000", ""), b.replace(" ", "").replace("\u3000", "")
    if not a or not b:
        return 0.0
    set_a, set_b = set(a), set(b)
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union else 0.0


def _count_repeats(recent_works: List[str]) -> int:
    """连续重复回答的次数：与最近工作文本做重叠率比较。"""
    if len(recent_works) < 2:
        return 0
    reps = 0
    for i in range(1, len(recent_works)):
        prev, cur = recent_works[i - 1], recent_works[i]
        if prev and _similarity(prev, cur) >= REPEAT_SIMILARITY:
            reps += 1
        elif _clean_text(cur).lower() in ("不知道", "不会", "不懂", "不会做", "不会算"):
            reps += 1
        else:
            reps = 0
    return reps


# ==================================================================== 决策

def decide_level(
    guide_history: List[Dict[str, Any]],
    student_input: Optional[str],
    *,
    current_level: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """根据引导历史 + 本轮学生输入，判定四层策略。

    Args:
        guide_history: 最近引导轮次（元素含 ``work``=学生作答，``reply``=老师回复），
            来自 ``practice_service._recent_guide_history``。
        student_input: 本轮学生输入文本（可为空串/None）。
        current_level: 上一轮策略（可选；用于命中 recent 时二次确认，防止来回跳）。

    Returns:
        ``(level, signals)``：
            level —— 应采用的策略层级（``L1_GUIDE`` 之一）；
            signals —— 供调试/日志用的信号字典。
    """
    works = [_clean_text(t.get("work")) for t in guide_history if isinstance(t, dict)]
    replies = [_clean_text(t.get("reply")) for t in guide_history if isinstance(t, dict)]
    cur = _clean_text(student_input)

    impatient = _is_impatient(cur)
    stuck_now = _is_stuck(cur) or (not cur and len(guide_history) > 0 and works and not works[-1])

    stuck_streak = 0
    for w in reversed(works):
        if _is_stuck(w):
            stuck_streak += 1
        else:
            break
    current_stuck_streak = stuck_streak + (1 if stuck_now else 0)

    cur_no_progress = (not cur) or bool(_NO_PROGRESS_RE.match(cur)) or _is_stuck(cur)
    no_progress_streak = 0
    for w in reversed(works):
        if not _has_substance(w):
            no_progress_streak += 1
        else:
            break
    current_no_progress_streak = no_progress_streak + (1 if cur_no_progress else 0)

    repeat_count = _count_repeats(works + ([cur] if cur else []))
    total_turns = len(guide_history) + 1

    signals: Dict[str, Any] = {
        "impatient": impatient,
        "stuck_streak": current_stuck_streak,
        "no_progress_streak": current_no_progress_streak,
        "repeated": repeat_count,
        "total_turns": total_turns,
        "recent_works": works[-3:],
    }

    # ---- 升级判定（优先级从高到低）----
    if impatient:
        return L4_DIRECT, signals
    if (
        current_stuck_streak >= STUCK_TO_L4
        or current_no_progress_streak >= NOPROGRESS_TO_L4
        or total_turns >= TURNS_TO_L4
    ):
        return L4_DIRECT, signals
    if (
        current_stuck_streak >= STUCK_TO_L3
        or repeat_count >= REPEAT_TO_L3
        or current_no_progress_streak >= NOPROGRESS_TO_L3
    ):
        return L3_TEACH_ASK, signals
    if current_stuck_streak >= STUCK_TO_L2 or repeat_count >= REPEAT_TO_L2:
        return L2_HALF_GUIDE, signals

    # ---- 防抖降级 ----
    if current_level in (L3_TEACH_ASK, L4_DIRECT):
        recent_relevant = (works + ([cur] if cur else []))[-2:]
        recent_substance = sum(1 for w in recent_relevant if _has_substance(w))
        if len(recent_relevant) >= 2 and recent_substance >= 2:
            idx = LEVEL_ORDER.index(current_level)
            return LEVEL_ORDER[max(idx - 1, 0)], signals

    return L1_GUIDE, signals


# ==================================================================== Prompt 模板

_BASE_TEMPLATE: str = practice_guide.PROMPT


#: 各层级追加的策略指令段
_LEVEL_SUFFIX: Dict[str, str] = {
    L1_GUIDE: "",

    L2_HALF_GUIDE: """

【当前策略：半引导 HALF_GUIDE】
孩子已经有点卡壳了（说过"不会/不知道"或没给出实质过程）。这一轮你要：
1. 先**肯定**孩子已经做到的部分（哪怕只是读对了题），用一句接住他。
2. 给**一个方向性小线索**：直接用题目里的数字或情境点一下思路（例如"先看看"剩下的"和"原来的"之间是什么关系？"）。
3. 最后只问一个**很小的问题**：要求孩子只做一步判断、报一个数或选一个答案，他一步就能答上来。
4. **严禁**重复之前任何一轮问过的问题；也**不要**把整道题的过程讲出来。
5. 线索要贴题，不要空谈"你要多想想"。""",

    L3_TEACH_ASK: """

【当前策略：边讲边问 TEACH_ASK】
孩子已经连续几次卡住了，单靠提问推不动。这一轮请你：
1. 用 1-2 句话把**当前这一步**的解题思路讲清楚（例如"余下的部分 = 原来的总长 − 剪去的长度，所以要先算剪去了多少"）。
2. 只讲这一步，**不要**一口气讲完整道题。
3. 讲完马上问一个**确认性小问题**，让孩子跟上："你觉得下一步要先算什么？""这一步你同意吗？""那剪去的长度怎么求？"
4. **严禁**重复之前任何一轮问过的问题或话术。
5. 语气温和，像老师俯身在桌边陪他一步步走。""",

    L4_DIRECT: """

【当前策略：直接讲解 DIRECT · 触发条件已满足】
孩子已经明确表示听不懂、没耐心，或多次求助未果。此刻你就是讲题的小老师，**请完整、清楚地讲解这道题**：
1. 把**完整解题过程**从头一步一步写清楚：先求什么、再求什么、每一步算式和单位都写出来。
2. 明确点出**这道题考核的知识点**是什么（比如"这是'求剩余量'的应用题，核心是：剩余量 = 总量 − 用去量"）。
3. 点出**易错点**（如"这里容易把'剩下的'和'剪去的'搞反""别忘记写单位"）。
4. **给出最终答案**，让孩子知道正确答案是多少。
5. 讲完后用一句温和的话把孩子拉回思考（如"这个思路你想想看，哪里没跟上？我们再走一遍"）。
6. **不要**再提问要求孩子自行推导；但可以在结尾问一句"明白了吗"保持互动。
7. 如果这是第二次及以后进入讲解模式，请换一种讲法、换一个角度，不要复读上一轮的内容。""",
}


def build_prompt(
    level: str,
    question_stem: str,
    work_text: str,
    guide_history: List[Dict[str, Any]],
) -> str:
    """按策略层级组装完整 system prompt（含历史轮次，注入防重复约束）。

    Args:
        level: 策略层级（``decide_level`` 的返回值）。
        question_stem: 题目 + 选项拼好的题干。
        work_text: 孩子当前作答（或占位说明）。
        guide_history: 最近引导轮次（元素含 ``work``/``reply``）。

    Returns:
        最终传给模型的可执行 prompt 文本。
    """
    base = _BASE_TEMPLATE.replace("{{QUESTION}}", question_stem).replace(
        "{{WORK}}", work_text or "（孩子还没写下过程，只是点开了求助）"
    )
    suffix = _LEVEL_SUFFIX.get(level, "")

    history_lines = [
        f"第 {idx} 轮：学生：{t.get('work') or '（未写过程）'}；老师：{t.get('reply')}"
        for idx, t in enumerate(guide_history, start=1)
        if isinstance(t, dict) and (t.get("work") or t.get("reply"))
    ]
    history_text = "\n".join(history_lines) if history_lines else "（这是第一轮，还没有之前的问答）"

    return (
        base
        + "\n\n【本题之前的引导记录】\n"
        + history_text
        + "\n请先看清之前老师已经问过什么。**绝对不要原样重复之前任何一轮的问题或话术**。"
        + suffix
    )


__all__ = [
    "L1_GUIDE",
    "L2_HALF_GUIDE",
    "L3_TEACH_ASK",
    "L4_DIRECT",
    "LEVEL_ORDER",
    "LEVEL_LABEL",
    "decide_level",
    "build_prompt",
]
