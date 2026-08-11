"""苏教版期末卷解析器（T05 · T3）。

两种版式：

**A 组 · 期末摸底诊断卷**（``正文.docx`` + ``答案.docx`` 分离）
    这是全项目**最高风险**的地方（R-02）：错配答案比缺答案危害大得多。
    配对关系在 ``manifest.py`` 里**硬登记**为 ``pair_key=(grade, paper_no)``，
    运行时再过**三重闸门**：

        闸门① 配对文件必须**恰好** 1 正文 + 1 答案（含文件存在性）；
        闸门② **正文**解析出的题数不得为 0；
        闸门③ 题号集合 overlap（交集 / 正文题号数）分档处置。

    闸门①②是**结构性错误** → 抛 :class:`PairingError` → 整卷跳过 + 记失败清单。

    闸门③改为**分档处置**（题目不再被连坐丢弃）：

        ``overlap ≥ 0.8``            配对正常，挂精确匹配到的答案；
        ``0.3 ≤ overlap < 0.8``      答案文件本身不全（图题/空白无文本答案），
                                     仍挂**精确匹配**的答案 + 记 warning，题目全保留；
        ``overlap < 0.3``            疑似答案文件错配 → **整卷答案置空** + 记 warning，
                                     题目仍全保留。

    ⛔ 严禁降级的红线**未变**：不做顺序对齐、不做文件名模糊匹配、不按位置猜配。
    挂答案永远只走 ``(major_no, minor_no)`` 精确键，且答案索引会先被
    **正文题号集合过滤**一遍（杜绝答案侧伪键碰撞出错答案）。
    「题目照常入库、答案宁缺毋错」不属于 best-effort 猜配——它是安全退化态。

**B 组 · 期末冲刺专项复习卷**（正文 + 答案合一，单文件含 5 套）
    先按 ``专项复习卷N`` 分卷，卷内再按 ``答案`` 标记切成题干区 / 答案区。
    B 组不涉及跨文件配对，闸门①不适用；闸门②③仍然生效，但**不达标时的
    处置是「整卷答案置空、题目照常入库」**——因为答案与题目同源同册，
    风险点在切分而不在配对，留空即可退化成安全态（``correct_answer`` 可空）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

from app.services.importers.blocks import NO_MINOR, BlockSegmenter, RawBlock
from app.services.importers.docx_reader import read_paragraphs
from app.services.importers.manifest import (
    ROLE_ANSWER,
    ROLE_BODY,
    DocxSource,
)
from app.services.importers.schema import (
    PairingError,
    ParsedQuestion,
    ParseOutcome,
    SkippedPaper,
    build_stable_key,
)
from app.services.importers.text_clean import (
    clean_line,
    detect_question_type,
    extract_options,
    validate_question_type,
)

#: 闸门③ 的 overlap 达标线（题号集合交集 / 正文题号数）：≥ 此值视为配对正常
OVERLAP_THRESHOLD = 0.8

#: 闸门③ 的 overlap 底线：低于此值视为「疑似答案文件错配」，整卷答案置空
LOW_OVERLAP_THRESHOLD = 0.3

#: B 组分卷标记（``专项复习卷3 分数的意义…``，前面可能还挂着「期末复习冲刺卷」）
B_PAPER_SEARCH = re.compile(r"专项复习卷\s*(\d{1,2})")
#: B 组卷内答案区标记
B_ANSWER_MARKER = re.compile(r"^答\s*案\s*$|^参考答案")

#: 题干最短 / 最长长度
MIN_CONTENT_LENGTH = 6
MAX_CONTENT_LENGTH = 1200


# ==================================================================== 通用工具
def _load_lines(path: Union[str, Path]) -> List[str]:
    """读 docx 并清洗成段落列表。

    Args:
        path: docx 路径。

    Returns:
        非空的已清洗段落列表。
    """
    out: List[str] = []
    for para in read_paragraphs(path):
        cleaned = clean_line(para.text)
        if cleaned:
            out.append(cleaned)
    return out


def _segment_body(lines: Sequence[str]) -> List[RawBlock]:
    """切正文题块（大题号带顿号，允许内联大题/小题）。

    Args:
        lines: 已清洗段落。

    Returns:
        题块列表。
    """
    segmenter = BlockSegmenter(
        require_major_separator=True,
        allow_inline_major=True,
        loose_minor=False,
    )
    for line in lines:
        segmenter.feed(line)
    return segmenter.finish()


def _segment_answers(
    lines: Sequence[str], *, loose_minor: bool = False
) -> Dict[Tuple[str, str], str]:
    """切答案并建索引 ``(大题号, 小题号) -> 答案文本``。

    Args:
        lines: 答案侧已清洗段落。
        loose_minor: 是否允许小题号紧跟在数字后面（``403.`` = 答案 40 + 小题 3）。
            默认 ``False``，与正文侧 :func:`_segment_body` 保持一致。
            ⚠️ 置 ``True`` 会从密集答案串里造出**伪题号键**，这些伪键可能与正文
            真实题号碰撞、把无关文本挂成答案（违反 R-02：错配答案 ≫ 缺答案）。
            实测 6 下(二) 一卷就能造出 16 个伪键，故 A 组一律用 ``False``。

    Returns:
        答案索引。
    """
    segmenter = BlockSegmenter(
        require_major_separator=True,
        allow_inline_major=True,
        loose_minor=loose_minor,
    )
    for line in lines:
        segmenter.feed(line)
    index: Dict[Tuple[str, str], str] = {}
    for block in segmenter.finish():
        text = block.text
        if text:
            index[(block.major_no, block.minor_no)] = text
    return index


def _block_keys(blocks: Sequence[RawBlock]) -> Set[Tuple[str, str]]:
    """题块的题号集合。

    Args:
        blocks: 题块列表。

    Returns:
        ``{(大题号, 小题号)}``。
    """
    return {(b.major_no, b.minor_no) for b in blocks}


def compute_overlap(
    question_keys: Set[Tuple[str, str]],
    answer_keys: Set[Tuple[str, str]],
) -> float:
    """闸门③ 的 overlap 比率。

    Args:
        question_keys: 正文侧题号集合。
        answer_keys: 答案侧题号集合。

    Returns:
        ``交集大小 / 正文题号数``；正文为空时返回 0.0。
    """
    if not question_keys:
        return 0.0
    return len(question_keys & answer_keys) / len(question_keys)


def _make_question(
    source: DocxSource,
    paper_no: str,
    block: RawBlock,
    answer: Optional[str],
) -> Optional[ParsedQuestion]:
    """题块 → :class:`ParsedQuestion`。

    Args:
        source: 源文件登记项。
        paper_no: 卷号。
        block: 题块。
        answer: 匹配到的答案。

    Returns:
        题目对象；题干过短时返回 ``None``。
    """
    content = block.text
    if len(content) < MIN_CONTENT_LENGTH:
        return None
    if len(content) > MAX_CONTENT_LENGTH:
        content = content[:MAX_CONTENT_LENGTH]

    q_type = validate_question_type(detect_question_type(block.major_title, content))
    return ParsedQuestion(
        stable_key=build_stable_key(source.filename, paper_no, block.major_no, block.minor_no),
        content=content,
        q_type=q_type,
        paper_no=paper_no,
        major_no=block.major_no,
        minor_no=block.minor_no,
        section=source.section,
        correct_answer=answer,
        analysis=None,  # 原书只有答案、无解析文本，留空待 AI 补
        options=extract_options(content),
    )


# ==================================================================== A 组
def parse_pair(
    body_sources: Sequence[DocxSource],
    answer_sources: Sequence[DocxSource],
    source_dir: Path,
) -> ParseOutcome:
    """解析苏教版 A 组的一卷（正文 + 答案），过三重闸门。

    Args:
        body_sources: 该 ``pair_key`` 下角色为 ``body`` 的登记项。
        answer_sources: 该 ``pair_key`` 下角色为 ``answer`` 的登记项。
        source_dir: 源目录。

    Returns:
        :class:`ParseOutcome`。低 overlap 时**题目照常返回**，只有答案会被
        按档位削减（见模块 docstring 的闸门③分档处置）。

    Raises:
        PairingError: 闸门①（配对文件数/存在性）或闸门②（正文题数为 0）未通过
            ——这两类是**结构性错误**，调用方应整卷跳过并记失败。
    """
    # ---------------------------------------------------- 闸门① 文件数必须精确
    if len(body_sources) != 1 or len(answer_sources) != 1:
        raise PairingError(
            f"闸门①未通过：配对文件数不是 1 正文 + 1 答案"
            f"（正文 {len(body_sources)} 个 / 答案 {len(answer_sources)} 个）"
        )
    body_src = body_sources[0]
    answer_src = answer_sources[0]

    body_path = body_src.path(source_dir)
    answer_path = answer_src.path(source_dir)
    if not body_path.exists():
        raise PairingError(f"闸门①未通过：正文文件缺失 {body_src.filename}")
    if not answer_path.exists():
        raise PairingError(f"闸门①未通过：答案文件缺失 {answer_src.filename}")

    body_blocks = _segment_body(_load_lines(body_path))
    # 答案侧与正文侧用同一套小题号规则（loose_minor=False），避免伪键碰撞挂错答案
    answer_index = _segment_answers(_load_lines(answer_path))

    # ---------------------------------------------------- 闸门② 正文题数不得为 0
    # 正文为 0 是结构性错误（文件选错/解析器不匹配）→ 抛错整卷跳过。
    # 答案为 0 只是「这份答案没解析出东西」→ 不抛错，题目照常入库、答案留空。
    if not body_blocks:
        raise PairingError(f"闸门②未通过：正文解析题数为 0（{body_src.filename}）")

    question_keys = _block_keys(body_blocks)
    raw_answer_count = len(answer_index)

    # 防御性兜底（R-02）：只保留题号确实出现在正文里的答案条目。
    # 答案侧一旦切出正文没有的题号，那多半是切分噪声；留着它只会增加挂错的机会。
    answer_index = {key: val for key, val in answer_index.items() if key in question_keys}
    dropped_keys = raw_answer_count - len(answer_index)

    paper_no = f"摸底诊断卷({body_src.pair_key[1]})" if body_src.pair_key else "摸底诊断卷"
    outcome = ParseOutcome(
        source_key=body_src.key,
        label=body_src.label.replace("·正文", ""),
        edition=body_src.edition,
        source_type=body_src.source_type,
        grade=body_src.grade,  # U1：升入年级，禁止任何 grade-1 换算
        source_doc=body_src.filename,
    )

    if not raw_answer_count:
        outcome.warnings.append(
            f"答案文件未解析出任何答案条目（{answer_src.filename}），本卷答案全部留空"
        )

    # ---------------------------------------------------- 闸门③ overlap 分档处置
    # 过滤后 answer_keys ⊆ question_keys，交集不变，故 overlap 数值与过滤前一致。
    answer_keys = set(answer_index.keys())
    overlap = compute_overlap(question_keys, answer_keys)
    matched = len(question_keys & answer_keys)
    stat = (
        f"正文 {len(question_keys)} 题号 / 答案 {raw_answer_count} 题号"
        f"（正文外题号已过滤 {dropped_keys} 个），交集 {matched}"
    )

    if overlap >= OVERLAP_THRESHOLD:
        outcome.warnings.append(
            f"配对通过：overlap={overlap:.2f}（答案来源 {answer_src.filename}）"
        )
    elif overlap >= LOW_OVERLAP_THRESHOLD:
        # 答案 docx 本身就不全（图题/操作题没有文本答案）——这不是配对错误。
        # 仍按精确题号挂答案，题目**全部保留**，缺答案的留空待 AI 补。
        outcome.warnings.append(
            f"部分答案缺失（图题/空白），仅挂精确匹配的答案："
            f"overlap={overlap:.2f} < {OVERLAP_THRESHOLD}（{stat}，"
            f"答案来源 {answer_src.filename}）"
        )
    else:
        # 交集低到这个程度，更可能是「答案文件配错了」而不是「答案不全」。
        # 宁缺毋错：整卷答案置空，但题目照常入库。
        outcome.warnings.append(
            f"疑似答案文件错配：overlap={overlap:.2f} < {LOW_OVERLAP_THRESHOLD}"
            f"（{stat}，答案来源 {answer_src.filename}）。"
            f"整卷答案置空（宁缺毋错），题目正常入库"
        )
        answer_index = {}

    for block in body_blocks:
        answer = answer_index.get((block.major_no, block.minor_no))
        question = _make_question(body_src, paper_no, block, answer)
        if question is not None:
            outcome.questions.append(question)
    return outcome


# ==================================================================== B 组
def _split_b_papers(lines: Sequence[str]) -> List[Tuple[str, List[str]]]:
    """把 B 组单文件按 ``专项复习卷N`` 分卷。

    Args:
        lines: 已清洗段落。

    Returns:
        ``[(卷号, [该卷段落]), ...]``。
    """
    papers: List[Tuple[str, List[str]]] = []
    current_no: Optional[str] = None
    buffer: List[str] = []

    for line in lines:
        match = B_PAPER_SEARCH.match(line) or (
            B_PAPER_SEARCH.search(line) if len(line) <= 40 else None
        )
        if match is not None:
            if current_no is not None and buffer:
                papers.append((current_no, buffer))
            current_no = f"专项复习卷{match.group(1)}"
            buffer = []
            tail = line[match.end():].strip()
            if tail:
                buffer.append(tail)
            continue
        if current_no is not None:
            buffer.append(line)

    if current_no is not None and buffer:
        papers.append((current_no, buffer))
    return papers


def _split_b_regions(lines: Sequence[str]) -> Tuple[List[str], List[str]]:
    """把 B 组卷内段落切成「题干区 / 答案区」。

    Args:
        lines: 单卷段落。

    Returns:
        ``(题干区, 答案区)``。
    """
    for idx, line in enumerate(lines):
        if B_ANSWER_MARKER.match(line):
            return list(lines[:idx]), list(lines[idx + 1:])
    return list(lines), []


def parse_b(source: DocxSource, path: Union[str, Path]) -> ParseOutcome:
    """解析苏教版 B 组（单文件含 5 套专项卷）。

    Args:
        source: 源文件登记项。
        path: docx 路径。

    Returns:
        :class:`ParseOutcome`。
    """
    outcome = ParseOutcome(
        source_key=source.key,
        label=source.label,
        edition=source.edition,
        source_type=source.source_type,
        grade=source.grade,  # U1：升入年级，禁止任何 grade-1 换算
        source_doc=source.filename,
    )

    lines = _load_lines(path)
    papers = _split_b_papers(lines)
    if not papers:
        outcome.skipped.append(
            SkippedPaper(paper_no="*", reason="no_paper", detail="未识别到「专项复习卷N」分卷标记")
        )
        return outcome

    for paper_no, paper_lines in papers:
        body_lines, answer_lines = _split_b_regions(paper_lines)
        body_blocks = _segment_body(body_lines)

        # 闸门②：本卷解析不出题目 → 整卷跳过
        if not body_blocks:
            outcome.skipped.append(
                SkippedPaper(paper_no=paper_no, reason="empty_body", detail="正文未切出任何题块")
            )
            continue

        answer_index = _segment_answers(answer_lines) if answer_lines else {}
        overlap = compute_overlap(_block_keys(body_blocks), set(answer_index.keys()))
        # 闸门③（B 组处置）：overlap 不足 → 本卷答案整体置空，题目照常入库
        if answer_index and overlap < OVERLAP_THRESHOLD:
            outcome.warnings.append(
                f"{paper_no}：题号 overlap={overlap:.2f} < {OVERLAP_THRESHOLD}，"
                f"整卷答案置空（宁缺毋错），题目正常入库"
            )
            answer_index = {}
        elif not answer_index:
            outcome.warnings.append(f"{paper_no}：未识别到答案区，答案留空")

        for block in body_blocks:
            answer = answer_index.get((block.major_no, block.minor_no))
            question = _make_question(source, paper_no, block, answer)
            if question is not None:
                outcome.questions.append(question)

    if not outcome.questions:
        outcome.skipped.append(
            SkippedPaper(paper_no="*", reason="no_question", detail="整个文件未解析出任何题目")
        )
    return outcome


__all__ = [
    "OVERLAP_THRESHOLD",
    "LOW_OVERLAP_THRESHOLD",
    "compute_overlap",
    "parse_pair",
    "parse_b",
    "ROLE_BODY",
    "ROLE_ANSWER",
    "NO_MINOR",
]
