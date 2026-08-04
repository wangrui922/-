"""Report Agent：把一周快照写成给家长看的三段话（AI-007 · Epic6）。

两层设计，核心目标是 **AI 挂了也必须出报告**（PRD 验收项 V4）：

    generate_report_text()   → 调方舟；任何异常都吞掉，转 build_fallback_report()
    build_fallback_report()  → 纯本地拼装，用 profile_service 已生成的中文推荐理由

因此 ``generate_report_text`` **对外永不抛异常、永不返回空**，
家长视角对降级完全无感知；``source`` 字段（``"ai"`` / ``"fallback"``）
只给运营侧统计 AI 成功率用，前端不得展示。

结构参照 ``agents/exercise.py``：``_normalize_*`` 系列 + 模块级 ``__all__``。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.ai.agents._json_utils import extract_json as _extract_json
from app.ai.ark_client import ark_respond
from app.ai.prompts import report as report_prompt
from app.core.error_categories import ERROR_LABELS

logger = logging.getLogger("app.ai.agents.report")

#: 叙述来源标记
SOURCE_AI: str = "ai"
SOURCE_FALLBACK: str = "fallback"

#: ``advice_items`` 的条数区间（提示词里也写死了同样的约束）
ADVICE_MIN: int = 2
ADVICE_MAX: int = 3

#: 单条文本的最大长度（防模型跑飞，截断而不是丢弃）
HEADLINE_MAX_LEN: int = 60
PARAGRAPH_MAX_LEN: int = 200
ADVICE_MAX_LEN: int = 60

#: 兜底建议池（真实推荐不足 2 条时补齐，全部是家长可直接照做的动作）
_GENERIC_ADVICE: tuple = (
    "每天固定 15 分钟做数学，做完让孩子自己对一遍答案",
    "把这周做错的题挑 3 道重做一次，做对了再讲一遍思路",
    "做完题先别急着交，带孩子从头检查一遍单位和答句",
)


# ==================================================================== 内部工具
def _clean_text(raw: Any, max_len: int) -> str:
    """把模型给的任意值收敛成一行干净中文，超长截断。"""
    text = str(raw or "").strip()
    text = text.replace("\r", " ").replace("\n", " ").replace("```", "")
    while "  " in text:
        text = text.replace("  ", " ")
    if len(text) > max_len:
        text = text[:max_len].rstrip()
    return text


def _safe_name(student_name: Optional[str]) -> str:
    """学生称呼兜底：为空时用「孩子」，绝不回显手机号或 ID。"""
    name = str(student_name or "").strip()
    return name or "孩子"


def _rate_text(rate: Optional[float]) -> str:
    """正确率 → 中文百分比；``None``（零练习）时给「暂时还看不出来」。"""
    if rate is None:
        return "暂时还看不出来"
    return f"{round(float(rate) * 100)}%"


def _top_weak_names(snapshot: Any, limit: int = 3) -> List[str]:
    """取快照里 Top N 薄弱知识点名（已按薄弱程度排好序）。"""
    names: List[str] = []
    for item in getattr(snapshot, "mastery", []) or []:
        name = str((item or {}).get("kp_name") or "").strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def _top_error_label(snapshot: Any) -> str:
    """取快照里出现次数最多的错因中文标签；无数据时返回空串。"""
    patterns = (getattr(snapshot, "error_patterns", {}) or {}).get("error_patterns")
    if not isinstance(patterns, list):
        return ""
    best_label = ""
    best_count = 0
    for item in patterns:
        if not isinstance(item, dict):
            continue
        try:
            count = int(item.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count > best_count:
            best_count = count
            best_label = str(item.get("label") or ERROR_LABELS.get(str(item.get("category")), "")).strip()
    return best_label if best_count > 0 else ""


def _build_user_payload(snapshot: Any, comparison: Any, student_name: str) -> str:
    """把快照压成紧凑中文描述喂给模型（**只喂真实知识点名与真实数字**）。

    Args:
        snapshot: ``report_service.WeeklySnapshot``。
        comparison: ``report_service.ComparisonResult``。
        student_name: 学生称呼。

    Returns:
        用户消息正文。
    """
    stat = getattr(snapshot, "engagement", None)
    study_days = getattr(stat, "study_days", 0)
    attempt_count = getattr(stat, "attempt_count", 0)
    correct_count = getattr(stat, "correct_count", 0)
    correct_rate = getattr(stat, "correct_rate", None)
    new_mistake = getattr(stat, "new_mistake_count", 0)
    solved_mistake = getattr(stat, "solved_mistake_count", 0)
    tutor_count = getattr(stat, "tutor_session_count", 0)

    lines: List[str] = [
        f"孩子的名字：{student_name}",
        "",
        "【本周学习记录】",
        f"- 学习天数：{study_days} 天",
        f"- 做题数：{attempt_count} 道，其中做对 {correct_count} 道",
        f"- 正确率：{_rate_text(correct_rate)}",
        f"- 本周新增错题：{new_mistake} 道，已经弄懂并攻克：{solved_mistake} 道",
        f"- 找老师讲题的次数：{tutor_count} 次",
    ]

    weak_names = _top_weak_names(snapshot)
    lines.append("")
    if weak_names:
        lines.append("【这周还需要多练的知识点（按需要程度排序，只能用这些名字）】")
        for index, name in enumerate(weak_names, start=1):
            lines.append(f"{index}. {name}")
    else:
        lines.append("【这周还需要多练的知识点】暂无记录，不要编造。")

    top_label = _top_error_label(snapshot)
    lines.append("")
    lines.append(f"【最常出现的错误类型】{top_label or '暂无记录，不要编造'}")

    lines.append("")
    if getattr(comparison, "available", False):
        rate_delta = getattr(comparison, "correct_rate_delta", None)
        if rate_delta is None:
            lines.append("【和上周比】有上周记录，但正确率无法直接比较。")
        else:
            trend = "提高" if rate_delta > 0 else ("下降" if rate_delta < 0 else "持平")
            lines.append(f"【和上周比】正确率{trend}了 {abs(round(float(rate_delta) * 100))} 个百分点。")
        improved = [str(row.get("kp_name") or "") for row in (getattr(comparison, "improved", []) or [])]
        declined = [str(row.get("kp_name") or "") for row in (getattr(comparison, "declined", []) or [])]
        improved = [name for name in improved if name][:3]
        declined = [name for name in declined if name][:3]
        if improved:
            lines.append(f"- 有进步的：{'、'.join(improved)}")
        if declined:
            lines.append(f"- 退步一点的：{'、'.join(declined)}")
    else:
        lines.append("【和上周比】这是第一份小结，没有上周数据可比。请在第二段里说明这一点。")

    reasons = [
        str((item or {}).get("reason") or "").strip()
        for item in (getattr(snapshot, "recommendations", []) or [])
    ]
    reasons = [item for item in reasons if item][:3]
    if reasons:
        lines.append("")
        lines.append("【可以参考的练习建议方向（自己改写成家长能照做的动作，不要照抄）】")
        for index, reason in enumerate(reasons, start=1):
            lines.append(f"{index}. {reason}")

    if attempt_count == 0:
        lines.append("")
        lines.append("【特别注意】这周孩子一道题都没练。不要编造任何练习数据，"
                     "第一段要如实说明这周还没有练习记录，语气要温和鼓励。")
    elif attempt_count < 3:
        lines.append("")
        lines.append("【特别注意】这周练习量很少，必须在文中说明判断只能作参考。")

    lines.append("")
    lines.append("请按你的规则，只输出一个 JSON 对象。")
    return "\n".join(lines)


def _normalize_advice(raw: Any, fallback_items: List[str]) -> List[str]:
    """把 ``advice_items`` 收敛成 2~3 条非空中文建议。

    Args:
        raw: 模型给的原始值（可能是 list / str / None）。
        fallback_items: 补齐用的候选建议。

    Returns:
        长度在 ``[ADVICE_MIN, ADVICE_MAX]`` 之间的字符串列表。
    """
    items: List[str] = []
    if isinstance(raw, list):
        for entry in raw:
            text = _clean_text(entry, ADVICE_MAX_LEN)
            if text and text not in items:
                items.append(text)
    elif raw:
        text = _clean_text(raw, ADVICE_MAX_LEN)
        if text:
            items.append(text)

    for candidate in list(fallback_items) + list(_GENERIC_ADVICE):
        if len(items) >= ADVICE_MIN:
            break
        text = _clean_text(candidate, ADVICE_MAX_LEN)
        if text and text not in items:
            items.append(text)

    return items[:ADVICE_MAX]


def _normalize_output(raw: Any, fallback: Dict[str, Any]) -> Dict[str, Any]:
    """把模型输出收敛成 §4.3 契约结构；任一必填段缺失就退回兜底文案。

    Args:
        raw: ``extract_json`` 的结果。
        fallback: ``build_fallback_report()`` 的结果，用于逐字段补位。

    Returns:
        完整的 ``report_text`` dict。
    """
    data = raw if isinstance(raw, dict) else {}

    headline = _clean_text(data.get("headline"), HEADLINE_MAX_LEN) or fallback["headline"]
    overview = _clean_text(data.get("overview_text"), PARAGRAPH_MAX_LEN) or fallback["overview_text"]
    progress = _clean_text(data.get("progress_text"), PARAGRAPH_MAX_LEN) or fallback["progress_text"]
    advice = _normalize_advice(data.get("advice_items"), list(fallback["advice_items"]))

    return {
        "headline": headline,
        "overview_text": overview,
        "progress_text": progress,
        "advice_items": advice,
        "source": SOURCE_AI,
        "prompt_version": report_prompt.VERSION,
    }


# ==================================================================== 兜底文案
def build_fallback_report(snapshot: Any, comparison: Any, student_name: str) -> Dict[str, Any]:
    """AI 不可用时的本地降级文案（纯模板拼装，绝不调用外部服务）。

    文案来源全部是真实数据：计数取自快照 ``engagement``，
    建议取自 ``snapshot.recommendations[].reason``（后端已拼好的完整中文句子）。

    Args:
        snapshot: ``report_service.WeeklySnapshot``。
        comparison: ``report_service.ComparisonResult``。
        student_name: 学生称呼（为空时自动降级为「孩子」）。

    Returns:
        ``report_text`` dict，``source == "fallback"``。
    """
    name = _safe_name(student_name)
    stat = getattr(snapshot, "engagement", None)
    study_days = int(getattr(stat, "study_days", 0) or 0)
    attempt_count = int(getattr(stat, "attempt_count", 0) or 0)
    correct_count = int(getattr(stat, "correct_count", 0) or 0)
    correct_rate = getattr(stat, "correct_rate", None)
    new_mistake = int(getattr(stat, "new_mistake_count", 0) or 0)
    solved_mistake = int(getattr(stat, "solved_mistake_count", 0) or 0)
    tutor_count = int(getattr(stat, "tutor_session_count", 0) or 0)

    weak_names = _top_weak_names(snapshot)
    weak_text = "、".join(weak_names) if weak_names else ""

    # ---- headline ---------------------------------------------------------
    if attempt_count == 0:
        headline = f"{name}这周还没有练习记录，周末一起补上吧"
    elif weak_text:
        headline = f"{name}这周练了 {attempt_count} 道题，{weak_names[0]}还需要多练一练"
    else:
        headline = f"{name}这周练了 {attempt_count} 道题，保持住这个节奏"
    headline = _clean_text(headline, HEADLINE_MAX_LEN)

    # ---- 第一段：本周概览 --------------------------------------------------
    if attempt_count == 0:
        overview = (
            f"这周{name}还没有留下练习记录，所以暂时没法看出学习情况。"
            "不用着急，周末抽 15 分钟一起做几道题，下周就能看到变化了。"
        )
    else:
        parts = [f"这周{name}学了 {study_days} 天，一共做了 {attempt_count} 道题，做对 {correct_count} 道"]
        if correct_rate is not None:
            parts.append(f"，正确率 {_rate_text(correct_rate)}")
        parts.append("。")
        if new_mistake > 0:
            parts.append(f"本周新收进错题本 {new_mistake} 道")
            if solved_mistake > 0:
                parts.append(f"，已经弄懂 {solved_mistake} 道")
            parts.append("。")
        if tutor_count > 0:
            parts.append(f"遇到不会的题，{name}主动找老师问了 {tutor_count} 次，这个习惯很好。")
        overview = "".join(parts)
    overview = _clean_text(overview, PARAGRAPH_MAX_LEN)

    # ---- 第二段：能力变化 --------------------------------------------------
    if not getattr(comparison, "available", False):
        if weak_text:
            progress = (
                f"这是{name}的第一份周小结，还没有上周的数据可以对比。"
                f"从这周的记录看，{weak_text}这块儿还不太稳，可以先从这里练起。"
                "下周开始就能看到进步对比了。"
            )
        else:
            progress = (
                f"这是{name}的第一份周小结，还没有上周的数据可以对比。"
                "先按现在的节奏坚持一周，下周开始就能看到进步对比了。"
            )
    else:
        rate_delta = getattr(comparison, "correct_rate_delta", None)
        improved = [str(row.get("kp_name") or "") for row in (getattr(comparison, "improved", []) or [])]
        declined = [str(row.get("kp_name") or "") for row in (getattr(comparison, "declined", []) or [])]
        improved = [item for item in improved if item][:2]
        declined = [item for item in declined if item][:2]

        chunks: List[str] = []
        if rate_delta is None:
            chunks.append("和上周比，这周的练习记录还不够多，正确率暂时不好直接比较。")
        elif rate_delta > 0:
            chunks.append(f"和上周比，正确率提高了 {abs(round(float(rate_delta) * 100))} 个百分点，有进步。")
        elif rate_delta < 0:
            chunks.append(f"和上周比，正确率低了 {abs(round(float(rate_delta) * 100))} 个百分点，是正常波动，不用紧张。")
        else:
            chunks.append("和上周比，正确率基本持平，状态挺稳。")
        if improved:
            chunks.append(f"{'、'.join(improved)}这几块明显熟练了。")
        if declined:
            chunks.append(f"{'、'.join(declined)}这周有点手生，可以再带着练练。")
        elif weak_text and not improved:
            chunks.append(f"{weak_text}还需要多练一练。")
        progress = "".join(chunks)
    progress = _clean_text(progress, PARAGRAPH_MAX_LEN)

    # ---- 第三段：建议清单 --------------------------------------------------
    reason_items: List[str] = []
    for item in (getattr(snapshot, "recommendations", []) or [])[:ADVICE_MAX]:
        reason = _clean_text((item or {}).get("reason"), ADVICE_MAX_LEN)
        if reason:
            reason_items.append(reason)
    advice_items = _normalize_advice(reason_items, [])

    return {
        "headline": headline,
        "overview_text": overview,
        "progress_text": progress,
        "advice_items": advice_items,
        "source": SOURCE_FALLBACK,
        "prompt_version": report_prompt.VERSION,
    }


# ==================================================================== 对外入口
def generate_report_text(snapshot: Any, comparison: Any, student_name: str) -> Dict[str, Any]:
    """生成给家长看的三段叙述。**对外永不抛异常、永不返回空**。

    Args:
        snapshot: ``report_service.WeeklySnapshot``。
        comparison: ``report_service.ComparisonResult``。
        student_name: 学生称呼（为空自动降级为「孩子」）。

    Returns:
        ``report_text`` dict（结构见 ``docs/epic6-design.md`` §4.3）：
        ``{headline, overview_text, progress_text, advice_items,
        source, prompt_version}``。AI 不可用时 ``source == "fallback"``。
    """
    name = _safe_name(student_name)
    fallback = build_fallback_report(snapshot, comparison, name)

    try:
        payload = _build_user_payload(snapshot, comparison, name)
        raw = ark_respond(
            messages=[{"role": "user", "content": payload}],
            system=report_prompt.PROMPT,
        )
        parsed = _extract_json(raw)
        result = _normalize_output(parsed, fallback)
    except Exception as exc:  # noqa: BLE001 - 家长必须拿到报告，任何异常都降级
        logger.warning("Report Agent 降级为本地文案：%s", exc)
        return fallback

    # 三段任一为空说明模型跑飞了，整体退回兜底（比拼出半截文案更安全）
    if not result["headline"] or not result["overview_text"] or not result["progress_text"]:
        logger.warning("Report Agent 输出字段不完整，降级为本地文案")
        return fallback
    if len(result["advice_items"]) < ADVICE_MIN:
        result["advice_items"] = _normalize_advice(
            result["advice_items"], list(fallback["advice_items"])
        )

    return result


__all__ = [
    "generate_report_text",
    "build_fallback_report",
    "SOURCE_AI",
    "SOURCE_FALLBACK",
    "ADVICE_MIN",
    "ADVICE_MAX",
]
