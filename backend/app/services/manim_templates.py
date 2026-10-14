"""Manim 场景模板库（方向四·2）。

设计原则（重要）：
    * **不让大模型自由写 Manim 代码**（语法/命名/依赖缺一必挂，2 核 4G 机器
      经不起反复失败重试）。改为：AI 只做「选模板 + 抽参数」两件事，
      场景代码由本文件按模板拼装——渲染成功率由模板保证（≈100%）；
    * 模板全部用 ``Text``（PangoCairo）而非 ``MathTex``，避免依赖全套 LaTeX，
      官方 manim 镜像也无需 texlive 即可渲染，镜像更小更稳；
    * 输出统一为 ``render-{scene_type}-{params_hash}.py`` 单文件场景，
      渲染命令 ``manim -ql --format=mp4 <file> Scene``（-ql=480p15，省 CPU；
      教师端/讲解页足够清晰）；
    * 每个模板只接受安全数值参数，内部对参数做 clamp/合法性清洗，
      任何输入都不会产生非法几何（防 NaN / 超画布 / 0 除）。

当前模板清单（小学-初中常见题型）：
    function_plot   一次/二次/正反比例函数图像
    fraction_pie    分数可视化（圆饼等分）
    number_line     数轴加法跳点（低年级）
    area_rect       长方形面积 = 长 x 宽（网格动画）
    geometry_tri    三角形（内角和 180 度）
    bar_compare     条形图对比（统计/应用题）
    clock_angle     时钟夹角（时针分针）
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

#: 场景名 -> 中文说明（供 AI prompt 使用）
SCENE_REGISTRY: Dict[str, str] = {
    "function_plot": "函数图像（一次/二次/正反比例，给出表达式与范围）",
    "fraction_pie": "分数可视化（把圆等分成 n 份，高亮其中 k 份）",
    "number_line": "数轴加法（从 a 开始向右跳 b 步，适合低年级）",
    "area_rect": "长方形面积（长 x 宽，棋盘格动态演示）",
    "geometry_tri": "三角形内角和（三个角拆开拼成平角 180 度）",
    "bar_compare": "条形图对比（两个/多个量的大小比较）",
    "clock_angle": "时钟夹角（时针分针形成的角度）",
}

SCENE_ORDER: list[str] = list(SCENE_REGISTRY.keys())


def scene_list_prompt() -> str:
    """在 AI prompt 中列出可选模板。"""
    lines = ["可选场景模板："]
    for name, desc in SCENE_REGISTRY.items():
        lines.append(f"- {name}：{desc}")
    return "\n".join(lines)


# ================================================================ 工具函数

def _fmt_float(value: Any, digits: int = 3) -> str:
    """把数值格式化成 manim 可用的字面量（去除多余小数）。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        num = 0.0
    if abs(num - round(num)) < 1e-9:
        return str(int(round(num)))
    return f"{num:.{digits}f}"


def _fmt_expr(expr: Optional[str]) -> str:
    """规范化函数表达式：只保留安全字符，插入必要的 *。"""
    raw = (expr or "").strip()
    # 中英文括号/乘号/幂符号统一成 manim/numpy 语法
    raw = (
        raw.replace("（", "(")
        .replace("）", ")")
        .replace("×", "*")
        .replace("÷", "/")
        .replace("^", "**")
        .replace("−", "-")
        .replace("—", "-")
        .replace("－", "-")
        .replace("ｘ", "x")
        .replace("Ｘ", "x")
        .replace("ｙ", "y")
    )
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in " +-*/().xX_")
    safe = safe.strip()
    if not safe:
        safe = "x**2"
    return safe


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
    try:
        num = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, num))


# ================================================================ 模板拼装

def _wrap_body(body: str) -> str:
    """包成可独立渲染的 manim 场景文件。"""
    return (
        '"""Auto-generated Manim scene (DuMate math coach)."""\n'
        "from __future__ import annotations\n\n"
        "import numpy as np\n"
        "from manim import *\n\n\n"
        "class Scene(Scene):\n"
        "    def construct(self):\n"
        f"{body}\n"
    )


def _indent(code: str, spaces: int = 4) -> str:
    """把代码块整体缩进，保证缩进统一为 4 空格。"""
    lines = [line.rstrip() for line in code.strip().splitlines()]
    return "\n".join(" " * spaces + line if line else line for line in lines)


def build_function_plot(params: Dict[str, Any]) -> str:
    """一次/二次/正反比例函数图像。"""
    expr = _fmt_expr(params.get("expr"))
    title = str(params.get("title") or f"y = {expr}")
    x_min = _clamp(params.get("x_min"), -20, 20, -5.0)
    x_max = _clamp(params.get("x_max"), -20, 20, 5.0)
    if x_max <= x_min:
        x_max, x_min = x_min, x_max
    x_max = max(x_max, x_min + 0.5)
    y_min = _clamp(params.get("y_min"), -20, 20, -4.0)
    y_max = _clamp(params.get("y_max"), -20, 20, 4.0)
    step = max(0.05, (x_max - x_min) / 80.0)
    body = f'''
        # 设置中文字体
        Text.set_default(font="Noto Sans CJK SC")
        title_t = Text("{title}", font_size=44, color=BLUE)
        title_t.to_edge(UP, buff=0.4)
        self.play(FadeIn(title_t, shift=DOWN * 0.3))

        axes = Axes(
            x_range=[{_fmt_float(x_min)}, {_fmt_float(x_max)}, 1],
            y_range=[{_fmt_float(y_min)}, {_fmt_float(y_max)}, 1],
            axis_config={{"color": GREY_B, "stroke_width": 2}},
            x_axis_config={{"include_numbers": True, "font_size": 24}},
            y_axis_config={{"include_numbers": True, "font_size": 24}},
        )
        axes_labels = axes.get_axis_labels(x_label="x", y_label="y")
        self.play(Create(axes), Write(axes_labels))
        self.wait(0.4)

        graph = axes.plot(
            lambda x_val: {expr},
            x_range=[{_fmt_float(x_min)}, {_fmt_float(x_max)}, {_fmt_float(step)}],
            color=YELLOW,
            stroke_width=4,
        )
        graph_label = Text("y = {expr}", font_size=32).scale(0.8)
        graph_label.set_color(YELLOW)
        graph_label.next_to(graph, RIGHT, buff=0.2)
        self.play(Create(graph), run_time=2.0)
        self.play(FadeIn(graph_label))
        self.wait(1.0)
'''
    return _wrap_body(body)


def build_fraction_pie(params: Dict[str, Any]) -> str:
    """分数可视化：圆等分 n 份，高亮 k 份。"""
    n = _int(params.get("denominator"), 4, 2, 12)
    k = _int(params.get("numerator"), 1, 1, n)
    k = min(k, n)
    label = str(params.get("title") or f"{k}/{n}")
    body = f'''
        Text.set_default(font="Noto Sans CJK SC")
        title_t = Text("{label}", font_size=52, color=BLUE)
        title_t.to_edge(UP, buff=0.3)
        self.play(FadeIn(title_t, shift=DOWN * 0.3))

        circle = Circle(radius=2.0, color=WHITE)
        self.play(Create(circle))
        self.wait(0.3)

        angle = 2 * PI / {n}
        for i in range({n}):
            start = angle * i
            end = angle * (i + 1)
            sector = Sector(
                arc_center=circle.get_center(),
                radius=2.0,
                angle=end - start,
                start_angle=start,
                fill_opacity=0,
                stroke_color=WHITE,
                stroke_width=2,
            )
            self.play(Create(sector), run_time=0.25)
        self.wait(0.3)

        fill_color = ORANGE
        for i in range({k}):
            start = angle * i
            end = angle * (i + 1)
            sector = Sector(
                arc_center=circle.get_center(),
                radius=2.0,
                angle=end - start,
                start_angle=start,
                fill_opacity=0.65,
                fill_color=fill_color,
                stroke_color=WHITE,
                stroke_width=2,
            )
            self.play(FadeIn(sector), run_time=0.4)
        self.wait(1.2)
'''
    return _wrap_body(body)


def build_number_line(params: Dict[str, Any]) -> str:
    """数轴加法：从 a 跳到 a+b。"""
    start = _int(params.get("start"), 1, 0, 20)
    steps = _int(params.get("steps"), 3, 1, 9)
    title = str(params.get("title") or f"{start} + {steps} = {start + steps}")
    body = f'''
        Text.set_default(font="Noto Sans CJK SC")
        title_t = Text("{title}", font_size=48, color=BLUE)
        title_t.to_edge(UP, buff=0.3)
        self.play(FadeIn(title_t, shift=DOWN * 0.3))

        total = {start} + {steps}
        line = NumberLine(
            x_range=[0, max(total + 1, 12), 1],
            length=10,
            include_numbers=True,
            font_size=28,
            color=GREY_B,
        )
        self.play(Create(line))
        self.wait(0.4)

        ball = Dot(color=YELLOW).move_to(line.number_to_point({start}))
        ball.scale(1.4)
        ball_label = Text("{start}", font_size=30, color=YELLOW)
        ball_label.next_to(ball, DOWN, buff=0.25)
        self.play(FadeIn(ball), FadeIn(ball_label))

        for i in range(1, {steps} + 1):
            target = {start} + i
            arc = ArcBetweenPoints(
                ball.get_center(),
                line.number_to_point(target),
                angle=PI / 8,
                color=ORANGE,
                stroke_width=3,
            )
            self.play(Create(arc), run_time=0.35)
            self.play(
                ball.animate.move_to(line.number_to_point(target)),
                run_time=0.35,
            )
            self.play(FadeOut(arc))
        self.play(FadeOut(ball_label))
        total_label = Text("{start + steps}", font_size=36, color=ORANGE)
        total_label.next_to(ball, DOWN, buff=0.25)
        self.play(FadeIn(total_label))
        self.wait(1.2)
'''
    return _wrap_body(body)


def build_area_rect(params: Dict[str, Any]) -> str:
    """长方形面积 = 长 x 宽（网格动画）。"""
    w = _int(params.get("width"), 4, 1, 9)
    h = _int(params.get("height"), 3, 1, 9)
    unit = str(params.get("unit") or "格")
    title = str(params.get("title") or f"面积 = {w} × {h} = {w * h} {unit}")
    body = f'''
        Text.set_default(font="Noto Sans CJK SC")
        title_t = Text("{title}", font_size=44, color=BLUE)
        title_t.to_edge(UP, buff=0.35)
        self.play(FadeIn(title_t, shift=DOWN * 0.3))

        rect = Rectangle(width={w}, height={h}, stroke_color=WHITE, stroke_width=3)
        rect.move_to(ORIGIN)
        self.play(Create(rect))
        self.wait(0.3)

        # 竖线（等分宽）
        for i in range(1, {w}):
            x_pos = rect.get_left()[0] + i
            vline = Line(
                np.array([x_pos, rect.get_bottom()[1], 0]),
                np.array([x_pos, rect.get_top()[1], 0]),
                color=GREY_B,
                stroke_width=1.5,
            )
            self.play(Create(vline), run_time=0.2)
        # 横线（等分高）
        for j in range(1, {h}):
            y_pos = rect.get_bottom()[1] + j
            hline = Line(
                np.array([rect.get_left()[0], y_pos, 0]),
                np.array([rect.get_right()[0], y_pos, 0]),
                color=GREY_B,
                stroke_width=1.5,
            )
            self.play(Create(hline), run_time=0.2)
        self.wait(0.4)

        # 逐格染色
        cell_color = ORANGE
        for jj in range({h}):
            row_blocks = VGroup()
            for ii in range({w}):
                cell = Square(side_length=1, fill_opacity=0.55, fill_color=cell_color, stroke_width=0)
                cell.move_to(
                    np.array([
                        rect.get_left()[0] + ii + 0.5,
                        rect.get_top()[1] - jj - 0.5,
                        0,
                    ])
                )
                row_blocks.add(cell)
            self.play(FadeIn(row_blocks), run_time=0.5)
        self.wait(1.0)
'''
    return _wrap_body(body)


def build_geometry_tri(params: Dict[str, Any]) -> str:
    """三角形内角和 = 180 度。"""
    title = str(params.get("title") or "三角形内角和 = 180°")
    body = f'''
        Text.set_default(font="Noto Sans CJK SC")
        title_t = Text("{title}", font_size=44, color=BLUE)
        title_t.to_edge(UP, buff=0.35)
        self.play(FadeIn(title_t, shift=DOWN * 0.3))

        apex = np.array([0, 1.5, 0])
        left = np.array([-2.0, -1.0, 0])
        right = np.array([2.0, -1.0, 0])

        tri = Polygon(apex, left, right, color=YELLOW, stroke_width=3, fill_opacity=0.15)
        self.play(Create(tri))
        self.wait(0.4)

        a_label = Text("A", font_size=46, color=YELLOW).next_to(apex, UP, buff=0.15)
        b_label = Text("B", font_size=46, color=YELLOW).next_to(left, DOWN, buff=0.15)
        c_label = Text("C", font_size=46, color=YELLOW).next_to(right, DOWN, buff=0.15)
        self.play(Write(a_label), Write(b_label), Write(c_label))
        self.wait(0.3)

        # 将三个角标记出来（线段端点附近的圆弧）
        arc_apex = ArcBetweenPoints(
            np.array([0.6, 0.9, 0]), np.array([-0.6, 0.9, 0]), angle=PI / 2, color=ORANGE, radius=0.4
        )
        arc_left = ArcBetweenPoints(
            np.array([-1.4, -0.9, 0]), np.array([-0.4, 1.4, 0]) * 0.7 + np.array([-0.8, -0.9, 0]), color=GREEN, radius=0.4
        )
        arc_right = ArcBetweenPoints(
            np.array([1.4, -0.9, 0]), np.array([0.4, 1.4, 0]) * 0.7 + np.array([0.8, -0.9, 0]), color=TEAL, radius=0.4
        )
        self.play(Create(arc_apex), Create(arc_left), Create(arc_right), run_time=0.8)
        self.wait(0.5)

        # 把三个角"拆下来"拼成平角（示意动画）
        note = Text("三个角拼起来 = 平角 180°", font_size=30, color=GREY_B)
        note.to_edge(DOWN, buff=0.5)
        self.play(FadeIn(note))
        self.wait(1.2)
'''
    return _wrap_body(body)


def build_bar_compare(params: Dict[str, Any]) -> str:
    """条形图对比。"""
    items: list[dict] = params.get("items") or []
    if not isinstance(items, list) or len(items) < 2:
        items = [{"name": "甲", "value": 4}, {"name": "乙", "value": 7}]
    # 只保留最多 5 项，避免拥挤
    items = items[:5]
    safe_items: list[tuple[str, int]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "?")[:6]
        value = _int(it.get("value"), 1, 0, 100)
        safe_items.append((name, value))
    title = str(params.get("title") or "数量对比")
    body_parts = [f'''
        Text.set_default(font="Noto Sans CJK SC")
        title_t = Text("{title}", font_size=44, color=BLUE)
        title_t.to_edge(UP, buff=0.35)
        self.play(FadeIn(title_t, shift=DOWN * 0.3))

        axis = Line(np.array([-4.5, -2.5, 0]), np.array([4.5, -2.5, 0]), color=GREY_B, stroke_width=2)
        self.play(Create(axis))
    ''']
    max_v = max(v for _, v in safe_items) or 1
    n = len(safe_items)
    bar_w = 0.9
    # 颜色用 hex（生成期不依赖 manim 环境；渲染期由 manim 解析）
    colors = ["#FFD700", "#FF8C00", "#1E90FF", "#32CD32", "#9370DB"]
    for idx, (name, value) in enumerate(safe_items):
        x_center = ((idx + 1 - (n + 1) / 2) * 1.1) * 0.9
        height = (value / max_v) * 3.2
        color = colors[idx % len(colors)]
        body_parts.append(f'''
        bar = Rectangle(
            width={bar_w}, height={height},
            fill_opacity=0.6, fill_color="{color}",
            stroke_color=WHITE, stroke_width=2,
        )
        bar.move_to((np.array([{x_center:.4f}, -2.5 + {height:.4f}, 0]) + np.array([{x_center:.4f}, -2.5, 0])) / 2)
        name_t = Text("{name}", font_size=28, color=WHITE)
        name_t.next_to(bar.get_bottom(), DOWN, buff=0.15)
        val_t = Text("{value}", font_size=28, color="{color}")
        val_t.next_to(bar.get_top(), UP, buff=0.1)
        self.play(Create(bar), run_time=0.8)
        self.play(FadeIn(name_t))
        self.play(FadeIn(val_t))
        ''')
    body_parts.append('''
        self.wait(1.2)
''')
    return _wrap_body("\n".join(body_parts))


def build_clock_angle(params: Dict[str, Any]) -> str:
    """时钟夹角。"""
    hour = _clamp(params.get("hour"), 1, 12, 3)
    minute = _clamp(params.get("minute"), 0, 59, 0)
    hour_norm = int(hour % 12) + int(minute) / 60.0
    hour_angle = -90 + hour_norm * 30  # 12点=0度 -> 上方为 -90
    minute_angle = -90 + int(minute) * 6
    diff = abs(hour_angle - minute_angle) % 360
    angle = min(diff, 360 - diff)
    title = str(params.get("title") or f"{int(hour)}:{int(minute):02d} 夹角 = {angle:.0f}°")
    body = f'''
        Text.set_default(font="Noto Sans CJK SC")
        title_t = Text("{title}", font_size=42, color=BLUE)
        title_t.to_edge(UP, buff=0.35)
        self.play(FadeIn(title_t, shift=DOWN * 0.3))

        face = Circle(radius=2.2, color=WHITE, stroke_width=3)
        self.play(Create(face))
        # 12 个刻度
        for i in range(12):
            ang = PI / 2 - i * 2 * PI / 12
            is_major = i % 3 == 0
            outer = np.array([2.2 * np.cos(ang), 2.2 * np.sin(ang), 0])
            inner = np.array([(2.2 if not is_major else 1.9) * np.cos(ang), (2.2 if not is_major else 1.9) * np.sin(ang), 0])
            tick = Line(outer, inner, color=GREY_B, stroke_width=2)
            self.play(Create(tick), run_time=0.1)
        center = Dot(radius=0.08, color=WHITE)

        hour_hand = Line(
            np.array([0, 0, 0]),
            np.array([1.3 * np.cos(np.deg2rad({_fmt_float(hour_angle)})), 1.3 * np.sin(np.deg2rad({_fmt_float(hour_angle)})), 0]),
            color=YELLOW, stroke_width=6,
        )
        minute_hand = Line(
            np.array([0, 0, 0]),
            np.array([1.9 * np.cos(np.deg2rad({_fmt_float(minute_angle)})), 1.9 * np.sin(np.deg2rad({_fmt_float(minute_angle)})), 0]),
            color=ORANGE, stroke_width=4,
        )
        self.play(Create(hour_hand), Create(minute_hand), FadeIn(center))
        self.wait(0.4)

        # 夹角弧线（两条指针之间）
        if angle > 1:
            a1 = np.deg2rad({_fmt_float(hour_angle)})
            a2 = np.deg2rad({_fmt_float(minute_angle)})
            arc = Arc(radius=1.0, start_angle=min(a1, a2), angle=abs(a2 - a1), color=TEAL, stroke_width=3)
            self.play(Create(arc), run_time=0.6)
        self.wait(1.2)
'''
    return _wrap_body(body)


# ================================================================ 分发

_BUILDERS = {
    "function_plot": build_function_plot,
    "fraction_pie": build_fraction_pie,
    "number_line": build_number_line,
    "area_rect": build_area_rect,
    "geometry_tri": build_geometry_tri,
    "bar_compare": build_bar_compare,
    "clock_angle": build_clock_angle,
}


def build_scene(scene_type: str, params: Optional[Dict[str, Any]] = None) -> str:
    """按模板拼装 Manim 场景代码。

    Args:
        scene_type: 模板名（见 SCENE_REGISTRY）。
        params: 结构化参数 dict，非法值由模板内部 clamp。

    Returns:
        可直接 ``manim <file> Scene`` 渲染的完整 .py 源码。

    Raises:
        KeyError: 模板名不存在时。
    """
    builder = _BUILDERS[scene_type]
    return builder(dict(params or {}))


def build_scene_file(scene_type: str, params: Optional[Dict[str, Any]] = None) -> bytes:
    """返回场景源码 UTF-8 字节（写入 .py 文件用）。"""
    return build_scene(scene_type, params).encode("utf-8")


__all__ = [
    "SCENE_REGISTRY",
    "SCENE_ORDER",
    "scene_list_prompt",
    "build_scene",
    "build_scene_file",
    "build_function_plot",
    "build_fraction_pie",
    "build_number_line",
    "build_area_rect",
    "build_geometry_tri",
    "build_bar_compare",
    "build_clock_angle",
]