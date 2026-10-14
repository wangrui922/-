"""Manim 讲解场景解析 AI 服务（方向四·2）。

职责：把「题干/AI 讲解文本」交给方舟大模型，让它输出**结构化 JSON**：
    {"scene": "<模板名>", "params": {...}}
然后由 ``manim_templates.build_scene`` 拼装成可渲染的 Manim 源码。

为什么不让 AI 直接写 Manim 代码？
    任写 10 行有 3 行语法/API 错误很正常，而 2 核 4G 机器每次渲染失败都要
    重新排队重试，成本高。选模板 + 抽参数是「封闭式问题」，模型输出可靠，
    场景代码由受控模板生成，渲染成功率 ≈ 100%。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

from app.ai.ark_client import ArkError, ark_respond
from app.services.manim_templates import SCENE_ORDER, scene_list_prompt

logger = logging.getLogger("app.services.manim_scene")

#: 解析失败后的兜底场景（保证前端永远有东西返回）
FALLBACK_SCENE = "function_plot"
FALLBACK_PARAMS: Dict[str, Any] = {"expr": "x**2", "title": "y = x²"}

_SYSTEM_TEMPLATE = """你是数学讲解动画的场景规划师。根据学生发来的题目/AI讲解文本，
判断最适合用动画演示的场景，并抽取必要参数。

{scene_list}

输出**严格 JSON**（不要任何其他文字、不要 markdown 代码块），格式：
{{"scene": "<模板名>", "params": {{...}}}}

参数规则（每个模板的参数）：
- function_plot: expr(数学表达式,变量用x,如 x**2 / 2*x+1 / 1/x)、title(标题中文)、可选 x_min/x_max/y_min/y_max
- fraction_pie: numerator(分子)、denominator(分母)、title(如 3/4)
- number_line: start(起点整数)、steps(向右跳几步)、title(如 3+2=5)
- area_rect: width(长/宽方向格数)、height(高方向格数)、可选 unit(单位名)、title(如 面积=4x3=12格)
- geometry_tri: title(默认 三角形内角和=180度)
- bar_compare: items([{{name:名称, value:数值}} 至少2个,最多5个])、title(标题)
- clock_angle: hour(小时 1-12)、minute(分钟 0-59)、title(如 3:00 夹角=90度)

要求：
1. 参数必须是可计算的数值，单位/中文放 title 里；
2. 无法确定时使用最贴近的模板和合理默认值；
3. 圆内角和/直白演示优先选 geometry_tri 或 fraction_pie。"""


def _parse_json_response(text: str) -> Optional[Dict[str, Any]]:
    """从模型输出中尽量稳健地捞 JSON。"""
    if not text:
        return None
    text = text.strip()
    # 去掉 ```json ... ``` 围栏
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    # 兜底：截取第一个 { 到最后一个 }
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None


def _ensure_scene(data: Optional[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    """校验模板名与参数 dict，返回 (scene, params)。"""
    if not isinstance(data, dict):
        return FALLBACK_SCENE, dict(FALLBACK_PARAMS)
    scene = str(data.get("scene") or "").strip()
    if scene not in SCENE_ORDER:
        scene = FALLBACK_SCENE
    params = data.get("params")
    if not isinstance(params, dict):
        return FALLBACK_SCENE, dict(FALLBACK_PARAMS)
    return scene, dict(params)


def plan_scene(text: str, topic: Optional[str] = None, timeout: float = 60.0) -> Tuple[str, Dict[str, Any]]:
    """解析文本 -> (scene_type, scene_params)。

    调用方舟（闭卷选型），失败时降级到兜底场景。**不抛异常**——
    动画生成是增强体验项，AI 不可用也要保证接口能返回（前端可重试）。

    Args:
        text: 题干或 AI 讲解文本。
        topic: 知识点提示（如「分数」），可为空。
        timeout: 方舟单次超时秒数。

    Returns:
        ``(scene_type, params)`` 二元组。
    """
    if not (text or "").strip():
        return FALLBACK_SCENE, dict(FALLBACK_PARAMS)
    user_msg = f"题目/讲解：{text.strip()[:1500]}"
    if topic and topic.strip():
        user_msg += f"\n知识点提示：{topic.strip()[:50]}"
    try:
        reply = ark_respond(
            messages=[{"role": "user", "content": user_msg}],
            system=_SYSTEM_TEMPLATE.format(scene_list=scene_list_prompt()),
            timeout=timeout,
        )
        data = _parse_json_response(reply)
        scene, params = _ensure_scene(data)
        logger.info("plan_scene -> %s params=%s", scene, json.dumps(params, ensure_ascii=False)[:200])
        return scene, params
    except (ArkError, Exception) as exc:  # noqa: BLE001 - 解析失败一律兜底
        logger.warning("plan_scene 降级到兜底场景: %s", exc)
        return FALLBACK_SCENE, dict(FALLBACK_PARAMS)


__all__ = ["plan_scene", "FALLBACK_SCENE"]