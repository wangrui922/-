"""质量闸门单元测试（T05 · P1）。

只测 ``app.services.importers.quality_gate.classify_question`` 这一纯函数：
    * 五条规则各自能独立命中并产出可读原因；
    * clean 题返回 ``("clean", [])``；
    * 空题干返回 review（题干为空）；
    * 命中多条规则时原因被**全部**累计（不短路）。

不依赖数据库/上下文，可随时对存量题重算。
"""
from __future__ import annotations

from app.services.importers.quality_gate import (
    ANSWER_LEAK_MARKERS,
    MAX_CONTENT_CHARS,
    VERDICT_CLEAN,
    VERDICT_REVIEW,
    classify_question,
    is_clean,
    is_placeholder_only,
)

# ----------------------------------------------------------------- 过长
def test_overlong_content_flagged():
    """题干去空白后 > 250 字 → review，原因含『题干过长』。"""
    content = "一、解决问题。" + "小明每天步行上学。" * 40  # 远超 250 字
    verdict, reasons = classify_question(content)
    assert verdict == VERDICT_REVIEW
    assert any("题干过长" in r for r in reasons)


def test_just_under_limit_is_clean_by_length():
    """恰好在 250 字以内（且无其它污染）应判 clean。"""
    content = (
        "计算下列各题并验算。小明买了一些铅笔和橡皮，一共花了 12 元。"
        "已知铅笔每支 2 元，橡皮每块 1 元，小明买了 3 支铅笔，"
        "请问橡皮买了几块？请列算式并写出答案。"
    )
    assert len("".join(content.split())) < MAX_CONTENT_CHARS
    verdict, reasons = classify_question(content)
    assert verdict == VERDICT_CLEAN
    assert reasons == []


# ----------------------------------------------------------------- 讲解窜入
def test_answer_leak_markers_flagged():
    """题干含『答案详解/思路点拨/答：』任一标志词 → review。"""
    for marker in ANSWER_LEAK_MARKERS:
        verdict, reasons = classify_question(f"一、计算。这道题{marker}结果是 5。")
        assert verdict == VERDICT_REVIEW, marker
        assert any("讲解" in r or "答案" in r for r in reasons), marker


def test_plain_answer_colon_is_not_flagged_but_full_marker_is():
    """仅出现普通『:』或『：』不构成讲解窜入，只有『答：』才命中。"""
    # 普通冒号不命中
    verdict, _ = classify_question("列式：3+4=？")
    assert verdict == VERDICT_CLEAN
    # 『答：』命中
    verdict2, _ = classify_question("3+4=7 答：结果是 7")
    assert verdict2 == VERDICT_REVIEW


# ----------------------------------------------------------------- 纯占位依赖插图
def test_placeholder_only_flagged():
    """``□○□=□(只)`` 这类只含量词的纯占位题 → review（纯占位依赖插图）。"""
    verdict, reasons = classify_question("□○□=□(只)")
    assert verdict == VERDICT_REVIEW
    assert any("纯占位" in r for r in reasons)


def test_real_question_not_flagged_as_placeholder():
    """含足够汉字的正常题不因『纯占位』规则被误杀。"""
    verdict, reasons = classify_question("把 3、5、7 填入算式，使等式成立。")
    assert not any("纯占位" in r for r in reasons)


def test_is_placeholder_only_unit():
    """is_placeholder_only 纯函数单测。"""
    # 只含量词 / 纯符号 → 占位
    assert is_placeholder_only("□○□=□(只)") is True
    assert is_placeholder_only("3+5=") is True
    assert is_placeholder_only("") is True
    # 正常题 → 非占位
    assert is_placeholder_only("用一根长1米的铁丝围等边三角形，边长是多少") is False
    assert is_placeholder_only("把 3、5、7 填入算式，使等式成立。") is False


# ----------------------------------------------------------------- 双份抽取
def test_duplicate_ratio_flagged():
    """文本框双份抽取（整体重复率 ≥ 40%）→ review。"""
    # 把同一段反复拼接，重复率远高于 0.4，且 token 数超过下限
    base = "小明有苹果 小红有橘子 小刚有香蕉 "
    content = base * 10  # 长且高度重复
    verdict, reasons = classify_question(content)
    assert verdict == VERDICT_REVIEW
    assert any("双份" in r for r in reasons)


def test_short_question_not_falsely_flagged_as_duplicate():
    """短算式天然重复（如 3×8=24 8×3=24）token 数不足下限，不应误杀。"""
    verdict, reasons = classify_question("计算：3×8=24，8×3=24。")
    assert verdict == VERDICT_CLEAN
    assert not any("双份" in r for r in reasons)


# ----------------------------------------------------------------- 多题号粘连
def test_multiple_question_markers_flagged():
    """题干含 ≥3 个『N.』题号标记 → review（疑似多题粘连）。"""
    content = "1. 计算 2+3。 2. 计算 5-1。 3. 计算 4×2。 4. 计算 9÷3。"
    verdict, reasons = classify_question(content)
    assert verdict == VERDICT_REVIEW
    assert any("题号" in r for r in reasons)


def test_single_marker_not_flagged():
    """单个题号标记不足以触发（需 ≥3）。"""
    verdict, reasons = classify_question("1. 计算 2+3 等于多少。")
    assert not any("题号" in r for r in reasons)


def test_pdf_split_decimal_not_flagged_as_marker():
    """PDF 转档把 2.4 拆成『2. 4』不应被当题号（小数点保护）。"""
    verdict, reasons = classify_question("解方程 0. 5x＝0. 4y，求 x。")
    assert not any("题号" in r for r in reasons)


def test_subquestion_parens_not_flagged_as_marker():
    """（1）（2）等同题分小问不判粘连（与 blocks 切分内核一致）。"""
    verdict, reasons = classify_question(
        "换一换。(1) 把下面的数从小到大排一排。(2) 圈出大于 5 的数。(3) 说一说你的理由。"
    )
    assert not any("题号" in r for r in reasons)


# ----------------------------------------------------------------- clean / 空 / 累计
def test_clean_short_question():
    """普通短题 → clean 且原因列表为空。"""
    verdict, reasons = classify_question("一、计算。12 - 5 = ( )。")
    assert verdict == VERDICT_CLEAN
    assert reasons == []
    assert is_clean("一、计算。12 - 5 = ( )。") is True
    assert is_clean("1. 计算 2. 计算 3. 计算 4. 计算") is False


def test_empty_content_flagged():
    """空题干 → review（题干为空）。"""
    verdict, reasons = classify_question("")
    assert verdict == VERDICT_REVIEW
    assert reasons == ["题干为空"]

    verdict2, _ = classify_question("   \n\t  ")
    assert verdict2 == VERDICT_REVIEW


def test_multiple_reasons_accumulated():
    """同时命中多条规则时，原因被全部累计（不短路）。"""
    # 长 + 含『答：』+ 纯数字长串（无占位形状，不应误判纯占位）
    content = "答：结果是这样。" + "1234567890" * 40
    verdict, reasons = classify_question(content)
    assert verdict == VERDICT_REVIEW
    # 至少命中『过长』与『讲解』
    assert any("过长" in r for r in reasons)
    assert any("讲解" in r or "答案" in r for r in reasons)
