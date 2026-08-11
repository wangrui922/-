"""题块切分状态机（T05 · T2 解析内核）。

> 落点说明：设计文档 §5 的 T2 只点名了 ``docx_reader / text_clean / schema``，
> 本模块是从中拆出来的「两级状态机」实现（设计 §1.1 的难点 D2），仍在
> ``app/services/importers/`` 目录内，属实现细节拆分而非新增落点。

难点（D2）：一个段落 ≠ 一道题。
    * 题干会跨段落（PDF 转档把一句话切成好几段）；
    * 大题号 / 小题号可能**内联**在同一段里
      （``一、填空。(每空2分)  1. 一张方格纸…``、``…价钱。二、判断。…``）；
    * 青岛版的顿号会在转档时丢失（``一细心思考填一填。``）；
    * 答案侧更密：``一、1.5 36  1.2  2. 720 3480 403. 18 158  4.…`` 全在一段里。

对策：**顺序期望（sequential expectation）**。
    任何时刻只承认「下一个该出现的编号」——当前在大题三就只找 ``四``，
    当前在小题 2 就只找 ``3``。这一条规则把绝大多数误切（``1.2`` 里的 ``1.``、
    ``一共有多少`` 里的 ``一``）自然挡掉，同时能正确切开 ``403.`` 这种
    「上一题答案 40」+「小题 3」粘在一起的情况。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from app.services.importers.text_clean import cn_number, collapse_spaces

#: 无小题时使用的占位小题号
NO_MINOR = "0"

#: 大题号后面允许的分隔符
_MAJOR_SEPARATORS = "、.．,，:："

#: 缺分隔符时，紧跟大题号的这些字往往是普通词（一共/一样/…），一律不认作大题号
_MAJOR_FALSE_FRIENDS = frozenset(
    "共样直定般致点下边面年月日些半块个只条张次遍旁道路片群起时分秒元角"
)

#: 缺分隔符时，出现这些词才认作大题标题
_MAJOR_HINT_WORDS = (
    "填一填", "填空", "选一选", "选择", "连一连", "算一算", "画一画", "圈一圈",
    "判断", "计算", "解决问题", "应用", "看图", "写出", "操作", "动脑", "思考",
    "想一想", "做一做", "量一量", "涂一涂", "比一比", "找规律", "解答",
)

#: 缺分隔符时，标题必须以这些标点收尾
_MAJOR_TAIL_PUNCT = "。？?！!：:；;"

#: 小题号后面允许的分隔符（题号后缀）
_MINOR_SEPARATORS = ".、．)）"

#: **纯页码残留行**：整行只有 1~3 位数字，不带任何题号后缀（docx 里的孤行 ``37`` / ``49``）。
#: 这类行必须继续当噪声，不能当小题号。
PAGE_RESIDUAL_RE = re.compile(r"^\s*\d{1,3}\s*$")

#: **独占一行的小题号**：整行只有「数字 + 题号后缀」（``1.`` / ``2．`` / ``3、`` / ``4)``）。
#: 这类行是真正的小题触发点，必须喂给状态机。
STANDALONE_MINOR_RE = re.compile(
    r"^\s*\d{1,3}\s*[" + re.escape(_MINOR_SEPARATORS) + r"]\s*$"
)


def is_page_residual(text: str) -> bool:
    """是否是「纯页码残留」行（整行只有数字，无题号后缀）。

    Args:
        text: 单行文本。

    Returns:
        是纯页码残留返回 ``True``。
    """
    return bool(PAGE_RESIDUAL_RE.match(text or ""))


def is_standalone_minor(text: str) -> bool:
    """是否是「独占一行的小题号」（``1.`` / ``2．`` / ``3、`` / ``4)``）。

    与 :func:`is_page_residual` 互斥：带题号后缀的才算小题号，
    光秃秃的数字（页码残留）不算。

    Args:
        text: 单行文本。

    Returns:
        是独占行小题号返回 ``True``。
    """
    return bool(STANDALONE_MINOR_RE.match(text or ""))


@dataclass
class RawBlock:
    """一个切好的题块。

    Attributes:
        major_no: 大题号（中文数字）。
        major_title: 大题标题（用于判题型）。
        minor_no: 小题号；大题下无小题时为 ``"0"``。
        lines: 题块的文本行。
    """

    major_no: str
    major_title: str
    minor_no: str
    lines: List[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        """题块正文（行间用空格连接）。"""
        return collapse_spaces(" ".join(self.lines))


class BlockSegmenter:
    """「大题号锚点 + 小题号切分」两级状态机。

    用法::

        seg = BlockSegmenter(require_major_separator=True)
        for line in lines:
            seg.feed(line)
        blocks = seg.finish()

    Args:
        require_major_separator: 大题号后是否**必须**带分隔符。
            苏教版转档质量好，置 ``True``；青岛版顿号会丢，置 ``False``。
        allow_inline_major: 是否允许在段落中部识别大题号（苏教版 B 组需要）。
        loose_minor: 小题号是否允许紧跟在数字后面。
            答案侧的密集串（``403.`` = 答案 40 + 小题 3）需要 ``True``；
            题干侧置 ``False``，避免把 ``6. 5厘米`` 这类小数切碎。
        max_major: 大题号上限，超过就不再往下找（防病态扫描）。
        minor_resync: **行首断号重同步**窗口，``0``（默认）= 完全关闭，行为与
            历史版本逐字节一致。置 ``N > 0`` 时：当「期望的下一个小题号」在本行
            找不到，允许改找 ``期望+1 … 期望+N``，但**必须命中在行首**
            （``start == 0``）才认。

            为什么需要它：PDF 转档时，公式/图片型答案会整段丢失文本
            （``read_paragraphs`` 直接丢掉纯图段落），于是答案区常常缺掉开头
            若干个编号——例如某单元答案区第一条就是 ``2. B``、下一条直接跳到
            ``6.最短: (厘米)``。严格顺序期望在这里会**永远卡在 1**，导致整个
            单元的答案索引为空（实测青岛五升六有 5 个单元 0 答案）。

            为什么限定「行首」：答案正文里到处是数字（``150÷24=6……6``），
            允许行内跳号会造出大量**伪题号键**，反而挂错答案（违反 R-02：
            错配答案 ≫ 缺答案）。实测行内跳号会让 四升五 从 85.4% 掉到 61.0%。
            行首命中则是排版意义上的「新答案条目」，误报率低得多。
    """

    def __init__(
        self,
        *,
        require_major_separator: bool = True,
        allow_inline_major: bool = True,
        loose_minor: bool = False,
        max_major: int = 15,
        minor_resync: int = 0,
    ) -> None:
        self.require_major_separator: bool = require_major_separator
        self.allow_inline_major: bool = allow_inline_major
        self.loose_minor: bool = loose_minor
        self.max_major: int = max_major
        self.minor_resync: int = max(0, int(minor_resync))

        self._major_index: int = 0
        self._major_no: str = ""
        self._major_title_parts: List[str] = []
        self._minor_expected: int = 1
        self._current: Optional[RawBlock] = None
        self._pending_lines: List[str] = []
        self._blocks: List[RawBlock] = []
        #: 当前 :meth:`_absorb` 是否还停在「刚喂进来那一行的行首」
        self._at_line_head: bool = False

    # ------------------------------------------------------------ 对外接口
    def feed(self, text: str) -> None:
        """喂入一行（已清洗的段落文本）。

        Args:
            text: 段落文本；空串直接忽略。
        """
        rest = collapse_spaces(text)
        if not rest:
            return
        self._at_line_head = True

        guard = 0
        while rest and guard < 64:
            guard += 1
            if self._major_index >= self.max_major:
                break
            char = cn_number(self._major_index + 1)

            head_end = self._match_major_at_start(rest, char)
            if head_end is not None:
                self._open_major(char)
                rest = rest[head_end:].lstrip()
                continue

            if self.allow_inline_major:
                span = self._find_inline_major(rest, char)
                if span is not None:
                    self._absorb(rest[: span[0]])
                    self._open_major(char)
                    rest = rest[span[1]:].lstrip()
                    continue
            break

        if rest:
            self._absorb(rest)

    def finish(self) -> List[RawBlock]:
        """收尾并返回全部题块。

        Returns:
            题块列表（已剔除空块）。
        """
        self._close_major()
        return [b for b in self._blocks if b.text]

    def reset(self) -> None:
        """重置状态机（换一份卷子时调用），已产出的题块会被清空。"""
        self._close_major()
        self._major_index = 0
        self._major_no = ""
        self._major_title_parts = []
        self._minor_expected = 1
        self._current = None
        self._pending_lines = []
        self._blocks = []
        self._at_line_head = False

    # ------------------------------------------------------------ 大题识别
    def _match_major_at_start(self, text: str, char: str) -> Optional[int]:
        """判断 ``text`` 是否以「下一个大题号」开头。

        Args:
            text: 待判定文本。
            char: 期望的大题号中文字符。

        Returns:
            命中时返回「标题正文的起始下标」，否则 ``None``。
        """
        if not text.startswith(char):
            return None
        pos = len(char)
        # 跳过空格
        while pos < len(text) and text[pos] == " ":
            pos += 1
        if pos < len(text) and text[pos] in _MAJOR_SEPARATORS:
            pos += 1
            while pos < len(text) and text[pos] == " ":
                pos += 1
            return pos
        if self.require_major_separator:
            return None
        return self._match_major_without_separator(text, pos)

    def _match_major_without_separator(self, text: str, pos: int) -> Optional[int]:
        """缺分隔符时的宽松匹配（青岛版专用）。

        为避免把 ``一共有多少人?`` 误判成大题一，加了三道约束：
            1. 紧跟的字不在「常见词首字」黑名单里；
            2. 紧跟的字必须是中文（不能是数字/字母）；
            3. 标题要么含大题关键词，要么是「短句 + 句末标点」。

        Args:
            text: 待判定文本。
            pos: 大题号之后的下标。

        Returns:
            命中时返回标题正文起始下标，否则 ``None``。
        """
        if pos >= len(text):
            return None
        nxt = text[pos]
        if nxt in _MAJOR_FALSE_FRIENDS:
            return None
        if not ("\u4e00" <= nxt <= "\u9fff"):
            return None
        body = text[pos:]
        if any(word in body for word in _MAJOR_HINT_WORDS):
            return pos
        if len(body) <= 80 and body[-1] in _MAJOR_TAIL_PUNCT:
            return pos
        return None

    def _find_inline_major(self, text: str, char: str) -> Optional[tuple]:
        """在段落中部找「下一个大题号 + 分隔符」。

        内联位置**必须**带分隔符，且前一个字符是句末标点或空格，
        否则一律不认（宁可少切，不可错切）。

        Args:
            text: 待扫描文本。
            char: 期望的大题号中文字符。

        Returns:
            ``(start, end)`` 下标区间，或 ``None``。
        """
        pattern = re.compile(re.escape(char) + r"\s*[" + re.escape(_MAJOR_SEPARATORS) + r"]\s*")
        for match in pattern.finditer(text):
            start = match.start()
            if start == 0:
                continue
            prev = text[start - 1]
            if prev in "。！？?!；;： )）\t":
                return (start, match.end())
        return None

    # ------------------------------------------------------------ 小题识别
    def _find_minor(self, text: str, number: int) -> Optional[tuple]:
        """在文本里找「期望的下一个小题号」。

        Args:
            text: 待扫描文本。
            number: 期望的小题号。

        Returns:
            ``(start, end)`` 下标区间，或 ``None``。
        """
        # 排除 (1) / （1） 这种子问号——它们属于同一道题的分小问
        prefix = r"(?<![(（])" if self.loose_minor else r"(?<![(（0-9A-Za-z.．])"
        pattern = re.compile(
            prefix + str(number) + r"\s*[" + re.escape(_MINOR_SEPARATORS) + r"]\s*"
        )
        for match in pattern.finditer(text):
            start, end = match.start(), match.end()
            if start > 0 and not self.loose_minor:
                prev = text[start - 1]
                if prev not in " 　\t。！？?!；;：:)）,，、":
                    continue
            if end >= len(text) and not (start == 0 and is_standalone_minor(text)):
                # 编号落在行尾有两种情况，必须分开处理（否则会误杀真小题）：
                #   a) **独占一行的小题号**（整行就是 ``1.`` / ``2．``）——青岛版部分单元
                #      把小题号单独排成一段，这是真正的小题触发点，必须切；
                #   b) 长行**末尾**孤零零挂着的编号——多半是页码/上一题答案的残留，不能切。
                # 纯数字页码行（``37``）压根不带题号后缀，匹配不上本模式，天然被排除。
                continue
            return (start, end)
        return None

    def _find_minor_resync(self, text: str) -> Tuple[Optional[tuple], int]:
        """找下一个小题号；找不到期望值时按 ``minor_resync`` 做**行首**重同步。

        Args:
            text: 待扫描文本。

        Returns:
            ``((start, end), 命中的小题号)``；都找不到时返回 ``(None, 0)``。
        """
        expected = self._minor_expected
        span = self._find_minor(text, expected)
        if span is not None:
            return span, expected
        # 重同步只在「本行还没被切过」且开关打开时生效
        if self.minor_resync <= 0 or not self._at_line_head:
            return None, 0
        for number in range(expected + 1, expected + self.minor_resync + 1):
            candidate = self._find_minor(text, number)
            # 必须命中在行首：这是排版意义上的「新答案条目」，误报率最低
            if candidate is not None and candidate[0] == 0:
                return candidate, number
        return None, 0

    # ------------------------------------------------------------ 内部状态
    def _absorb(self, segment: str) -> None:
        """把一段文本按小题号切开并归位。

        Args:
            segment: 待处理文本。
        """
        rest = segment.strip()
        guard = 0
        while rest and guard < 64:
            guard += 1
            span, number = self._find_minor_resync(rest)
            if span is None:
                break
            self._append(rest[: span[0]])
            self._open_minor(str(number))
            rest = rest[span[1]:]
            self._at_line_head = False
        if rest:
            self._append(rest)
        self._at_line_head = False

    def _append(self, chunk: str) -> None:
        """把文本追加到当前题块（或大题标题缓冲）。

        Args:
            chunk: 文本片段。
        """
        text = collapse_spaces(chunk)
        if not text:
            return
        if self._major_index == 0:
            return  # 大题还没开始，游离文本（封面/前言）直接丢
        if self._current is None:
            self._major_title_parts.append(text)
            self._pending_lines.append(text)
        else:
            self._current.lines.append(text)

    def _open_major(self, char: str) -> None:
        """开一个新大题。

        Args:
            char: 大题号中文字符。
        """
        self._close_major()
        self._major_index += 1
        self._major_no = char
        self._major_title_parts = []
        self._pending_lines = []
        self._minor_expected = 1
        self._current = None

    def _open_minor(self, minor_no: str) -> None:
        """开一个新小题。

        Args:
            minor_no: 小题号字符串。
        """
        if self._major_index == 0:
            # 单元内容直接以小题（1. 2. 3.）开头、前面没有可识别的大题号「一」
            # （如「●细心思考填一填。」「能力训练营」等副标题横幅后紧跟小题）。
            # 此时 _major_index==0，_append 会把内容当游离文本丢弃 → 整单元 0 块。
            # 自动开一个合成大题「一」，让小题有归属、内容不再丢失（答案精确匹配不受影响）。
            self._open_major(cn_number(1))
        if self._current is not None:
            self._blocks.append(self._current)
        self._current = RawBlock(
            major_no=self._major_no,
            major_title=self._major_title(),
            minor_no=minor_no,
            lines=[],
        )
        self._minor_expected = int(minor_no) + 1

    def _close_major(self) -> None:
        """收掉当前大题：落最后一个小题，或把「无小题的大题」整体当一道题。"""
        if self._current is not None:
            self._blocks.append(self._current)
            self._current = None
            self._pending_lines = []
            return
        if self._major_index > 0 and self._pending_lines:
            self._blocks.append(
                RawBlock(
                    major_no=self._major_no,
                    major_title=self._major_title(),
                    minor_no=NO_MINOR,
                    lines=list(self._pending_lines),
                )
            )
        self._pending_lines = []

    def _major_title(self) -> str:
        """当前大题标题（取缓冲里的第一行，最能代表题型）。"""
        return self._major_title_parts[0] if self._major_title_parts else ""


def segment_blocks(
    lines: List[str],
    *,
    require_major_separator: bool = True,
    allow_inline_major: bool = True,
    loose_minor: bool = False,
    max_major: int = 15,
    minor_resync: int = 0,
) -> List[RawBlock]:
    """一次性把一批行切成题块（便捷封装）。

    Args:
        lines: 已清洗的段落文本列表。
        require_major_separator: 见 :class:`BlockSegmenter`。
        allow_inline_major: 见 :class:`BlockSegmenter`。
        loose_minor: 见 :class:`BlockSegmenter`。
        max_major: 见 :class:`BlockSegmenter`。
        minor_resync: 见 :class:`BlockSegmenter`。

    Returns:
        题块列表。
    """
    segmenter = BlockSegmenter(
        require_major_separator=require_major_separator,
        allow_inline_major=allow_inline_major,
        loose_minor=loose_minor,
        max_major=max_major,
        minor_resync=minor_resync,
    )
    for line in lines:
        segmenter.feed(line)
    return segmenter.finish()


__all__ = [
    "NO_MINOR",
    "PAGE_RESIDUAL_RE",
    "STANDALONE_MINOR_RE",
    "RawBlock",
    "BlockSegmenter",
    "segment_blocks",
    "is_page_residual",
    "is_standalone_minor",
]
