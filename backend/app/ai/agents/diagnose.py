"""Diagnosis Agent：判断孩子"为什么错"与能力缺口（docs §C.1 第 2 个 Agent）。

输入 ``{question, student_answer, history, profile}``，输出 ``{error, reason, gap}``：
    * ``error``：错误类型（读题错/方法错/计算错/知识点缺/检查漏）；
    * ``reason``：一句话说清具体错在哪一步；
    * ``gap``：暴露出的能力缺口，供画像更新与出题使用。

结论只给系统与老师看，**不直接展示给孩子**（对孩子说话是 Tutor Agent 的事）。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.ai.agents._json_utils import extract_json as _extract_json
from app.ai.ark_client import ark_respond
from app.ai.prompts import tutor as tutor_prompt
from app.core.error_categories import (
    ERROR_LABELS,
    ERROR_TYPES,
    error_label,
    normalize_error,
)

#: 解析不出时的兜底错因（中文标签，与 ERROR_LABELS 一致）
DEFAULT_ERROR_TYPE: str = "方法错"

#: 送进模型的历史对话最大条数（太长既费 token 也稀释重点）
MAX_HISTORY = 12

_DIAGNOSE_INSTRUCTION: str = (
    "下面是一道题、孩子的作答和辅导对话。请判断孩子为什么错、缺什么能力。"
    "只输出JSON，不要任何解释、前后缀或代码块标记。\n"
    '格式严格为：{"error":"错误类型","reason":"一句话说清错在哪一步","gap":"暴露的能力缺口"}\n'
    f"error 只能取 {'/'.join(ERROR_TYPES)} 之一。\n"
)


def _normalize_error(raw: Any) -> str:
    """把模型给的错误类型收敛到五类机器码（统一枚举，缺口②）。"""
    return normalize_error(raw)


def _format_history(history: Optional[List[Dict[str, Any]]]) -> str:
    """把对话历史压成可读文本（只取最近 ``MAX_HISTORY`` 条）。"""
    items = [item for item in (history or []) if isinstance(item, dict)]
    if not items:
        return "（无）"
    lines = []
    for item in items[-MAX_HISTORY:]:
        role = "孩子" if str(item.get("role")) == "user" else "老师"
        content = str(item.get("content") or "").strip()
        if content:
            lines.append(f"{role}：{content}")
    return "\n".join(lines) or "（无）"


def _format_profile(profile: Optional[Dict[str, Any]]) -> str:
    """把学生画像压成一行 JSON 文本。"""
    if not profile:
        return "（暂无画像）"
    try:
        return json.dumps(profile, ensure_ascii=False)[:600]
    except (TypeError, ValueError):
        return str(profile)[:600]


def diagnose(
    *,
    question_text: str,
    student_answer: str,
    history: List[Dict[str, Any]],
    profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """诊断孩子的错误类型、原因与能力缺口。

    Args:
        question_text: 题目文字（含【图中信息】补充）。
        student_answer: 孩子的作答内容。
        history: 辅导对话历史，元素形如 ``{"role": "user"|"assistant", "content": "..."}``。
        profile: 学生画像摘要（mastery / weak_points 等），可为空。

    Returns:
        ``{"error": str, "reason": str, "gap": str}``。

    Raises:
        ArkError: 方舟调用失败，或返回内容无法解析为 JSON。
    """
    user_content = (
        f"{_DIAGNOSE_INSTRUCTION}\n"
        f"【题目】\n{(question_text or '').strip() or '（未提供）'}\n\n"
        f"【孩子的作答】\n{(student_answer or '').strip() or '（未作答）'}\n\n"
        f"【辅导对话】\n{_format_history(history)}\n\n"
        f"【学生画像】\n{_format_profile(profile)}"
    )

    raw = ark_respond(
        messages=[{"role": "user", "content": user_content}],
        system=tutor_prompt.PROMPT,
    )
    data = _extract_json(raw)
    if not isinstance(data, dict):
        data = {}

    err = _normalize_error(data.get("error"))
    return {
        "error": err,
        "error_label": error_label(err),
        "reason": str(data.get("reason") or "").strip() or "暂未定位到具体出错步骤",
        "gap": str(data.get("gap") or "").strip() or "需要更多作答样本才能判断",
    }


__all__ = ["diagnose", "ERROR_TYPES"]
