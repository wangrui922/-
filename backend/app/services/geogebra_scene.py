"""GeoGebra 讲解场景解析 AI 服务（方向四·3）。

职责：把「题干/AI 讲解文本」交给方舟大模型，让它输出**结构化 JSON**：
    {"scene": "<模板名>", "params": {...}}
然后由 ``geogebra_templates.build_scene`` 拼装成 GeoGebra 指令列表。

与 Manim 的区别：
    * 模板更轻量（纯指令列表，无渲染）；
    * 兜底场景改为三角形内角和（最通用的小学几何题）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

from app.ai.ark_client import ArkError, ark_respond
from app.services.geogebra_templates import SCENE_ORDER, scene_list_prompt

logger = logging.getLogger("app.services.geogebra_scene")

#: 解析失败后的兜底场景
FALLBACK_SCENE = "geometry_angle_sum"
FALLBACK_PARAMS: Dict[str, Any] = {"ax": 2, "ay": 3, "bx": 6, "by": 1, "cx": 4, "cy": 5}

_SYSTEM_TEMPLATE = """你是数学几何交互的场景规划师。根据学生发来的题目/AI讲解文本，
判断最适合用 GeoGebra 动态几何画板演示的场景，并抽取必要参数。

{scene_list}

输出**严格 JSON**（不要任何其他文字、不要 markdown 代码块），格式：
{{"scene": "<模板名>", "params": {{...}}}}

参数规则（每个模板的参数）：
- geometry_angle_sum: ax, ay, bx, by, cx, cy（三个顶点坐标，默认 2,3 / 6,1 / 4,5）
- area_rectangle: width(长,整数2-10), height(宽,整数2-10), unit(单位名如"厘米")
- area_triangle: base(底,整数3-10), height(高,整数2-8)
- circle_properties: radius(半径,整数2-6)
- parallelogram_area: base(底,整数3-10), height(高,整数2-8), skew(倾斜量,整数1-base-1)
- coordinate_points: points([{{name:名称,x:坐标,y:坐标}}],2-6个点)

要求：
1. 参数必须是可计算的数值，单位/中文放 title 里；
2. 无法确定时使用最贴近的模板和合理默认值；
3. 涉及三角形内角和/角度变化优先选 geometry_angle_sum；
4. 涉及面积计算优先选 area_rectangle / area_triangle / parallelogram_area；
5. 涉及圆/半径/直径/周长优先选 circle_properties。"""


def _parse_json_response(text: str) -> Optional[Dict[str, Any]]:
    """从模型输出中尽量稳健地捞 JSON。"""
    if not text:
        return None
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
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


def plan_scene(text: str, topic: Optional[str] = None, timeout: float = 30.0) -> Tuple[str, Dict[str, Any]]:
    """解析文本 -> (scene_type, scene_params)。

    调用方舟（闭卷选型），失败时降级到兜底场景。**不抛异常**——
    几何交互是增强体验项，AI 不可用也要保证接口能返回。
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
    except (ArkError, Exception) as exc:  # noqa: BLE001
        logger.warning("plan_scene 降级到兜底场景: %s", exc)
        return FALLBACK_SCENE, dict(FALLBACK_PARAMS)


__all__ = ["plan_scene", "FALLBACK_SCENE"]
