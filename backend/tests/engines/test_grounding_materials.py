# -*- coding: utf-8 -*-
"""接地气素材包引擎 · golden 真值守卫（A-1 期）。

背景与目的
----------
``app/engines/grounding_materials.py`` 把「一道题 + 一个知识点」加工成四段
可直接塞进 tutor system 提示的素材包：

    GroundingMaterials
    ├── peer_misconception  # 同伴视角句 —— A-1 期恒空（语料库属 A-2）
    ├── analogy             # 生活类比句 —— 有内容
    ├── kp_anchor           # 知识点释义 —— 有内容
    └── personal_error      # 个体错因 —— A-1 期恒空（无个体数据）

本文件是**手算真值**守卫：期望值全部按「人工推演应该得到什么」写死，
**不做对称互比**（不拿实现去比实现，避免两边一起错却互相印证）。

覆盖的硬约束
------------
1. **四段各自可测**（每段的取值口径独立断言）；
2. **缺素材不编造** —— 拿不到就留空串，**不写占位语**；
3. **渲染文本不含原 content 中的任何数字串** ← 防泄题关键断言；
4. **单句 ≤ 40 字**（《规格》红线⑥）；
5. **未知易错点标签走安全兜底**（未知锚点 → 空串，不套通用模板）；
6. **渲染文本 ≤ 1500 字符**（MAX_MATERIAL_CHARS）；
7. 6 条硬红线逐条可测；
8. 年级窗口（年级过滤的硬上限住在这里）。

纯函数层铁律
------------
``app/engines/`` 是纯函数层：**禁 import sqlalchemy / Session / FastAPI、
禁读环境变量**。本文件顺带把这条铁律做成断言 —— 将来谁往引擎里塞了
ORM import，本文件立刻红。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.engines.grounding_materials import (
    ANALOGY_TEMPLATES,
    CN_NUMERAL_CHARS,
    DIGIT_CHARS,
    INTERNAL_HINT,
    MAX_ANCHOR_CHARS,
    MAX_MATERIAL_CHARS,
    MAX_SEGMENT_CHARS,
    MAX_SENTENCE_CHARS,
    PRIMARY_MAX_GRADE,
    RED_LINE_CHECKERS,
    SCENE_SKELETONS,
    GroundingMaterials,
    build_analogy,
    build_grounding_materials,
    build_kp_anchor,
    build_peer_misconception,
    build_personal_error,
    extract_number_tokens,
    extract_scene_anchor,
    find_red_line_violations,
    grade_window,
    passes_red_lines,
    render_materials_for_prompt,
    strip_numbers,
)

# =============================================================================
# 素材源样本（模拟真实题面 / 真实知识点释义）
# =============================================================================

#: 真实题面样本（含数字、含姓名、含推广尾巴 —— 泄题防线的主要考验对象）。
_SAMPLE_CONTENTS = (
    "小明在文具店买了 3 支铅笔，一共花了 5 元。",
    "老师把 12 个苹果平均分给 4 个小朋友，每人分几个？",
    "妈妈买了 3 千克苹果，一共 12 元。",
    "一根绳子长 5 米，剪去 2 米，还剩多少米？",
    "小树每天长高 3 厘米，10 天后有多高？",
    "王芳在超市买了 4 盒牛奶。",
    "把一个蛋糕分给 5 个小朋友，每人分多少？",
    "在括号里填上 >、< 或 =。",
    "二（1）班的小明带了 25 元，买笔记本用了 8 元。",
)

#: 真实知识点释义样本。
_SAMPLE_DESCRIPTIONS = (
    "四则运算、口算笔算、简便运算与运算定律",
    "整除特征、奇数偶数与整除性质",
    "秒的认识、时分秒进率及简单时间读写",
    "三角形内角和是180度，多边形的内角和是360度",
    "两位数乘除法",
)

#: 数字字符集合（阿拉伯 + 中文）。断言「素材里一个数字都不许有」。
_ANY_DIGIT = re.compile(f"[{re.escape(DIGIT_CHARS + CN_NUMERAL_CHARS)}]")


def _rendered_segment(materials: GroundingMaterials, label: str) -> str:
    """从渲染文本里取某一段（测试断言用，避免重复解析）。"""
    for line in render_materials_for_prompt(materials).split("\n"):
        if line.startswith(f"【{label}】"):
            return line[len(f"【{label}】") :]
    return ""


# ============================================================
# 0. 纯函数层铁律（engines 不得碰 ORM / env）
# ============================================================


def test_engines_layer_has_no_orm_or_env_import():
    """engines 两个文件里不得出现 sqlalchemy / Session / FastAPI / os.environ。

    这是层次铁律的机器化守卫：服务层（唯一允许碰 ORM 的地方）一旦被
    「顺手下沉」到引擎层，本测试立刻红。
    """
    engine_dir = Path(__file__).resolve().parents[2] / "app" / "engines"
    targets = (
        engine_dir / "grounding_materials.py",
        engine_dir / "grounding_retrieval.py",
    )
    forbidden = (
        "sqlalchemy",
        "Session",
        "FastAPI",
        "os.environ",
        "getenv",
    )
    for target in targets:
        source = target.read_text(encoding="utf-8")
        for token in forbidden:
            # 只查真正的 import / 调用行（注释与 docstring 不算）
            for line in source.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("*"):
                    continue
                if token not in stripped:
                    continue
                assert not (
                    stripped.startswith("import ")
                    or stripped.startswith("from ")
                    or "os.environ" in stripped
                    or "getenv(" in stripped
                ), f"{target.name} 出现禁止的依赖: {line.strip()}"


# ============================================================
# 1. 四段各自可测
# ============================================================


def test_peer_misconception_is_always_empty_in_a1():
    """同伴视角句：A-1 期恒空（语料库零条，人工抽检属 A-2）。

    即使传入了语料句也必须返回空串 —— 因为**没有放行记录**，
    用了就是「未放行内容进线上讲解」。
    """
    assert build_peer_misconception() == ""
    assert build_peer_misconception("好多同学都会把余数算错") == ""
    assert build_peer_misconception("") == ""


def test_personal_error_is_always_empty_in_a1():
    """个体错因：A-1 期恒空（没有可靠的个体错因数据源）。"""
    assert build_personal_error() == ""
    assert build_personal_error("上次把加法当成减法了") == ""
    assert build_personal_error("") == ""


@pytest.mark.parametrize(
    "content,expected",
    [
        # —— 第一层：操作结构（分给 / 平均分 / 送给）——
        (
            "老师把 12 个苹果平均分给 4 个小朋友，每人分几个？",
            # 「分给 / 平均分」+ 出现具体食物 → 用更具体的食物骨架
            "分苹果给小朋友",
        ),
        (
            "把一个蛋糕分给 5 个小朋友，每人分多少？",
            "分蛋糕给同学",
        ),
        # —— 第二层：具体场所与道具 ——
        (
            "小明在文具店买了 3 支铅笔，一共花了 5 元。",
            "去文具店买文具",
        ),
        (
            "王芳在超市买了 4 盒牛奶。",
            "去超市挑东西",
        ),
        (
            "小树每天长高 3 厘米，10 天后有多高？",
            "看小树长高",
        ),
        # —— 第三层：交易动作 ——
        (
            "妈妈买了 3 千克苹果，一共 12 元。",
            "去商店挑东西再结账",
        ),
        (
            "一根绳子长 5 米，剪去 2 米，还剩多少米？",
            "看看手里还剩多少",
        ),
        # —— 第五层：人物 ——
        (
            "小红帮妈妈擦桌子，妈妈很高兴。",
            "帮妈妈做家务",
        ),
        # —— 认不出场景 → 空串（不编造）——
        (
            "在括号里填上 >、< 或 =。",
            "",
        ),
        # —— 推广尾巴砍掉后没有场景 → 空串 ——
        (
            "A/老师带你口算闯关，快扫描右侧二维码",
            "",
        ),
        # —— 空题面 → 空串 ——
        ("", ""),
    ],
)
def test_extract_scene_anchor_golden(content, expected):
    """情境骨架：逐条手算真值（分层优先级 + 骨架映射表）。"""
    assert extract_scene_anchor(content) == expected


def test_extract_scene_anchor_never_exceeds_anchor_limit():
    """情境骨架长度 ≤ MAX_ANCHOR_CHARS。"""
    for content in _SAMPLE_CONTENTS:
        anchor = extract_scene_anchor(content)
        assert len(anchor) <= MAX_ANCHOR_CHARS, (content, anchor)


def test_extract_scene_anchor_strips_promo_tail():
    """推广尾巴（「A/老师带你口算闯关…」）要被砍掉，且不影响场景识别。"""
    with_tail = "小明在文具店买了 3 支铅笔。A/老师带你口算闯关，快扫描右侧二维码"
    assert extract_scene_anchor(with_tail) == "去文具店买文具"


@pytest.mark.parametrize(
    "anchor,expected",
    [
        (
            "去文具店买文具",
            "就像去文具店买文具那样，先想清楚要什么，再看还剩多少。",
        ),
        (
            "把东西平均分给同学",
            "就像把东西平均分给同学那样，先算清要分给几个人。",
        ),
        (
            "看小树长高",
            "就像看小树长高那样，得慢慢等它长。",
        ),
        (
            "看看手里还剩多少",
            "就像看看手里还剩多少那样，先看原来有多少再减掉用掉的。",
        ),
    ],
)
def test_build_analogy_golden(anchor, expected):
    """生活类比句：逐条手算真值（骨架 → 类别 → 模板）。"""
    assert build_analogy(anchor) == expected


def test_build_analogy_unknown_anchor_returns_empty():
    """未知标签走**安全兜底**：认不出的锚点返回空串，不套通用模板。

    套通用模板 = 把任意文本塞进提示词 = 编造，所以必须返回空串。
    """
    assert build_analogy("") == ""
    assert build_analogy(None) == ""
    assert build_analogy("完全不认识的骨架") == ""
    assert build_analogy("12345") == ""


def test_build_analogy_long_sentence_returns_empty():
    """类比句超过 MAX_SENTENCE_CHARS → 返回空串（宁缺毋滥）。"""
    long_anchor = "买" * (MAX_SENTENCE_CHARS + 1)
    assert build_analogy(long_anchor) == ""


@pytest.mark.parametrize(
    "description,expected",
    [
        (
            "四则运算、口算笔算、简便运算与运算定律",
            "四则运算、口算笔算、简便运算与运算定律",
        ),
        (
            "整除特征、奇数偶数与整除性质",
            "整除特征、奇数偶数与整除性质",
        ),
        (
            "秒的认识、时分秒进率及简单时间读写",
            "秒的认识、时分秒进率及简单时间读写",
        ),
        # 阿拉伯数字 + 单位整段剥掉（180度 可能是本题答案！）
        (
            "三角形内角和是180度，多边形的内角和是360度",
            "三角形内角和、多边形的内角和",
        ),
        # 中文数字是词的一部分 → 保留（「两位数」不能变成「位数」）
        (
            "两位数乘除法",
            "两位数乘除法",
        ),
        # 空描述 → 空串（不编造）
        ("", ""),
        (None, ""),
        # 剥完没东西 → 空串
        ("180度", ""),
    ],
)
def test_build_kp_anchor_golden(description, expected):
    """知识点释义：逐条手算真值（剥数字 → 切子句 → 丢虚词 → 拼装）。"""
    assert build_kp_anchor(description) == expected


def test_build_kp_anchor_keeps_trailing_he():
    """结尾的「和」是实词的一部分，**不能**被当成虚词剥掉。

    「三角形内角和」剥掉「和」就变成「三角形内角」—— 语义毁了。
    """
    assert build_kp_anchor("三角形内角和是180度") == "三角形内角和"
    assert build_kp_anchor("多边形的内角和") == "多边形的内角和"


def test_build_kp_anchor_respects_segment_limit():
    """单段素材长度 ≤ MAX_SEGMENT_CHARS。"""
    long_description = "、".join(f"知识点{i}描述" for i in range(40))
    assert len(build_kp_anchor(long_description)) <= MAX_SEGMENT_CHARS


def test_build_grounding_materials_fills_each_section():
    """四段各自可测：一次组装，四段的取值各自独立且正确。"""
    materials = build_grounding_materials(
        content="小明在文具店买了 3 支铅笔，一共花了 5 元。",
        kp_description="四则运算、口算笔算、简便运算与运算定律",
    )
    assert materials.analogy == "就像去文具店买文具那样，先想清楚要什么，再看还剩多少。"
    assert materials.kp_anchor == "四则运算、口算笔算、简便运算与运算定律"
    # A-1 期没有语料与个体数据 → 两段恒空
    assert materials.peer_misconception == ""
    assert materials.personal_error == ""
    assert not materials.is_empty()
    assert materials.sections() == [
        ("生活类比", materials.analogy),
        ("知识点", materials.kp_anchor),
    ]


def test_sections_order_is_stable():
    """段落顺序固定：同伴提醒 → 生活类比 → 知识点 → 个人错因。"""
    materials = GroundingMaterials(
        peer_misconception="P",
        analogy="A",
        kp_anchor="K",
        personal_error="E",
    )
    assert [label for label, _ in materials.sections()] == [
        "同伴提醒",
        "生活类比",
        "知识点",
        "个人错因",
    ]


# ============================================================
# 2. 缺素材不编造
# ============================================================


def test_empty_package_is_empty():
    """四段全空 → is_empty() 为真。"""
    assert GroundingMaterials().is_empty()
    assert GroundingMaterials(analogy=" ").is_empty()


def test_render_empty_package_returns_empty_string():
    """四段全空 → 渲染成空串（调用方据此完全不追加后缀）。"""
    assert render_materials_for_prompt(GroundingMaterials()) == ""
    assert render_materials_for_prompt(None) == ""


def test_missing_material_leaves_section_empty_not_placeholder():
    """缺素材的那一段必须是**空串**，不许出现占位语。

    占位语（「暂无相关素材」「你可以参考…」）是 PRD 明确要防的编造风险。
    """
    materials = build_grounding_materials(
        content="在括号里填上 >、< 或 =。",  # 认不出场景 → analogy 空
        kp_description="",  # 无释义 → kp_anchor 空
    )
    assert materials.analogy == ""
    assert materials.kp_anchor == ""

    rendered = render_materials_for_prompt(materials)
    assert rendered == ""  # 四段全空 → 整段不注入

    # 有一段有内容时，空段**整段不出现**（不是出现成占位语）
    partial = build_grounding_materials(
        content="小明在文具店买了 3 支铅笔。",
        kp_description="",
    )
    rendered_partial = render_materials_for_prompt(partial)
    assert "【生活类比】" in rendered_partial
    assert "【知识点】" not in rendered_partial
    # 占位语黑名单：这些是「拿不到素材却硬写一句」的典型形态
    for placeholder in ("暂无", "无素材", "待补充", "暂无相关", "没有找到", "缺少"):
        assert placeholder not in rendered_partial


def test_render_skips_red_line_violating_section():
    """任一段没过 6 条硬红线 → **整段丢弃**（宁缺毋滥）。"""
    materials = GroundingMaterials(
        analogy="就像去文具店买文具那样，先想清楚要什么。",
        kp_anchor="小明同学在三年级二班偷看答案",  # 真实姓名 + 班级 → 红线
    )
    rendered = render_materials_for_prompt(materials)
    assert "【生活类比】" in rendered
    assert "【知识点】" not in rendered
    assert "二班" not in rendered


# ============================================================
# 3. 渲染文本不含原 content 中的任何数字串（防泄题关键断言）
# ============================================================


@pytest.mark.parametrize("content", _SAMPLE_CONTENTS)
def test_rendered_material_contains_no_digit_from_source(content):
    """渲染后的素材文本**不含原 content 中的任何数字串**。

    断言写法（两层）：
    1. **逐 token 比对**：把原 content 里的数字串（阿拉伯数字串 +
       中文数量词）全部抽出来，逐个断言不出现在渲染文本里；
    2. **整体扫描**：断言渲染文本里连一个阿拉伯数字都没有
       （知识点释义里也可能带数字，释义同样不许漏）。
    """
    materials = build_grounding_materials(
        content=content,
        kp_description="三角形内角和是180度，多边形的内角和是360度",
    )
    rendered = render_materials_for_prompt(materials)

    source_tokens = extract_number_tokens(content)
    for token in source_tokens:
        assert token not in rendered, f"素材泄露了原题数字 {token!r}: {rendered}"

    assert not re.search(f"[{re.escape(DIGIT_CHARS)}]", rendered), rendered


@pytest.mark.parametrize("content", _SAMPLE_CONTENTS)
def test_analogy_section_contains_no_digit_at_all(content):
    """类比段（直接来自题面场景）**零数字** —— 阿拉伯与中文都不许有。

    类比段是唯一直接由 content 派生的段落，所以它的数字约束最严：
    连「一」「两」都不许出现。

    认不出场景的题面（analogy 为空串）跳过 —— 那是「缺素材不编造」，
    由 :func:`test_missing_material_leaves_section_empty_not_placeholder` 守。
    """
    analogy = build_analogy(extract_scene_anchor(content))
    if not analogy:
        pytest.skip("该题面认不出生活场景，analogy 为空串")
    assert not _ANY_DIGIT.search(analogy), analogy


def test_all_skeletons_and_templates_are_digit_free():
    """整张骨架表与模板表**逐条**零数字。

    这条断言是「结构上不可能泄题」的机器化证明：素材里能出现的字，
    全部来自这两张常量表，表里没有数字，素材里就不可能有数字。
    """
    for word, (_tier, _category, skeleton) in SCENE_SKELETONS.items():
        assert not _ANY_DIGIT.search(skeleton), (word, skeleton)
    for category, template in ANALOGY_TEMPLATES.items():
        assert not _ANY_DIGIT.search(template), (category, template)


def test_strip_numbers_removes_arabic_and_cn_quantities():
    """数字剥离：阿拉伯数字 + 单位、中文数量词都要剥干净。"""
    assert strip_numbers("小明有 5 个苹果和 12 根香蕉") == "小明有苹果和香蕉"
    assert strip_numbers("三角形内角和是 180 度") == "三角形内角和是"
    assert strip_numbers("") == ""
    assert strip_numbers(None) == ""


def test_extract_number_tokens_distinguishes_words_from_quantities():
    """数字 token 抽取：词内数字（三角形/两位数）**不算**数量。"""
    assert extract_number_tokens("三角形内角和") == []
    assert extract_number_tokens("两位数乘除法") == []
    assert extract_number_tokens("12 个苹果") == ["12"]
    assert extract_number_tokens("三个苹果") == ["三个"]
    assert extract_number_tokens("") == []


# ============================================================
# 4. 单句 ≤ 40 字（《规格》红线⑥）
# ============================================================


@pytest.mark.parametrize("content", _SAMPLE_CONTENTS)
def test_rendered_material_sentences_within_limit(content):
    """渲染文本里每一句都 ≤ MAX_SENTENCE_CHARS 字。"""
    materials = build_grounding_materials(
        content=content,
        kp_description="四则运算、口算笔算、简便运算与运算定律",
    )
    rendered = render_materials_for_prompt(materials)
    for sentence in re.split(r"[。；;！!？?]", rendered):
        if sentence.strip():
            assert len(sentence.strip()) <= MAX_SENTENCE_CHARS, sentence


def test_rendered_material_within_char_budget():
    """渲染文本 ≤ MAX_MATERIAL_CHARS（1500 字符）。"""
    materials = GroundingMaterials(
        analogy="就像去文具店买文具那样，先想清楚要什么，再看还剩多少。",
        kp_anchor="四则运算、口算笔算、简便运算与运算定律",
    )
    rendered = render_materials_for_prompt(materials)
    assert len(rendered) <= MAX_MATERIAL_CHARS


def test_render_truncates_overlong_material():
    """超长素材按行截断，且截断后仍 ≤ MAX_MATERIAL_CHARS。"""
    overlong = GroundingMaterials(
        analogy="就像去文具店买文具那样，先想清楚要什么，再看还剩多少。" * 40,
    )
    rendered = render_materials_for_prompt(overlong)
    assert len(rendered) <= MAX_MATERIAL_CHARS


# ============================================================
# 5. 6 条硬红线逐条可测
# ============================================================


def test_red_line_checkers_cover_six_rules():
    """6 条红线齐套（顺序与《规格》一致）。"""
    assert len(RED_LINE_CHECKERS) == 6
    assert [name for name, _ in RED_LINE_CHECKERS] == [
        "禁真实姓名/班级/校名",
        "禁把错误指向「你」",
        "禁 LaTeX",
        "禁初中高中内容",
        "禁评价性贬义词",
        "单句≤40字/不连用感叹号/不自称",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "张伟同学在三年级二班买了 3 支铅笔",  # 真实姓名 + 班级
        "实验小学的同学们在植树",  # 校名
        "你又做错了，这次要细心",  # 把错误指向「你」
        "解法是 $x^2 + y^2$ 的展开",  # LaTeX
        "用方程组解这道二次方程",  # 初中内容
        "你太笨了，这题这么简单",  # 贬义词
        "作为老师我告诉你正确答案",  # 自称老师
        "太棒了！！真厉害！！",  # 连用感叹号
    ],
)
def test_red_line_violations_detected(text):
    """红线文本必须被检出（不能漏）。"""
    violations = find_red_line_violations(text)
    assert violations, text
    assert not passes_red_lines(text)


@pytest.mark.parametrize(
    "text",
    [
        "就像去文具店买文具那样，先想清楚要什么，再看还剩多少。",
        "四则运算、口算笔算、简便运算与运算定律",
        "整除特征、奇数偶数与整除性质",
        "小明在文具店买了 3 支铅笔",  # 「小明」是虚构角色名 → 不算真实姓名
        "全班同学一起打扫教室",
    ],
)
def test_clean_text_passes_red_lines(text):
    """干净文本必须放行（不能误杀）。"""
    assert find_red_line_violations(text) == [], text
    assert passes_red_lines(text)


# ============================================================
# 6. 年级窗口（年级过滤的硬上限）
# ============================================================


@pytest.mark.parametrize(
    "student_grade,width,expected",
    [
        (4, 1, (3, 4, 5)),
        (4, 0, (4,)),
        (1, 1, (1, 2)),
        (6, 1, (5, 6)),
        # 窗口再宽也**绝不越过 6** —— 小学硬上限
        (4, 10, (1, 2, 3, 4, 5, 6)),
        (6, 10, (1, 2, 3, 4, 5, 6)),
        # 年级未知 → 全段 1~6（仍不越 6）
        (None, 1, (1, 2, 3, 4, 5, 6)),
    ],
)
def test_grade_window_golden(student_grade, width, expected):
    """年级窗口：逐条手算真值。"""
    assert grade_window(student_grade, width=width) == expected


def test_grade_window_never_exceeds_primary_max_grade():
    """任何年级组合下窗口都不得越过 PRIMARY_MAX_GRADE（=6）。"""
    for student_grade in range(1, 13):
        for width in range(0, 6):
            window = grade_window(student_grade, width=width)
            assert all(grade <= PRIMARY_MAX_GRADE for grade in window), (
                student_grade,
                width,
                window,
            )


# ============================================================
# 7. 渲染文本形态
# ============================================================


def test_render_includes_internal_hint():
    """渲染文本必须带「（内部提示，不要念给孩子听）」标注。"""
    materials = build_grounding_materials(
        content="小明在文具店买了 3 支铅笔。",
        kp_description="四则运算",
    )
    rendered = render_materials_for_prompt(materials)
    assert INTERNAL_HINT in rendered


def test_render_uses_section_labels():
    """渲染文本用【段标签】前缀，标签集合固定。"""
    materials = build_grounding_materials(
        content="小明在文具店买了 3 支铅笔。",
        kp_description="四则运算",
    )
    rendered = render_materials_for_prompt(materials)
    assert "【生活类比】" in rendered
    assert "【知识点】" in rendered
    # A-1 期这两段恒空 → 标签不得出现
    assert "【同伴提醒】" not in rendered
    assert "【个人错因】" not in rendered
