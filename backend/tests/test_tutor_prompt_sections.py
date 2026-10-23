"""tutor 提示词资产守卫（C01-P1）。

``prompts/tutor.py`` 是整个产品的核心教学资产，历史上多次「只做加法」
迭代（v1.2 模式切换、v1.4 四段增强）。本文件锁两类东西：

1. **既有铁律不丢**：条件式不给答案、一次一件事、不用否定词、
   公式纯文本、四种响应模式——这些是被测试与前端依赖的行为契约，
   任何「顺手精简」都不许删；
2. **v1.4 四段增强在**：孩子是思考的主人 / 鼓励只说一个点 /
   接住情绪 / 我们的约定——每段的关键约束句必须逐字在，
   且四段都在【语气与节奏】之后（增强是追加，不是替换）。
"""

from __future__ import annotations

from app.ai.prompts import tutor


def test_tutor_prompt_version_is_current() -> None:
    """VERSION 必须与 docstring 记录的最高版本一致（1.4.0 = 四段增强）。"""
    assert tutor.VERSION == "1.4.0"


def test_existing_iron_rules_survive() -> None:
    """既有铁律逐条锁死：条件式答案锁 / 一次一件事 / 无否定词 / 公式纯文本。"""
    p = tutor.PROMPT
    # 条件式：GUIDE 下不给答案，DEMO 放开
    assert "第一轮绝不直接给答案" in p
    assert "讲解模式（DEMO）时，本条不适用" in p
    # 一次只说一件事
    assert "一次只说一件事" in p
    # 不用否定词
    assert "不用\"你错了\"\"不对\"这类词" in p
    # 公式纯文本（LaTeX 禁令）
    assert "绝不用 LaTeX" in p


def test_response_modes_section_survive() -> None:
    """四种响应模式段（v1.2 资产）仍在，且强调模型不自行切换。"""
    p = tutor.PROMPT
    for mode in ("引导模式（GUIDE）", "提示模式（HINT）", "讲解模式（DEMO）", "评判模式（JUDGE）"):
        assert mode in p, mode
    assert "绝不自己切换" in p


def test_v14_four_sections_present() -> None:
    """v1.4 四段增强标题全部在，且位于【语气与节奏】之后（追加而非替换）。"""
    p = tutor.PROMPT
    sections = (
        "【孩子是思考的主人】",
        "【鼓励只说一个点】",
        "【接住情绪】",
        "【我们的约定】",
    )
    for s in sections:
        assert s in p, s
    anchor = p.index("【语气与节奏】")
    for s in sections:
        assert p.index(s) > anchor, f"{s} 必须出现在【语气与节奏】之后"


def test_v14_key_constraints_verbatim() -> None:
    """四段的关键约束句逐字锁——这是行为契约，漂移=教学行为漂移。"""
    p = tutor.PROMPT
    # autonomy：思考权归孩子，先问他试到哪步
    assert "先问一句他试到哪一步了" in p
    # 激励单角度：最多一个具体角度，禁堆词
    assert "每轮最多从一个具体的地方肯定他" in p
    assert "不堆词" in p
    assert "真棒真聪明太好了" in p  # 禁用语原样列出
    # 情绪接纳：先共情再降台阶，禁比较禁鸡汤
    assert "先接住情绪，再拉回题目" in p
    assert "不灌鸡汤" in p
    assert "别人都会" in p  # 禁用语原样列出
    # pedagogical contract：合作约定讲清且不每轮重复
    assert "这个约定讲一次就够" in p


def test_emotion_section_does_not_leak_into_modes() -> None:
    """情绪接法不能覆盖模式切换的权威——模式段仍声明「系统每轮会告诉你」。"""
    p = tutor.PROMPT
    modes_idx = p.index("【响应模式切换")
    assert "系统会在每轮的提示末尾附一行" in p[modes_idx:]


def test_prompt_still_rejects_templating() -> None:
    """「看题说话，不套模板」铁律不受四段增强影响（增强不是新模板）。"""
    assert "看题说话，不套模板" in tutor.PROMPT
    assert "没有固定问句" in tutor.PROMPT
