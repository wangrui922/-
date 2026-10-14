"""讲解质量评测 harness（#67）。

用途
----
自动给 AI 老师的「讲解 / 引导语」打分，守住"教得好"。评测器把
（题目 + 学生作答/错因 + AI 的讲解文本）交给同一个方舟模型当"裁判"，
按 5 维 rubric 打 1-5 分并给改进建议。

复用关系
--------
裁判调用走 ``app.ai.ark_respond``（与 tutor.next_question 同一客户端、
同一 Key），不新增任何密钥或网络依赖。本模块**纯同步**，与 tutor agent 一致。

质量闸门（已接入生产流）
----------------
``ensure_explanation_quality(...)`` 把"评测 → 不达标则重生成"做成了在线闸门，
已在 ``app.ai.orchestrator.continue_session`` 的 **DEMO 讲解**路径接入：学生点
「不会做，给我讲」时，生成讲解后自动评一次分，低于阈值（默认 3）就重讲一次。
开关见 ``config.explanation_quality_gate_enabled``（默认关闭，确认稳定后再开启）。

典型用法
--------
    from app.services.explanation_quality import evaluate_explanation

    result = evaluate_explanation(
        question="解方程 2x + 3 = 11",
        student_attempt="我直接写了 x=4",
        ai_explanation="我们反过来想：等号两边先同时减 3，会得到什么？",
    )
    print(result.overall, result.grade, result.suggestion)
"""
from __future__ import annotations

import inspect
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.ai import ark_respond, ark_respond_async
from app.ai.ark_client import ArkError

logger = logging.getLogger("app.services.explanation_quality")

#: 5 维 rubric 的权威定义（key 同时是裁判 JSON 的字段名，勿改）
RUBRIC_DIMENSIONS: List[Dict[str, str]] = [
    {
        "key": "guidance",
        "label": "引导性",
        "desc": "用提问/提示引导孩子自己想，而不是直接给答案；一次只讲一件事",
    },
    {
        "key": "correctness",
        "label": "准确性",
        "desc": "数学概念、公式、推理过程正确，没有知识性错误",
    },
    {
        "key": "clarity",
        "label": "清晰度",
        "desc": "表达清楚、循序渐进，孩子能跟上每一步",
    },
    {
        "key": "relevance",
        "label": "针对性",
        "desc": "贴着学生具体的卡点/错因讲，而不是泛泛而谈",
    },
    {
        "key": "warmth",
        "label": "鼓励性",
        "desc": "语气鼓励、耐心，适合该学段孩子的年龄",
    },
]

#: 维度 key 的有序列表（解析与打分都按它）
DIMENSION_KEYS: List[str] = [d["key"] for d in RUBRIC_DIMENSIONS]

#: 裁判（judge）系统提示词：只输出 JSON，不要任何额外文字
_QUALITY_SYSTEM = (
    "你是一位资深数学教育评估专家，负责评估一位 AI 家教给学生的"
    "「讲解 / 引导语」的质量。\n"
    "你会拿到：①题目 ②学生的作答或错误 ③AI 家教的讲解文本"
    "（可能还有 ④诊断出的错因类型）。\n"
    "请只依据【AI 家教的讲解文本】本身打分，不要评价题目或学生。\n\n"
    "按以下 5 个维度各打 1-5 分（5 最好）：\n"
    + "\n".join(f"- {d['key']}（{d['label']}）：{d['desc']}" for d in RUBRIC_DIMENSIONS)
    + "\n\n再给一个 1-5 的综合分 overall，以及一句话改进建议 suggestion"
    "（若已很好可写「保持」）。\n\n"
    "**输出要求**：只输出一个 JSON 对象，不要 markdown 围栏、不要任何解释文字。"
    "字段必须包含："
    + "、".join(DIMENSION_KEYS)
    + "、overall、suggestion。示例：\n"
    '{"guidance":5,"correctness":5,"clarity":4,"relevance":4,"warmth":5,'
    '"overall":5,"suggestion":"可以再缩小提示的颗粒度"}'
)


def _build_user_content(
    *,
    question: str,
    student_attempt: str,
    ai_explanation: str,
    error_type: Optional[str] = None,
) -> str:
    """拼装裁判看到的用户消息。"""
    parts: List[str] = []
    parts.append(f"【题目】\n{question or '（未提供）'}")
    parts.append(f"【学生的作答 / 错误】\n{student_attempt or '（未提供）'}")
    if error_type and error_type.strip():
        parts.append(f"【诊断错因类型】\n{error_type.strip()}")
    parts.append(f"【AI 家教的讲解文本】\n{ai_explanation or '（未提供）'}")
    parts.append("请按系统指示输出评分 JSON。")
    return "\n\n".join(parts)


def parse_quality(raw: str) -> Dict[str, Any]:
    """把裁判返回的文本解析成结构化评分。

    鲁棒策略：
    1. 去掉 ```json ... ``` 围栏；
    2. 截取第一个 ``{`` 到最后一个 ``}`` 之间的内容；
    3. ``json.loads``；
    4. 每个维度缺失/非法时降级为默认值 3；overall 缺失时用维度均值四舍五入；
    5. 任何异常都返回 ``parsed_ok=False`` + 默认分，绝不抛出（上游可据此告警）。

    Returns:
        dict，含 ``dimensions``(dict[str,int])、``overall``(int)、
        ``suggestion``(str)、``parsed_ok``(bool)、``error``(Optional[str])。
    """
    result: Dict[str, Any] = {
        "dimensions": {key: 3 for key in DIMENSION_KEYS},
        "overall": 3,
        "suggestion": "",
        "parsed_ok": False,
        "error": None,
    }
    text = (raw or "").strip()
    if not text:
        result["error"] = "裁判返回为空"
        return result

    # 1) 去围栏
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    # 2) 截取首尾大括号
    start, end = fenced.find("{"), fenced.rfind("}")
    if start == -1 or end == -1 or end <= start:
        result["error"] = "找不到 JSON 对象"
        return result

    try:
        data = json.loads(fenced[start : end + 1])
    except (ValueError, json.JSONDecodeError) as exc:
        result["error"] = f"JSON 解析失败：{exc}"
        return result

    if not isinstance(data, dict):
        result["error"] = "JSON 顶层不是对象"
        return result

    def _coerce(value: Any, default: int = 3) -> int:
        try:
            n = int(round(float(value)))
        except (TypeError, ValueError):
            return default
        return max(1, min(5, n))

    dims = {key: _coerce(data.get(key)) for key in DIMENSION_KEYS}
    result["dimensions"] = dims

    overall_raw = data.get("overall")
    if overall_raw is None:
        result["overall"] = max(1, min(5, round(sum(dims.values()) / len(dims))))
    else:
        result["overall"] = _coerce(overall_raw)

    suggestion = data.get("suggestion")
    result["suggestion"] = suggestion if isinstance(suggestion, str) else ""

    result["parsed_ok"] = True
    result["error"] = None
    return result


@dataclass
class ExplanationQuality:
    """一次讲解质量评测的结果。"""

    dimensions: Dict[str, int] = field(default_factory=lambda: {k: 3 for k in DIMENSION_KEYS})
    overall: int = 3
    suggestion: str = ""
    raw: str = ""
    parsed_ok: bool = False
    error: Optional[str] = None

    @property
    def grade(self) -> str:
        """综合分映射到中文等级。"""
        if self.overall >= 5:
            return "优秀"
        if self.overall >= 4:
            return "良好"
        if self.overall >= 3:
            return "合格"
        return "待改进"

    def passes(self, threshold: int = 4) -> bool:
        """是否达到质量闸门阈值（默认 4 分）。"""
        return self.overall >= max(1, min(5, threshold))

    def dimension_label(self, key: str) -> str:
        for d in RUBRIC_DIMENSIONS:
            if d["key"] == key:
                return d["label"]
        return key

    def report_lines(self) -> List[str]:
        """给人看的一行行报告。"""
        lines = [f"综合：{self.overall}/5（{self.grade}）"]
        if not self.parsed_ok:
            lines.append(f"⚠️ 裁判结果解析异常：{self.error or '未知'}")
        for key in DIMENSION_KEYS:
            score = self.dimensions.get(key, 0)
            lines.append(f"  - {self.dimension_label(key)}：{score}/5")
        if self.suggestion:
            lines.append(f"建议：{self.suggestion}")
        return lines


def evaluate_explanation(
    *,
    question: str,
    student_attempt: str,
    ai_explanation: str,
    error_type: Optional[str] = None,
    timeout: float = 60.0,
) -> ExplanationQuality:
    """评测一段 AI 讲解的质量。

    Args:
        question: 题目文本。
        student_attempt: 学生的作答或错误描述（裁判只看讲解本身，但会给它作上下文）。
        ai_explanation: 要评测的 AI 讲解/引导语文本。
        error_type: 可选的错因类型（诊断结论），作为额外上下文。
        timeout: 裁判调用超时（秒）。

    Returns:
        ExplanationQuality。即使裁判 JSON 解析失败也返回结构化结果（parsed_ok=False）。

    Raises:
        ArkError: 方舟调用本身失败（Key 未配置 / 网络 / 超时 / 上游错误）时原样抛出，
            由调用方决定降级策略。
    """
    raw = ark_respond(
        messages=[
            {
                "role": "user",
                "content": _build_user_content(
                    question=question,
                    student_attempt=student_attempt,
                    ai_explanation=ai_explanation,
                    error_type=error_type,
                ),
            }
        ],
        system=_QUALITY_SYSTEM,
        timeout=timeout,
    )

    return _quality_from_raw(raw)


async def evaluate_explanation_async(
    *,
    question: str,
    student_attempt: str,
    ai_explanation: str,
    error_type: Optional[str] = None,
    timeout: float = 60.0,
) -> ExplanationQuality:
    """``evaluate_explanation`` 的异步版：await ``ark_respond_async``。

    Args:
        与 ``evaluate_explanation`` 完全相同。

    Returns:
        与 ``evaluate_explanation`` 相同。

    Raises:
        ArkError: 方舟调用本身失败时原样抛出，由调用方决定降级策略。
    """
    raw = await ark_respond_async(
        messages=[
            {
                "role": "user",
                "content": _build_user_content(
                    question=question,
                    student_attempt=student_attempt,
                    ai_explanation=ai_explanation,
                    error_type=error_type,
                ),
            }
        ],
        system=_QUALITY_SYSTEM,
        timeout=timeout,
    )

    return _quality_from_raw(raw)


def _quality_from_raw(raw: str) -> ExplanationQuality:
    """从裁判原始回复构造 ``ExplanationQuality``（同步 / 异步共用）。"""
    parsed = parse_quality(raw)
    return ExplanationQuality(
        dimensions=parsed["dimensions"],
        overall=parsed["overall"],
        suggestion=parsed["suggestion"],
        raw=raw,
        parsed_ok=parsed["parsed_ok"],
        error=parsed["error"],
    )


def _empty_quality(error: str = "") -> ExplanationQuality:
    """评测未执行 / 不可判定时的兜底结果（parsed_ok=False）。"""
    return ExplanationQuality(parsed_ok=False, error=error or "评测未执行")


def ensure_explanation_quality(
    *,
    question: str,
    student_attempt: str,
    ai_explanation: str,
    error_type: Optional[str] = None,
    regenerate: Optional[Callable[[], str]] = None,
    threshold: int = 3,
    max_retries: int = 1,
    timeout: float = 60.0,
) -> Tuple[str, ExplanationQuality]:
    """评测讲解质量；低于阈值且提供 ``regenerate`` 时，重生成最多 ``max_retries`` 次。

    #67→生产流接入点（DEMO 讲解闸门）。设计原则：**best-effort，绝不阻塞主流程**——

    * 评测本身抛 ``ArkError``（Key 缺失 / 网络 / 超时 / 上游错误）→ 直接返回原始讲解，不重试；
    * 评测拿到结果但解析异常（``parsed_ok=False``）→ 视为「无法判定」，返回原始讲解；
    * ``regenerate`` 回调抛异常 → 捕获并停止重试，以当前最优讲解返回；
    * 所有路径都返回 ``(最终采用的讲解文本, 最近一次质量结果)``。

    Args:
        question: 题目文本。
        student_attempt: 学生的作答或错误描述（裁判上下文）。
        ai_explanation: 当前要评测的 AI 讲解文本。
        error_type: 可选错因类型。
        regenerate: 可选的「重生成」回调，返回一段新的讲解文本；为 ``None`` 时不做重试。
        threshold: 质量闸门阈值（综合分 1-5），低于该值才重试。
        max_retries: 最大重生成次数（内部夹紧到 1-3）。
        timeout: 评测裁判调用超时（秒）。

    Returns:
        ``(text, quality)``：``text`` 是最终采用的讲解；``quality`` 是最后一次评测结果
        （即使未执行也返回结构化兜底，``parsed_ok=False``）。

    Note:
        本函数**纯同步**，与 ``tutor.next_question`` 一致；调用方应在 ``run_in_threadpool`` 内包裹。
    """
    threshold = max(1, min(5, int(threshold)))
    current_text = ai_explanation
    quality: ExplanationQuality = _empty_quality()

    try:
        quality = evaluate_explanation(
            question=question,
            student_attempt=student_attempt,
            ai_explanation=current_text,
            error_type=error_type,
            timeout=timeout,
        )
    except ArkError as exc:
        logger.warning("讲解质量评测调用失败，跳过闸门：%s", exc)
        return current_text, _empty_quality(error=str(exc))

    # 已达标（或解析失败无法判定）→ 直接采用
    if quality.parsed_ok and quality.passes(threshold):
        return current_text, quality

    # 低于阈值 → 尝试重生成（最多 max_retries 次，每次重新评测）
    for _ in range(max(1, min(3, max_retries))):
        if regenerate is None:
            break
        try:
            candidate = regenerate()
        except Exception as exc:  # 重生成失败不应拖垮主流程
            logger.warning("讲解重生成回调异常，停止重试：%s", exc)
            break
        if not candidate or not candidate.strip():
            break
        current_text = candidate
        try:
            quality = evaluate_explanation(
                question=question,
                student_attempt=student_attempt,
                ai_explanation=current_text,
                error_type=error_type,
                timeout=timeout,
            )
        except ArkError as exc:
            logger.warning("重生成后评测失败，沿用当前讲解：%s", exc)
            return current_text, _empty_quality(error=str(exc))
        if quality.parsed_ok and quality.passes(threshold):
            return current_text, quality

    return current_text, quality


async def ensure_explanation_quality_async(
    *,
    question: str,
    student_attempt: str,
    ai_explanation: str,
    error_type: Optional[str] = None,
    regenerate: Optional[Callable[[], Any]] = None,
    threshold: int = 3,
    max_retries: int = 1,
    timeout: float = 60.0,
) -> Tuple[str, ExplanationQuality]:
    """``ensure_explanation_quality`` 的异步版（await async 裁判）。

    ``regenerate`` 回调**同步 / 异步皆可**：返回 ``Awaitable`` 时自动 ``await``；
    其余语义（best-effort、绝不阻塞主流程、``(text, quality)`` 返回）与同步版一致。

    Args:
        与 ``ensure_explanation_quality`` 完全相同（``regenerate`` 可为 async 函数）。

    Returns:
        ``(text, quality)``：最终采用的讲解与最后一次评测结果。
    """
    threshold = max(1, min(5, int(threshold)))
    current_text = ai_explanation
    quality: ExplanationQuality = _empty_quality()

    async def _evaluate() -> ExplanationQuality:
        return await evaluate_explanation_async(
            question=question,
            student_attempt=student_attempt,
            ai_explanation=current_text,
            error_type=error_type,
            timeout=timeout,
        )

    try:
        quality = await _evaluate()
    except ArkError as exc:
        logger.warning("讲解质量评测调用失败，跳过闸门：%s", exc)
        return current_text, _empty_quality(error=str(exc))

    if quality.parsed_ok and quality.passes(threshold):
        return current_text, quality

    for _ in range(max(1, min(3, max_retries))):
        if regenerate is None:
            break
        try:
            candidate = regenerate()
            if inspect.isawaitable(candidate):
                candidate = await candidate
        except Exception as exc:  # 重生成失败不应拖垮主流程
            logger.warning("讲解重生成回调异常，停止重试：%s", exc)
            break
        if not candidate or not candidate.strip():
            break
        current_text = candidate
        try:
            quality = await _evaluate()
        except ArkError as exc:
            logger.warning("重生成后评测失败，沿用当前讲解：%s", exc)
            return current_text, _empty_quality(error=str(exc))
        if quality.parsed_ok and quality.passes(threshold):
            return current_text, quality

    return current_text, quality


__all__ = [
    "RUBRIC_DIMENSIONS",
    "DIMENSION_KEYS",
    "parse_quality",
    "evaluate_explanation",
    "evaluate_explanation_async",
    "ensure_explanation_quality",
    "ensure_explanation_quality_async",
    "ExplanationQuality",
]
