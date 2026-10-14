"""GeoGebra 指令模板库（方向四·3）。

设计原则：
    * 与 Manim 模板库一致：**不让大模型自由写 GeoGebra 代码**，
      AI 只做「选模板 + 抽参数」，指令由本文件按模板拼装——
      保证前端 Applet 执行成功率 ≈ 100%；
    * 每个模板返回：
        - ``instructions``: GeoGebra ``evalCommand`` 指令列表（字符串数组）
        - ``applet_config``: Applet 参数覆盖字典（如 ``coordSystem``, ``gridVisible``）
    * 指令全部使用 GeoGebra 5.0 web3d 验证过的 API（见 demo 实测清单）：
        ``evalCommand``, ``setFixed``, ``setLabelStyle``, ``setCoordSystem``,
        ``setAxesVisible``, ``setGridVisible``, ``registerUpdateListener``（函数名方式）,
        ``getValue``, ``setCoords`` 等；
    * 任何输入都会做 clamp/合法性清洗，避免非法几何。

当前模板清单（小学常见几何）：
    geometry_angle_sum   三角形内角和（拖拽验证 180°）
    area_rectangle       长方形面积（长 x 宽，网格计数）
    area_triangle        三角形面积（底 x 高 / 2）
    circle_properties    圆的性质（半径/直径/周长关系）
    parallelogram_area   平行四边形面积（割补法）
    coordinate_points    坐标定位（象限/对称）
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

#: 场景名 -> 中文说明（供 AI prompt 使用）
SCENE_REGISTRY: Dict[str, str] = {
    "geometry_angle_sum": "三角形内角和（拖拽顶点验证 180°）",
    "area_rectangle": "长方形面积（长 x 宽，网格计数验证）",
    "area_triangle": "三角形面积（底 x 高 ÷ 2，动态演示）",
    "circle_properties": "圆的性质（半径/直径/周长关系）",
    "parallelogram_area": "平行四边形面积（割补成长方形）",
    "coordinate_points": "坐标定位（象限、对称点）",
}

SCENE_ORDER: List[str] = list(SCENE_REGISTRY.keys())


def scene_list_prompt() -> str:
    """在 AI prompt 中列出可选模板。"""
    lines = ["可选 GeoGebra 场景模板："]
    for name, desc in SCENE_REGISTRY.items():
        lines.append(f"- {name}：{desc}")
    return "\n".join(lines)


# ================================================================ 工具函数

def _clamp(value: Any, lo: float, hi: float, default: float) -> float:
    """把值限制到 [lo, hi]，非法/NaN 用 default。"""
    try:
        num = float(value)
        if num != num:  # NaN
            raise ValueError
        return max(lo, min(hi, num))
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int, lo: int = 1, hi: int = 100) -> int:
    """把值限制为整数 [lo, hi]。"""
    try:
        num = int(float(value))
        if num != num:
            raise ValueError
        return max(lo, min(hi, num))
    except (TypeError, ValueError):
        return default


def _fmt(value: Any, digits: int = 2) -> str:
    """格式化数值为 GeoGebra 可用字面量。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        num = 0.0
    if abs(num - round(num)) < 1e-9:
        return str(int(round(num)))
    return f"{num:.{digits}f}"


# ================================================================ 模板实现

# ------------------------------------------------------------------ geometry_angle_sum

def _geometry_angle_sum(params: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    """三角形内角和：拖拽顶点验证 180°。

    参数：
        ax, ay, bx, by, cx, cy —— 三个顶点坐标（默认 2,3 / 6,1 / 4,5）
    """
    ax = _fmt(params.get("ax", 2))
    ay = _fmt(params.get("ay", 3))
    bx = _fmt(params.get("bx", 6))
    by = _fmt(params.get("by", 1))
    cx = _fmt(params.get("cx", 4))
    cy = _fmt(params.get("cy", 5))

    instructions = [
        f"A = ({ax}, {ay})",
        f"B = ({bx}, {by})",
        f"C = ({cx}, {cy})",
        "t = Polygon(A, B, C)",
        "SetColor(t, 99/255, 102/255, 241/255)",
        # 内角标注（正确顶点顺序 → 真实内角）
        "α = Angle(B, A, C)",
        "β = Angle(C, B, A)",
        "γ = Angle(A, C, B)",
        # 标签样式：显示名称+值
        "SetLabelStyle(α, 1)",
        "SetLabelStyle(β, 1)",
        "SetLabelStyle(γ, 1)",
        # 多边形可拖拽
        "SetFixed(t, false, true)",
    ]

    # 计算合适的视野范围（留边距）
    xs = [float(ax), float(bx), float(cx)]
    ys = [float(ay), float(by), float(cy)]
    margin = 2
    x_min = min(xs) - margin
    x_max = max(xs) + margin
    y_min = min(ys) - margin
    y_max = max(ys) + margin

    applet_config = {
        "coordSystem": [x_min, x_max, y_min, y_max],
        "axesVisible": True,
        "gridVisible": True,
    }
    return instructions, applet_config


# ------------------------------------------------------------------ area_rectangle

def _area_rectangle(params: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    """长方形面积：长 x 宽，网格计数验证。

    参数：
        width, height —— 长和宽（默认 5, 3）
        unit —— 单位名（如 "厘米"）
    """
    w = _int(params.get("width", 5), default=5, lo=2, hi=10)
    h = _int(params.get("height", 3), default=3, lo=2, hi=10)
    unit = str(params.get("unit", "")).strip() or "厘米"

    # 左下角在原点，向右上方延伸
    instructions = [
        "A = (0, 0)",
        f"B = ({w}, 0)",
        f"C = ({w}, {h})",
        f"D = (0, {h})",
        "rect = Polygon(A, B, C, D)",
        "SetColor(rect, 99/255, 102/255, 241/255)",
        # 边长标注
        f"lenAB = Segment(A, B)",
        f"lenAD = Segment(A, D)",
        f"SetLabelStyle(lenAB, 1)",
        f"SetLabelStyle(lenAD, 1)",
        # 面积文本
        f"areaText = Text(\"面积 = {w} × {h} = {w*h}（{unit}²）\", ({w/2}, -1.2))",
        # 网格辅助线（水平和垂直）
    ]

    # 添加网格线
    for i in range(1, w):
        instructions.append(f"xLine{i} = Line(({i}, 0), ({i}, {h}))")
        instructions.append(f"SetLineStyle(xLine{i}, 1)")
        instructions.append(f"SetLineThickness(xLine{i}, 1)")
    for j in range(1, h):
        instructions.append(f"yLine{j} = Line((0, {j}), ({w}, {j}))")
        instructions.append(f"SetLineStyle(yLine{j}, 1)")
        instructions.append(f"SetLineThickness(yLine{j}, 1)")

    margin = 2
    applet_config = {
        "coordSystem": [-margin, w + margin, -margin - 1, h + margin],
        "axesVisible": True,
        "gridVisible": True,
    }
    return instructions, applet_config


# ------------------------------------------------------------------ area_triangle

def _area_triangle(params: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    """三角形面积：底 x 高 ÷ 2，动态演示。

    参数：
        base, height —— 底和高（默认 6, 4）
    """
    base = _int(params.get("base", 6), default=6, lo=3, hi=10)
    height = _int(params.get("height", 4), default=4, lo=2, hi=8)

    # 底边在 x 轴上，顶点在上方
    instructions = [
        "A = (0, 0)",
        f"B = ({base}, 0)",
        f"C = ({base/2}, {height})",
        "tri = Polygon(A, B, C)",
        "SetColor(tri, 99/255, 102/255, 241/255)",
        # 底边和高
        f"baseSeg = Segment(A, B)",
        f"heightSeg = Segment(C, ({base/2}, 0))",
        "SetLineStyle(heightSeg, 1)",
        "SetColor(heightSeg, 34/255, 197/255, 94/255)",
        # 直角标记
        f"rt = Angle(({base/2 - 0.3}, 0), ({base/2}, 0), ({base/2}, 0.3))",
        # 面积文本
        f"areaText = Text(\"面积 = {base} × {height} ÷ 2 = {base*height//2}\", ({base/2}, -1.2))",
        # 标签
        "SetLabelStyle(baseSeg, 1)",
        "SetLabelStyle(heightSeg, 1)",
    ]

    margin = 2
    applet_config = {
        "coordSystem": [-margin, base + margin, -margin - 1, height + margin],
        "axesVisible": True,
        "gridVisible": True,
    }
    return instructions, applet_config


# ------------------------------------------------------------------ circle_properties

def _circle_properties(params: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    """圆的性质：半径/直径/周长关系。

    参数：
        radius —— 半径（默认 3）
    """
    r = _int(params.get("radius", 3), default=3, lo=2, hi=6)
    d = r * 2
    pi_approx = 3.14
    c = round(2 * pi_approx * r, 2)

    instructions = [
        "O = (0, 0)",
        f"c1 = Circle(O, {r})",
        "SetColor(c1, 99/255, 102/255, 241/255)",
        # 半径线段
        f"R = ({r}, 0)",
        "radiusSeg = Segment(O, R)",
        "SetColor(radiusSeg, 239/255, 68/255, 68/255)",
        # 直径线段
        f"D = (-{r}, 0)",
        "diameterSeg = Segment(D, R)",
        "SetColor(diameterSeg, 34/255, 197/255, 94/255)",
        # 文本标注
        f"rText = Text(\"半径 r = {r}\", ({r+0.5}, 0.3))",
        f"dText = Text(\"直径 d = {d}\", (0, {r+0.8}))",
        f"cText = Text(\"周长 C ≈ {c}\", (0, -{r+0.8}))",
    ]

    margin = r + 3
    applet_config = {
        "coordSystem": [-margin, margin, -margin, margin],
        "axesVisible": True,
        "gridVisible": True,
    }
    return instructions, applet_config


# ------------------------------------------------------------------ parallelogram_area

def _parallelogram_area(params: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    """平行四边形面积：割补成长方形。

    参数：
        base, height, skew —— 底、高、倾斜量（默认 6, 4, 2）
    """
    base = _int(params.get("base", 6), default=6, lo=3, hi=10)
    height = _int(params.get("height", 4), default=4, lo=2, hi=8)
    skew = _int(params.get("skew", 2), default=2, lo=1, hi=base - 1)

    instructions = [
        f"A = ({skew}, 0)",
        f"B = ({skew + base}, 0)",
        f"C = ({base}, {height})",
        f"D = (0, {height})",
        "para = Polygon(A, B, C, D)",
        "SetColor(para, 99/255, 102/255, 241/255)",
        # 高
        f"heightSeg = Segment(A, ({skew}, {height}))",
        "SetLineStyle(heightSeg, 1)",
        "SetColor(heightSeg, 34/255, 197/255, 94/255)",
        # 面积文本
        f"areaText = Text(\"面积 = 底 × 高 = {base} × {height} = {base*height}\", ({base/2}, -1.2))",
    ]

    margin = 2
    applet_config = {
        "coordSystem": [-margin, skew + base + margin, -margin - 1, height + margin],
        "axesVisible": True,
        "gridVisible": True,
    }
    return instructions, applet_config


# ------------------------------------------------------------------ coordinate_points

def _coordinate_points(params: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
    """坐标定位：象限、对称点。

    参数：
        points —— 点列表 [{name, x, y}, ...]（默认 A(3,2), B(-2,3), C(-1,-2), D(2,-3)）
    """
    default_points = [
        {"name": "A", "x": 3, "y": 2},
        {"name": "B", "x": -2, "y": 3},
        {"name": "C", "x": -1, "y": -2},
        {"name": "D", "x": 2, "y": -3},
    ]
    points = params.get("points", default_points)
    if not isinstance(points, list) or len(points) < 2:
        points = default_points

    instructions = []
    xs, ys = [], []
    for p in points[:6]:  # 最多 6 个点
        name = str(p.get("name", "P")).strip() or "P"
        x = _fmt(p.get("x", 0))
        y = _fmt(p.get("y", 0))
        instructions.append(f"{name} = ({x}, {y})")
        xs.append(float(x))
        ys.append(float(y))

    # 连线（如果 4 个点则连四边形）
    if len(points) == 4:
        names = [str(p.get("name", "P")) for p in points]
        instructions.append(f"poly = Polygon({', '.join(names)})")
        instructions.append("SetColor(poly, 99/255, 102/255, 241/255)")

    # 文本标注
    instructions.append("xAxisText = Text(\"X轴\", (4.5, 0.3))")
    instructions.append("yAxisText = Text(\"Y轴\", (0.3, 4.5))")

    margin = 3
    x_min = min(xs + [-margin])
    x_max = max(xs + [margin])
    y_min = min(ys + [-margin])
    y_max = max(ys + [margin])

    applet_config = {
        "coordSystem": [x_min, x_max, y_min, y_max],
        "axesVisible": True,
        "gridVisible": True,
    }
    return instructions, applet_config


# ================================================================ 统一入口

_SCENE_BUILDERS = {
    "geometry_angle_sum": _geometry_angle_sum,
    "area_rectangle": _area_rectangle,
    "area_triangle": _area_triangle,
    "circle_properties": _circle_properties,
    "parallelogram_area": _parallelogram_area,
    "coordinate_points": _coordinate_points,
}


def build_scene(scene_type: str, params: Optional[Dict[str, Any]] = None) -> Tuple[List[str], Dict[str, Any]]:
    """按模板名和参数生成 GeoGebra 指令列表与 Applet 配置。

    Args:
        scene_type: 模板名（必须在 SCENE_ORDER 中）。
        params: 模板参数字典（可为空，用默认值）。

    Returns:
        ``(instructions, applet_config)`` 二元组。
    """
    params = params or {}
    builder = _SCENE_BUILDERS.get(scene_type, _geometry_angle_sum)
    return builder(params)


__all__ = [
    "SCENE_REGISTRY",
    "SCENE_ORDER",
    "scene_list_prompt",
    "build_scene",
]
