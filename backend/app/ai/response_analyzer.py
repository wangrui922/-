"""学生回答结构化 Analyzer（TASK-002 · V1 规则版）。

把「孩子这一轮说的话」结构化成 response object（契约见
docs/task-002-response-object-design.md §3.3），落在 ``ai_messages.response_object``
列，供 003 决策引擎 / 006 六维掌握度消费。

与 ``app/ai/intent.py`` 的关系（并存铁律，勿破）：
    * intent.mode 回答「老师**下一轮怎么教**」（教学策略，``detect_mode`` 唯一定稿）；
    * 本模块的 response_type 回答「学生**这一轮说了什么**」（内容分类）；
    * **response_type 不得回写 mode，mode 不得推导 response_type**；软映射表
      （哪些 type 常伴生哪些 mode）只用于观测/埋点，不进任何分支判断。

硬约束（照抄 intent.py 的模块纪律）：
    * **零 DB、零网络、零新依赖**：只用标准库 ``re`` / ``logging``，可离线单测；
    * **不额外调 LLM**：V1 规则版，多一次方舟往返等于把用户体感延迟翻倍；
    * **永不抛异常**：任何脏输入都收敛成合法契约对象（最差也是 UNCLEAR）——
      结构化是辅导主流程的旁路，绝不能拖垮聊天；
    * **判题边界**：本模块只做抽取与分类，**不判对错**。对错权威在
      ``app/services/sympy_judge.py`` / ``app/ai/answer_guard.py``；``error``
      字段只承载学生**明示自报**的错因，不猜。

演进位（规划 §40 Response Analyzer 与 Tutor 分离的完整版）：
    ``analyze_response`` 签名即契约。模型升级后可凭 ``ai_messages.content``
    （raw_response，永远保留）重跑本接口、或换 LLM 实现覆盖旧数据——接口不变，
    消费方（#33）零感知。LLM 版通过 ``system_config`` 灰度开关分流（默认关）。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Sequence, Tuple

from app.ai import _num_utils

logger = logging.getLogger("app.ai.response_analyzer")

# ==================================================================== 八枚举
#: 给出（自认为）最终的答案/结论
RESPONSE_TYPE_FINAL_ANSWER: str = "FINAL_ANSWER"
#: 讲思路/过程，未给最终答案
RESPONSE_TYPE_REASONING: str = "REASONING"
#: 学生反问题目本身 / 向老师要答案
RESPONSE_TYPE_QUESTION: str = "QUESTION"
#: 主动要提示/线索
RESPONSE_TYPE_HINT_REQUEST: str = "HINT_REQUEST"
#: 明确不会/不懂
RESPONSE_TYPE_I_DONT_KNOW: str = "I_DONT_KNOW"
#: 做了一部分，明示未完成
RESPONSE_TYPE_PARTIAL_ANSWER: str = "PARTIAL_ANSWER"
#: 修正自己或他人的说法
RESPONSE_TYPE_CORRECTION: str = "CORRECTION"
#: 兜底：无法归入以上任何一类
RESPONSE_TYPE_UNCLEAR: str = "UNCLEAR"

#: 全部合法取值（**只增不改**：新增枚举 appended 在 UNCLEAR 之前，
#: 并同步设计文档 §3.2 表与 ``RESPONSE_TYPE_VALUES``，消费方用 in 集合匹配）
RESPONSE_TYPE_VALUES: Tuple[str, ...] = (
    RESPONSE_TYPE_FINAL_ANSWER,
    RESPONSE_TYPE_REASONING,
    RESPONSE_TYPE_QUESTION,
    RESPONSE_TYPE_HINT_REQUEST,
    RESPONSE_TYPE_I_DONT_KNOW,
    RESPONSE_TYPE_PARTIAL_ANSWER,
    RESPONSE_TYPE_CORRECTION,
    RESPONSE_TYPE_UNCLEAR,
)

#: 兜底时的分类置信度（语义 = 分类置信度，**不是**答案正确概率）
_UNCLEAR_CONFIDENCE: float = 0.3
#: 命中规则数的置信度映射
_CONFIDENCE_SINGLE_HIT: float = 0.6
_CONFIDENCE_MULTI_HIT: float = 0.8

# ================================================================ 规则表
# 组织方式参照 intent.py：模块级预编译，避免每轮请求重复编译。
# 判级优先级（先命中先返回，设计文档 §3.2）：
#   I_DONT_KNOW > HINT_REQUEST > QUESTION > CORRECTION
#     > FINAL_ANSWER > PARTIAL_ANSWER > REASONING > UNCLEAR
#
# 注意：本表**不 import intent 的私有正则**——两个模块各自独立演进，
# 防止「改 mode 规则顺手打破回答分类」。

_I_DONT_KNOW_PATTERNS: Tuple[str, ...] = (
    r"我(真的|实在|就是|完全|一点儿?也|一点儿?都)?不会(?![吧呗嘛])",
    r"不会做|不会算|不会解|不会写|不会做这题",
    r"不知道(怎么|咋|如何)(做|算|解|下手|开始|入手)",
    r"(完全|根本)(不懂|没懂|看不懂)",
    r"没(思路|头绪|方向)",
    r"一点?也不会|太难了",
)

_HINT_REQUEST_PATTERNS: Tuple[str, ...] = (
    r"给\s*(点|个|些|一点)?\s*提示",
    r"提示(一)?下|提醒(我)?(一)?下|点(拨|拨一)下",
    r"给\s*(个|点|条)?\s*思路",
    r"从哪(儿|里)?\s*(开始|下手|入手|想)",
    r"第一步(是什么|是啥|怎么|该|要|干什么)",
    r"有(没有|什么)\s*(提示|线索|思路|窍门)",
    r"能不能\s*(提示|点)",
    r"卡住了|卡在|卡死",
)

_QUESTION_PATTERNS: Tuple[str, ...] = (
    r"求什么|求的是|求啥",
    r"什么叫|什么是|什么意思|啥意思|怎么理解",
    r"(这|那)?(道|个)?题.{0,10}(求|问|说|是)什么",
    r"答案是(多少|什么|啥|几)",
    r"结果是(多少|什么|啥|几)",
    r"(告诉|给|公布|说出|讲|写出)\s*(我)?\s*(正确)?答案",
    r"怎么(读|看这道|理解这道)",
)

_CORRECTION_PATTERNS: Tuple[str, ...] = (
    r"(?<!对)不对(?!意思)",  # 「对不对」是求证语气不是修正，用环视排除
    r"不是这样|不是这么|说反了",
    r"搞错|说错|理解错|写错|看错|算错|移错",
    r"我改|改一下|修改一下|重新(算|做|写|来|想)",
    r"更正|纠正",
)

_FINAL_ANSWER_PATTERNS: Tuple[str, ...] = (
    r"(答案|结果|我算出来|我算的|我得出|我得到|我觉得是|应该是)\s*[是为:：=＝]?\s*\S+",
    r"我(做|算|写|填)完[了成]",
    r"对不对|对吗|对么|对不",
    r"是不是这样|这样对|这么做对|做得对",
    r"帮我(看看|检查|批改)",
    r"检查一下",
    r"我这样(做|算|想)",
)

_PARTIAL_ANSWER_PATTERNS: Tuple[str, ...] = (
    r"我(算|做|写|推|化)到",
    r"还差(一点|一步|最后|一点就)",
    r"接下来(怎么|不会|不知|卡)",
    r"然后(就|我)?(不会|不懂|卡)",
    r"后面(的)?不会",
    r"只(剩|差)",
    r"做到这",
)

_REASONING_PATTERNS: Tuple[str, ...] = (
    r"因为|所以|由于|也就是说|相当于|等价于",
    r"先(把|将|求|算|找|设)",
    r"首先|然后|接着|下一步",
    r"我的思路|我是这样想|我想的是|我是想|我想的思路",
    r"我觉得(?!是)",
    r"应该是这样",
)

#: 规则表：``(response_type, 编译后的正则元组)``，顺序即判级优先级
_RULES: Tuple[Tuple[str, Tuple["re.Pattern[str]", ...]], ...] = (
    (RESPONSE_TYPE_I_DONT_KNOW, tuple(re.compile(p) for p in _I_DONT_KNOW_PATTERNS)),
    (RESPONSE_TYPE_HINT_REQUEST, tuple(re.compile(p) for p in _HINT_REQUEST_PATTERNS)),
    (RESPONSE_TYPE_QUESTION, tuple(re.compile(p) for p in _QUESTION_PATTERNS)),
    (RESPONSE_TYPE_CORRECTION, tuple(re.compile(p) for p in _CORRECTION_PATTERNS)),
    (RESPONSE_TYPE_FINAL_ANSWER, tuple(re.compile(p) for p in _FINAL_ANSWER_PATTERNS)),
    (RESPONSE_TYPE_PARTIAL_ANSWER, tuple(re.compile(p) for p in _PARTIAL_ANSWER_PATTERNS)),
    (RESPONSE_TYPE_REASONING, tuple(re.compile(p) for p in _REASONING_PATTERNS)),
)

# ================================================================ 错因自报
#: 学生**明示自报**错因 → 五类机器码（与 error_categories.normalize_error 对齐）。
#: 只认明示，不猜——权威判题在 sympy_judge / answer_guard（模块纪律）。
_ERROR_SELF_REPORT: Tuple[Tuple["re.Pattern[str]", str], ...] = (
    (re.compile(r"(忘[了记]?|没[有]?)\s*变号"), "METHOD"),
    (re.compile(r"移项.{0,6}(错|忘|没|漏)"), "METHOD"),
    (re.compile(r"(算|计算)错[了误]?"), "CALC"),
    (re.compile(r"(看|读|理解?)错[了误]?"), "READ"),
    (re.compile(r"忘[了记]?公式|不记得公式|不会[动用]公式"), "KNOWLEDGE"),
    (re.compile(r"漏[了写]?|少[了写]?(一)?步|忘记[写检]查|没[有]?检查"), "CHECK"),
)

# ================================================================ 步骤自报
_CN_NUM: Dict[str, int] = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
_STEP_DONE_RE = re.compile(r"我?(算|做|写|填|检查)完[了成]|做完[了成]|写完[了成]|算完[了成]")
_STEP_STUCK_RE = re.compile(r"卡[住在死]")
_STEP_INDEX_RE = re.compile(r"第\s*([一二三12345])\s*步")


def _classify(text: str) -> Tuple[str, int]:
    """按判级优先级分类，返回 ``(response_type, 命中规则总数)``。

    命中规则总数跨全部类别计数（不限胜出类别），用于 confidence 映射。
    """
    hit_count = 0
    winner = RESPONSE_TYPE_UNCLEAR
    for response_type, regexes in _RULES:
        for regex in regexes:
            if regex.search(text):
                hit_count += 1
                if winner == RESPONSE_TYPE_UNCLEAR:
                    winner = response_type
    return winner, hit_count


def _self_reported_error(text: str) -> Optional[Dict[str, str]]:
    """学生明示自报的错因；没有则 ``None``。只认明示，不猜。"""
    for regex, error_type in _ERROR_SELF_REPORT:
        match = regex.search(text)
        if match:
            return {"type": error_type, "detail": match.group(0)[:50]}
    return None


def _step_state(text: str) -> Optional[Dict[str, Any]]:
    """学生自报的步骤进度；没有强信号则 ``None``。

    判定顺序：完成 > 卡住 > 步骤序号。``total_steps`` V1 恒 ``None``
    （学生极少自报总步数，留空比猜好）；与 ``ai_sessions.session_state``
    （系统阶段）是两回事，消费方不得互相推导。
    """
    if _STEP_DONE_RE.search(text):
        return {"current_step": None, "total_steps": None, "status": "done"}
    if _STEP_STUCK_RE.search(text):
        return {"current_step": None, "total_steps": None, "status": "stuck"}
    step_match = _STEP_INDEX_RE.search(text)
    if step_match:
        token = step_match.group(1)
        current = _CN_NUM.get(token)
        if current is None:
            try:
                current = int(token)
            except (TypeError, ValueError):
                current = None
        if current is not None:
            return {"current_step": current, "total_steps": None, "status": "in_progress"}
    return None


def _build_contract(
    *,
    response_type: str,
    answer: Optional[str],
    evidence: Optional[list],
    error: Optional[Dict[str, str]],
    confidence: float,
    step_state: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """组装契约对象：7 个键**恒存在**，值可 None（消费方缺键按 None 处理）。"""
    return {
        "response_type": response_type,
        "answer": answer,
        "reasoning": None,  # V1 不抽（LLM analyzer 演进位填充）
        "evidence": evidence,
        "error": error,
        "confidence": confidence,
        "step_state": step_state,
    }


def _unclear_contract() -> Dict[str, Any]:
    """兜底契约：全字段最保守值。"""
    return _build_contract(
        response_type=RESPONSE_TYPE_UNCLEAR,
        answer=None,
        evidence=None,
        error=None,
        confidence=_UNCLEAR_CONFIDENCE,
        step_state=None,
    )


def analyze_response(
    *, text: str, history: Optional[Sequence[Any]] = None
) -> Dict[str, Any]:
    """把学生这一轮原话结构化成 response object（设计文档 §3.3 契约）。

    Args:
        text: 学生本轮原话（必填，空串按 UNCLEAR 兜底）。
        history: 会话历史 ``[{"role","content"}]``，**不含**本轮。V1 仅预留
            入参（CORRECTION 的「修正上一轮」判定可能用到），当前不参与判断，
            保留签名以便演进时不改调用方。

    Returns:
        7 键契约 dict：``response_type`` 恒为 ``RESPONSE_TYPE_VALUES`` 之一；
        其余字段可 ``None``。``confidence`` 语义是**分类置信度**
        （多规则命中 0.8 / 单规则 0.6 / 兜底 0.3），不是答案正确概率。

    Raises:
        永不抛异常——内部任何失败都收敛为 UNCLEAR 契约（旁路容错纪律）。

    Examples:
        >>> analyze_response(text="我算出来是 x = 2，对不对")["response_type"]
        'FINAL_ANSWER'
        >>> analyze_response(text="给点提示吧")["response_type"]
        'HINT_REQUEST'
        >>> analyze_response(text="……")["response_type"]
        'UNCLEAR'
    """
    content = str(text or "").strip()
    if not content:
        return _unclear_contract()

    try:
        response_type, hit_count = _classify(content)
        confidence = (
            _CONFIDENCE_MULTI_HIT if hit_count >= 2 else _CONFIDENCE_SINGLE_HIT
        )
        # evidence / error / step_state 都是**显式正则信号**（等式片段、明示
        # 错因、明示步骤），误报成本低——UNCLEAR 也照抽；answer 保守不抽
        # （没分类就不猜答案，防「？？？12」被当成给答案）。
        evidence = _num_utils.extract_equalities(content) or None
        error = _self_reported_error(content)
        step_state = _step_state(content)
        if response_type == RESPONSE_TYPE_UNCLEAR:
            return _build_contract(
                response_type=RESPONSE_TYPE_UNCLEAR,
                answer=None,
                evidence=evidence,
                error=error,
                confidence=_UNCLEAR_CONFIDENCE,
                step_state=step_state,
            )
        return _build_contract(
            response_type=response_type,
            answer=_num_utils.extract_answer(content),
            evidence=evidence,
            error=error,
            confidence=confidence,
            step_state=step_state,
        )
    except Exception:  # noqa: BLE001 - 旁路容错：结构化失败绝不拖垮辅导主流程
        logger.warning("response analyze 失败，按 UNCLEAR 兜底", exc_info=True)
        return _unclear_contract()


__all__ = [
    "analyze_response",
    "RESPONSE_TYPE_VALUES",
    "RESPONSE_TYPE_FINAL_ANSWER",
    "RESPONSE_TYPE_REASONING",
    "RESPONSE_TYPE_QUESTION",
    "RESPONSE_TYPE_HINT_REQUEST",
    "RESPONSE_TYPE_I_DONT_KNOW",
    "RESPONSE_TYPE_PARTIAL_ANSWER",
    "RESPONSE_TYPE_CORRECTION",
    "RESPONSE_TYPE_UNCLEAR",
]
