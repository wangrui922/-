"""Question Agent：题目识别与知识点/难度/题型分析（docs §C.1 第 1 个 Agent）。

链路：``图片 → OCR(ocr.PROMPT) → 题目文字`` → ``分析(tutor.PROMPT) → JSON``。
本模块只做"读题+定性"，不做诊断、不做引导。

分析 JSON 除知识点/难度/题型/答案/解答外，还要求模型输出 ``confidence``
（0~1 自评置信度：识别是否正确、分析是否可靠）——供编排层低置信度重拍闸门
使用，没把握时不硬答（灰度开关见 ``system_config.CONFIDENCE_RETHRESHOLD``）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.ai.agents._json_utils import extract_json as _extract_json
from app.ai.ark_client import ark_respond, ark_respond_async
from app.ai.prompts import ocr as ocr_prompt
from app.ai.prompts import tutor as tutor_prompt
from app.core.response import ApiError, CODE_BAD_REQUEST

#: 难度档位（与 questions.difficulty 的 CHECK 约束一致）
DIFFICULTY_LEVELS = (1, 3, 5)

#: 允许的题型（覆盖小学、初中、高中的常见题型）
QUESTION_TYPES = ("计算", "应用", "几何", "函数", "证明")

#: 分析失败时的兜底值
DEFAULT_KNOWLEDGE: str = "综合"
DEFAULT_DIFFICULTY: int = 3
DEFAULT_TYPE: str = "计算"

_ANALYZE_INSTRUCTION: str = (
    "请分析并完整解答以下题目，只输出JSON，不要任何解释、前后缀或代码块标记。\n"
    '格式严格为：{"knowledge":"知识点名称","difficulty":3,"type":"计算",'
    '"correct_answer":"标准答案","solution":"完整但简洁的解题过程",'
    '"confidence":0.95}\n'
    "其中 difficulty 只能取 1(易)/3(中)/5(难) 三个整数之一；"
    "type 只能取 计算/应用/几何/函数/证明 之一；knowledge 用不超过 12 个字的知识点名；"
    "correct_answer 必须包含最终答案和必要单位；solution 要覆盖关键推理步骤；"
    "confidence 是 0~1 之间的小数，表示你对「题目识别是否正确、分析是否可靠」的自我评估——"
    "图像模糊、字迹不清、条件有歧义或无法确认某个数字/符号时，必须给出低置信度"
    "（如 0.3 甚至更低）；识别与分析都很有把握时，才可以给 0.9 以上。宁可低报也不要高估。\n\n"
    "题目：\n"
)


def _normalize_difficulty(raw: Any) -> int:
    """把模型给的任意难度值收敛到 1/3/5。"""
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return DEFAULT_DIFFICULTY
    if value in DIFFICULTY_LEVELS:
        return value
    # 取最接近的档位（如 2 → 1、4 → 3、9 → 5）
    return min(DIFFICULTY_LEVELS, key=lambda level: (abs(level - value), level))


def _normalize_type(raw: Any) -> str:
    """把模型给的题型收敛到既定枚举，不匹配则回落到默认题型。"""
    value = str(raw or "").strip()
    if value in QUESTION_TYPES:
        return value
    for item in QUESTION_TYPES:
        if item and item in value:
            return item
    return DEFAULT_TYPE


def _normalize_confidence(raw: Any) -> Optional[float]:
    """把模型自评置信度收敛到 0~1；缺失 / 无法解析时返回 ``None``（按放行处理）。

    为什么缺失返回 ``None`` 而不是低分：模型没给（或给了脏值）不等于"没看清"，
    闸门对 ``None`` 一律放行，避免把解析失败误伤成拦截用户重新拍照。
    """
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN 自我保护
        return None
    return min(1.0, max(0.0, value))


def ocr_image(image_b64: str) -> str:
    """对题目照片做 OCR 转写。

    Args:
        image_b64: 图片 base64 或 data URL。

    Returns:
        转写出的题目文字（可能为空串，表示图中无题）。

    Raises:
        ArkError: 方舟调用失败。
    """
    text = ark_respond(
        messages=[{"role": "user", "content": "请按规则转写这张图片里的数学题。"}],
        system=ocr_prompt.PROMPT,
        image_b64=image_b64,
    )
    return (text or "").strip()


async def ocr_image_async(image_b64: str) -> str:
    """``ocr_image`` 的异步版：不阻塞事件循环（await ``ark_respond_async``）。

    Args:
        image_b64: 图片 base64 或 data URL。

    Returns:
        转写出的题目文字（可能为空串，表示图中无题）。

    Raises:
        ArkError: 方舟调用失败。
    """
    text = await ark_respond_async(
        messages=[{"role": "user", "content": "请按规则转写这张图片里的数学题。"}],
        system=ocr_prompt.PROMPT,
        image_b64=image_b64,
    )
    return (text or "").strip()


def _merge_question_text(ocr_text: str, typed_text: str) -> str:
    """合并 OCR 与手输题面：图在前、文在后，去重空串。"""
    parts: List[str] = []
    if (ocr_text or "").strip():
        parts.append(ocr_text.strip())
    if (typed_text or "").strip():
        parts.append(typed_text.strip())
    return "\n".join(parts).strip()


def _build_analysis(raw: str, question_text: str) -> Dict[str, Any]:
    """把方舟分析响应收敛成统一的返回结构。

    ``confidence`` 为模型自评置信度（0~1），缺失 / 解析失败时为 ``None``——
    由编排层低置信度重拍闸门按「放行」处理。
    """
    data = _extract_json(raw)
    if not isinstance(data, dict):
        data = {}

    return {
        "knowledge": str(data.get("knowledge") or DEFAULT_KNOWLEDGE).strip() or DEFAULT_KNOWLEDGE,
        "difficulty": _normalize_difficulty(data.get("difficulty")),
        "type": _normalize_type(data.get("type")),
        "question_text": question_text,
        "correct_answer": str(data.get("correct_answer") or "").strip(),
        "solution": str(data.get("solution") or "").strip(),
        "confidence": _normalize_confidence(data.get("confidence")),
    }


def analyze_question(*, text: Optional[str], image_b64: Optional[str]) -> Dict[str, Any]:
    """识别题目并给出知识点 / 难度 / 题型（同步版）。

    Args:
        text: 用户输入的题目文字，可为空。
        image_b64: 题目照片 base64 或 data URL，可为空；非空时先做 OCR 并与 ``text`` 合并。

    Returns:
        ``{"knowledge", "difficulty", "type", "question_text", "correct_answer",
        "solution", "confidence"}``。
        ``question_text`` 是合并后的完整题面，供编排层写入会话上下文；
        ``confidence`` 是模型自评置信度（0~1），缺失时为 ``None``。

    Raises:
        ApiError: code=400，文字与图片同时为空。
        ArkError: 方舟调用失败，或返回内容无法解析为 JSON。
    """
    typed_text = (text or "").strip()
    image = (image_b64 or "").strip()

    if not typed_text and not image:
        raise ApiError(CODE_BAD_REQUEST, "题目文字与图片不能同时为空")

    ocr_text = ocr_image(image) if image else ""
    question_text = _merge_question_text(ocr_text, typed_text)
    if not question_text:
        raise ApiError(CODE_BAD_REQUEST, "没能从图片里读出题目，换张清楚点的照片试试")

    raw = ark_respond(
        messages=[{"role": "user", "content": _ANALYZE_INSTRUCTION + question_text}],
        system=tutor_prompt.PROMPT,
    )
    return _build_analysis(raw, question_text)


async def analyze_question_async(*, text: Optional[str], image_b64: Optional[str]) -> Dict[str, Any]:
    """``analyze_question`` 的异步版：OCR 与分析两步均 ``await``，不阻塞事件循环。

    Args:
        text: 用户输入的题目文字，可为空。
        image_b64: 题目照片 base64 或 data URL，可为空；非空时先做 OCR 并与 ``text`` 合并。

    Returns:
        与 ``analyze_question`` 完全相同的返回结构（含 ``confidence``）。

    Raises:
        ApiError: code=400，文字与图片同时为空。
        ArkError: 方舟调用失败，或返回内容无法解析为 JSON。
    """
    typed_text = (text or "").strip()
    image = (image_b64 or "").strip()

    if not typed_text and not image:
        raise ApiError(CODE_BAD_REQUEST, "题目文字与图片不能同时为空")

    ocr_text = await ocr_image_async(image) if image else ""
    question_text = _merge_question_text(ocr_text, typed_text)
    if not question_text:
        raise ApiError(CODE_BAD_REQUEST, "没能从图片里读出题目，换张清楚点的照片试试")

    raw = await ark_respond_async(
        messages=[{"role": "user", "content": _ANALYZE_INSTRUCTION + question_text}],
        system=tutor_prompt.PROMPT,
    )
    return _build_analysis(raw, question_text)


__all__ = [
    "analyze_question",
    "analyze_question_async",
    "ocr_image",
    "ocr_image_async",
    "DIFFICULTY_LEVELS",
    "QUESTION_TYPES",
]
