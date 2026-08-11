"""青岛版暑假衔接卷解析器（T05 · T3）。

版式特征（已抽样核对 5 册）：
    1. 封面 + 目录（目录行以 ``/页码`` 收尾）
    2. 正文，按「第X部分」分区：
         第一部分 温故而知新 → section=温故
         第二部分 暑假提优班 → section=提优
         第三部分 新课衔接站 → section=预习
         第四部分 开学摸摸底 → section=摸底
       每个分区里有若干**单元**：``巩固提升N`` / ``思维提优N`` / ``奥赛大讲堂N`` /
       ``第N课时`` / ``学期达标自测卷`` / ``入学摸底自测卷``。
    3. 文末「视频详讲答案」之后是**集中答案区**，按同样的单元标题重新组织一遍，
       每个单元下是 ``一、…`` ``二、…`` 的答案。

关键坑：转档时大题号后的顿号常常丢失（``一细心思考填一填。``），
所以题干侧用 ``require_major_separator=False`` 的宽松匹配 + 顺序期望兜底；
答案侧顿号保留完好，用严格匹配。

答案回填策略：按 ``(单元, 大题号, 小题号)`` 精确匹配。**匹配不上就留空**，
绝不按顺序硬凑——错答案比没答案危害大得多。

⚠️ 切分顺序陷阱（已修）：``text_clean.INLINE_NOISE_PATTERNS`` 会把 ``视频详讲答案``
当作行内噪声抠掉，所以**必须先在原始段落上定位答案区标记、再分别清洗两侧**。
反过来（先清洗再找标记）会导致标记永远找不到 → 全册 0 答案。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

from app.services.importers.blocks import NO_MINOR, BlockSegmenter, RawBlock
from app.services.importers.docx_reader import read_paragraphs
from app.services.importers.manifest import DocxSource
from app.services.importers.schema import (
    ParsedQuestion,
    ParseOutcome,
    SkippedPaper,
    build_stable_key,
)
from app.services.importers.text_clean import (
    clean_lines,
    detect_question_type,
    extract_options,
    is_toc_line,
    validate_question_type,
)

#: 答案区起点标记（文末「视频详讲答案」）
ANSWER_SECTION_MARKERS: Tuple[str, ...] = ("视频详讲答案", "参考答案", "答案详解汇总")

#: 同名单元的序号后缀分隔符（``第1课时`` 的第二次出现 → ``第1课时#2``）。
#: 一册书里 ``第1课时`` 等标题会在不同章节重复出现，必须区分开，
#: 否则主键相撞（落库 UNIQUE 冲突）、答案串单元。详见 :func:`_group_by_unit`。
_UNIT_OCCURRENCE_SEP = "#"

#: 答案区标记「独立成段」的宽容字数（标记后允许挂少量残字，如页码）
_MARKER_SLACK = 10

#: 单元标题
UNIT_PATTERN = re.compile(
    r"^(?:能力训练营)?\s*(巩固提升|思维提优|奥赛大讲堂|专题突破)\s*(\d{1,2})"
)
#: 新课衔接站的课时标题
LESSON_PATTERN = re.compile(r"^第\s*(\d{1,2})\s*课时\s*(.*)$")
#: 自测卷标题
PAPER_PATTERN = re.compile(r"^(学期达标自测卷|入学摸底自测卷|期末自测卷)")

#: 分区标题 → section 取值
SECTION_PATTERNS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"^第一部分\s*温故而知新"), "温故"),
    (re.compile(r"^第二部分\s*暑假提优班"), "提优"),
    (re.compile(r"^第三部分\s*新课衔接站"), "预习"),
    (re.compile(r"^第四部分\s*开学摸摸底"), "摸底"),
)

#: 答案区「行首断号重同步」窗口（见 :class:`BlockSegmenter` 的 ``minor_resync``）。
#: 实测 4 已经吃满收益（再放大到 6/10/20 结果完全一致），窗口越小误报面越小。
MINOR_RESYNC_WINDOW = 4

#: 题干最短长度（更短的多半是残行）
MIN_CONTENT_LENGTH = 6
#: 题干最长长度（更长的多半是把整页粘一起了，截断保存）
MAX_CONTENT_LENGTH = 1200


def _match_unit(line: str) -> Optional[str]:
    """识别单元标题。

    Args:
        line: 已清洗的段落文本。

    Returns:
        单元号（如 ``巩固提升1``、``第3课时``、``学期达标自测卷``）；不是标题返回 ``None``。
    """
    m = UNIT_PATTERN.match(line)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    m = LESSON_PATTERN.match(line)
    if m:
        return f"第{m.group(1)}课时"
    m = PAPER_PATTERN.match(line)
    if m:
        return m.group(1)
    return None


def _match_section(line: str) -> Optional[str]:
    """识别分区标题。

    Args:
        line: 已清洗的段落文本。

    Returns:
        section 取值；不是分区标题返回 ``None``。
    """
    for pattern, section in SECTION_PATTERNS:
        if pattern.match(line):
            return section
    return None


def _find_answer_marker(texts: Sequence[str]) -> Tuple[int, Optional[str]]:
    """在**原始**段落文本里定位答案区起点。

    必须在 :func:`clean_line` 之前调用——清洗会把 ``视频详讲答案`` 整段抠掉。

    定位规则（已按 5 册真实 docx 逐一核对）：取**第一个**满足下面两条的段落，

        1. 不是目录行——目录里那条长这样：``视频详讲答案/59``（带页码）；
        2. 标记**独立成段**（整段长度 ≈ 标记本身），排除正文里偶然提到的情况。

    ⚠️ 不能取「最后一次出现」：标记会作为**页眉重复印在答案区每一页上**
    （二升三 3 处 / 三升四 5 处 / 五升六 3 处），取最后一次会把答案区自己
    切掉一大半，同时把前半截答案当成题目误收进题干区。

    Args:
        texts: **未清洗**的段落文本列表。

    Returns:
        ``(标记所在下标, 实际命中的标记文本)``；找不到时返回 ``(-1, None)``。
    """
    for idx, text in enumerate(texts):
        token = (text or "").strip()
        if not token or is_toc_line(token):
            continue
        for mark in ANSWER_SECTION_MARKERS:
            # 「独立成段」判定：允许标记后带少量残字（页码/空格），不允许整句话
            if mark in token and len(token) <= len(mark) + _MARKER_SLACK:
                return idx, mark
    return -1, None


def _split_regions(texts: Sequence[str]) -> Tuple[List[str], List[str]]:
    """把**原始**段落切成「题干区 / 答案区」（标记行本身两边都不要）。

    Args:
        texts: **未清洗**的段落文本列表。

    Returns:
        ``(题干区原始段落, 答案区原始段落)``；找不到标记时答案区为空列表。
    """
    marker_index, _marker = _find_answer_marker(texts)
    if marker_index < 0:
        return list(texts), []
    return list(texts[:marker_index]), list(texts[marker_index + 1:])


def _group_by_unit(lines: List[str], *, track_section: bool) -> List[Tuple[str, Optional[str], List[str]]]:
    """按单元标题把段落分组。

    Args:
        lines: 段落列表。
        track_section: 是否同时跟踪「第X部分」分区。

    Returns:
        ``[(单元号, section, [该单元的段落]), ...]``；单元标题之前的散行会被丢弃。

    Note:
        **同名单元必须去重**。一册书里 ``第1课时`` / ``第2课时`` / ``第3课时`` 会在
        不同章节下**重复出现**（实测一升二/三升四各重复 3 个标题）。若直接拿标题当
        单元号，会连锁引发两个事故：

        1. :func:`app.services.importers.schema.build_stable_key` 以
           ``(文件, 单元, 大题, 小题)`` 为主键 → 后一个同名单元的题与前一个**撞主键**，
           落库直接 ``UNIQUE constraint failed``（实测 58 行冲突），或静默覆盖丢题；
        2. :func:`_build_answer_index` 也以单元号为键 → 后一个同名单元的答案会覆盖
           前一个，导致题目挂上**别的单元的答案**（比没答案更糟）。

        对策：按出现顺序给重复标题加序号后缀（``第1课时`` / ``第1课时#2``）。
        正文区与答案区**共用本函数**，编号规则一致，因此 ``#2`` 的题仍能对上 ``#2``
        的答案；若答案区少一次出现（实测四升五 第2课时），则查不到 → 答案留空，
        符合「宁缺毋错」。
    """
    groups: List[Tuple[str, Optional[str], List[str]]] = []
    current_unit: Optional[str] = None
    current_section: Optional[str] = None
    buffer: List[str] = []
    # 已产出的单元标题出现次数，用于给重复标题编号
    seen: Dict[str, int] = {}

    def _flush(unit: str, section: Optional[str], lines_buf: List[str]) -> None:
        """把一个单元分组落到 groups，并按需给重复标题加序号后缀。"""
        seen[unit] = seen.get(unit, 0) + 1
        occurrence = seen[unit]
        label = unit if occurrence == 1 else f"{unit}{_UNIT_OCCURRENCE_SEP}{occurrence}"
        groups.append((label, section, lines_buf))

    for line in lines:
        if track_section:
            section = _match_section(line)
            if section is not None:
                current_section = section
                continue
        unit = _match_unit(line)
        if unit is not None:
            if current_unit is not None and buffer:
                _flush(current_unit, current_section, buffer)
            current_unit = unit
            buffer = []
            continue
        if current_unit is not None:
            buffer.append(line)

    if current_unit is not None and buffer:
        _flush(current_unit, current_section, buffer)
    return groups


def _index_one_pass(answer_lines: List[str], *, minor_resync: int) -> Dict[Tuple[str, str, str], str]:
    """用一套切分参数扫一遍答案区，产出 ``(单元, 大题号, 小题号) -> 答案文本``。

    Args:
        answer_lines: 答案区段落列表。
        minor_resync: 行首断号重同步窗口（见 :class:`BlockSegmenter`）。

    Returns:
        答案索引。
    """
    index: Dict[Tuple[str, str, str], str] = {}
    for unit, _section, buffer in _group_by_unit(answer_lines, track_section=False):
        segmenter = BlockSegmenter(
            require_major_separator=True,   # 答案侧顿号保留完好
            allow_inline_major=True,
            loose_minor=True,               # 「403.」= 答案 40 + 小题 3
            minor_resync=minor_resync,
        )
        for line in buffer:
            segmenter.feed(line)
        for block in segmenter.finish():
            text = block.text
            if not text:
                continue
            index[(unit, block.major_no, block.minor_no)] = text
    return index


def _build_answer_index(answer_lines: List[str]) -> Dict[Tuple[str, str, str], str]:
    """把答案区解析成 ``(单元, 大题号, 小题号) -> 答案文本``。**双趟 + 严格优先**。

    为什么要两趟（R-02「宁缺毋错」的工程化落法）：

    * **严格趟**（``minor_resync=0``）＝ 历史行为，一个字节都不变；
    * **补漏趟**（``minor_resync=MINOR_RESYNC_WINDOW``）只用来填严格趟压根
      没产出的键——PDF 转档会把公式/图片型答案整段吞掉，导致某些单元的答案
      区开头就缺编号（``2. B`` 之后直接跳到 ``6.最短…``），严格顺序期望会
      卡死在「1」上，整单元 0 答案（实测青岛五升六 5 个单元中招）。

    合并规则是 ``{**补漏, **严格}``——**严格趟永远覆盖补漏趟**。因此本函数
    对任何输入都满足「**单调不回退**」：今天能匹配上的键，值逐字节不变；
    补漏趟只可能新增键，不可能改写或删除已有键。这条性质让答案配对率只增不减，
    也把「重同步猜错」的爆炸半径限制在**原本就没有答案**的题上。

    ⛔ 闸门（R-02 实测补加）：补漏趟的产物**只在「严格趟已经在该单元切出过
    答案」的单元里采信**。抽检发现，严格趟颗粒无收的单元往往不是「开头缺编号」
    而是**整段答案根本不属于这个单元**（青岛五升六「思维提优1」就是：题干侧是
    工程问题，答案区却是最大公因数铺砖题——该册题干/答案的单元序列本身就对不齐，
    答案区多出「奥赛大讲堂1/2」、少了「思维提优2」）。在这种单元里做重同步，
    等于把隔壁单元的答案硬挂上来，属于「错配答案」，比留空危害大得多。
    严格趟能出货 ⇒ 该单元编号体系是通的 ⇒ 补漏才有意义。

    Args:
        answer_lines: 答案区段落列表。

    Returns:
        答案索引；解析不出来时是空字典。
    """
    strict = _index_one_pass(answer_lines, minor_resync=0)
    if MINOR_RESYNC_WINDOW <= 0:
        return strict
    relaxed = _index_one_pass(answer_lines, minor_resync=MINOR_RESYNC_WINDOW)
    trusted_units = {key[0] for key in strict}
    merged: Dict[Tuple[str, str, str], str] = {
        key: value for key, value in relaxed.items() if key[0] in trusted_units
    }
    merged.update(strict)  # 严格趟优先，保证单调不回退
    return merged


def _lookup_answer(
    index: Dict[Tuple[str, str, str], str],
    unit: str,
    major_no: str,
    minor_no: str,
) -> Optional[str]:
    """精确查答案；查不到返回 ``None``（**绝不做顺序兜底**）。

    Args:
        index: 答案索引。
        unit: 单元号。
        major_no: 大题号。
        minor_no: 小题号。

    Returns:
        答案文本或 ``None``。
    """
    hit = index.get((unit, major_no, minor_no))
    if hit:
        return hit
    if minor_no == NO_MINOR:
        # 题干侧整个大题算一道题时，答案侧可能也只有大题级答案
        hit = index.get((unit, major_no, NO_MINOR))
    return hit or None


def _make_question(
    source: DocxSource,
    unit: str,
    section: Optional[str],
    block: RawBlock,
    answer: Optional[str],
) -> Optional[ParsedQuestion]:
    """把题块转成 :class:`ParsedQuestion`。

    Args:
        source: 源文件登记项。
        unit: 单元号。
        section: 册内分区。
        block: 题块。
        answer: 已匹配到的答案（可为 ``None``）。

    Returns:
        题目对象；题干太短时返回 ``None``（脏数据丢弃）。
    """
    content = block.text
    if len(content) < MIN_CONTENT_LENGTH:
        return None
    if len(content) > MAX_CONTENT_LENGTH:
        content = content[:MAX_CONTENT_LENGTH]

    q_type = validate_question_type(detect_question_type(block.major_title, content))
    options = extract_options(content)
    return ParsedQuestion(
        stable_key=build_stable_key(source.filename, unit, block.major_no, block.minor_no),
        content=content,
        q_type=q_type,
        paper_no=unit,
        major_no=block.major_no,
        minor_no=block.minor_no,
        section=section or source.section,
        correct_answer=answer,
        analysis=None,  # 青岛版原书只有答案、无解析文本，留空待 AI 补
        options=options,
    )


def parse(source: DocxSource, path: Union[str, Path]) -> ParseOutcome:
    """解析一册青岛版衔接卷。

    Args:
        source: 源文件登记项（提供 edition / grade / source_type）。
        path: docx 绝对路径。

    Returns:
        :class:`ParseOutcome`。解析不出任何题目时 ``questions`` 为空，
        并在 ``skipped`` 里记一条原因。
    """
    outcome = ParseOutcome(
        source_key=source.key,
        label=source.label,
        edition=source.edition,
        source_type=source.source_type,
        grade=source.grade,  # U1：升入年级，禁止任何 grade-1 换算
        source_doc=source.filename,
    )

    # ⚠️ 顺序不能反：先在**原始**段落上定位答案区标记，再分别清洗两侧。
    #    clean_line() 会把「视频详讲答案」当行内噪声抠掉，先清洗就永远找不到标记。
    raw_texts = [para.text for para in read_paragraphs(path)]
    marker_index, marker_text = _find_answer_marker(raw_texts)
    body_raw, answer_raw = _split_regions(raw_texts)

    body_lines = clean_lines(body_raw, drop_toc=True)
    answer_lines = clean_lines(answer_raw, drop_toc=True)

    if marker_index < 0:
        outcome.warnings.append(
            "未找到任何答案区标记（"
            + "/".join(ANSWER_SECTION_MARKERS)
            + "），本册答案全部留空"
        )

    answer_index = _build_answer_index(answer_lines)
    if marker_index >= 0 and not answer_index:
        outcome.warnings.append(
            f"已找到「{marker_text}」标记（第 {marker_index} 段），"
            f"但答案区未解析出任何条目，本册答案全部留空"
        )

    for unit, section, buffer in _group_by_unit(body_lines, track_section=True):
        segmenter = BlockSegmenter(
            require_major_separator=False,  # 青岛版顿号会丢
            allow_inline_major=False,       # 题干侧不做中部切分，避免误切
            loose_minor=False,
        )
        for line in buffer:
            segmenter.feed(line)
        blocks = segmenter.finish()
        if not blocks:
            outcome.skipped.append(
                SkippedPaper(paper_no=unit, reason="empty_unit", detail="该单元未切出任何题块")
            )
            continue
        for block in blocks:
            answer = _lookup_answer(answer_index, unit, block.major_no, block.minor_no)
            question = _make_question(source, unit, section, block, answer)
            if question is not None:
                outcome.questions.append(question)

    if not outcome.questions:
        outcome.skipped.append(
            SkippedPaper(paper_no="*", reason="no_question", detail="整册未解析出任何题目")
        )

    answered = sum(1 for q in outcome.questions if q.correct_answer)
    if marker_index >= 0 and answer_index and answered == 0:
        outcome.warnings.append(
            f"已找到「{marker_text}」标记（答案区解析出 {len(answer_index)} 条），"
            f"但无一条与题干题号对上，本册答案全部留空（宁缺毋错）"
        )
    return outcome


__all__ = [
    "ANSWER_SECTION_MARKERS",
    "UNIT_PATTERN",
    "LESSON_PATTERN",
    "SECTION_PATTERNS",
    "parse",
]
