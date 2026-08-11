"""段落文本清洗与题型判定（T05 · T2 解析内核）。

源 docx 都是 PDF 转出来的，正文里混着大量非题目文本：
    * 配套视频标记：``视频讲答案`` / ``视频预习课`` / ``视频详讲答案``（常连续重复两遍）
    * 页眉页脚：``少而精·期末活页卷  数学  5年级  下册  苏教  第1页  共4页``
    * 页码：``1/38``
    * 目录行：``巩固提升1\t20以内的退位减法/1``
    * 版权页 / 出版社 / 丛书名
本模块负责把这些噪声清掉，并给题块判题型。
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from app.models.content import QUESTION_TYPE_VALUES

# ==================================================================== 中文数字
_CN_DIGITS = "零一二三四五六七八九"


def cn_number(value: int) -> str:
    """阿拉伯数字转中文大题号（支持 1~99）。

    Args:
        value: 正整数。

    Returns:
        中文数字串，如 ``1 -> 一``、``10 -> 十``、``12 -> 十二``、``21 -> 二十一``。

    Raises:
        ValueError: 超出 1~99 范围。
    """
    if not 1 <= value <= 99:
        raise ValueError(f"大题号超出支持范围 1~99: {value}")
    if value < 10:
        return _CN_DIGITS[value]
    if value == 10:
        return "十"
    if value < 20:
        return "十" + _CN_DIGITS[value - 10]
    tens, ones = divmod(value, 10)
    return _CN_DIGITS[tens] + "十" + (_CN_DIGITS[ones] if ones else "")


def parse_cn_number(text: str) -> Optional[int]:
    """中文数字串转阿拉伯数字（支持 1~99），失败返回 ``None``。

    Args:
        text: 中文数字串。

    Returns:
        整数或 ``None``。
    """
    token = text.strip()
    if not token:
        return None
    if token == "十":
        return 10
    if token.startswith("十") and len(token) == 2:
        idx = _CN_DIGITS.find(token[1])
        return 10 + idx if idx > 0 else None
    if len(token) == 1:
        idx = _CN_DIGITS.find(token)
        return idx if idx > 0 else None
    if "十" in token:
        head, _, tail = token.partition("十")
        tens = _CN_DIGITS.find(head)
        if tens <= 0:
            return None
        if not tail:
            return tens * 10
        ones = _CN_DIGITS.find(tail)
        return tens * 10 + ones if ones > 0 else None
    return None


# ==================================================================== 噪声规则
#: 直接从行内抠掉的噪声片段（可重复出现）
INLINE_NOISE_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"视频详讲答案"),
    re.compile(r"视频讲答案"),
    re.compile(r"视频预习课"),
    re.compile(r"扫码(看|听|学|做)?[^，。；]{0,6}"),
    re.compile(r"[（(]\s*[!！]\s*[)）]"),
)

#: 整段丢弃的噪声（页眉页脚 / 版权 / 目录）
DROP_LINE_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"^\d{1,3}\s*/\s*\d{1,3}$"),                       # 页码 1/38
    re.compile(r"^第?\s*\d{1,3}\s*页\s*共\s*\d{1,3}\s*页$"),
    re.compile(r"少而精[·•・]期末活页卷"),
    re.compile(r"^快乐暑假"),
    re.compile(r"江苏人民出版社|人民教育出版社|出版发行|印刷|开本|字数|版次|书号|ISBN", re.I),
    re.compile(r"^《.*》编写组编"),
    re.compile(r"^目\s*录$|^Contents$", re.I),
    re.compile(r"^实验班$|^提优训练$|^暑假衔接$"),
    re.compile(r"^建议用时[：:]"),
    re.compile(r"^[A-Za-z\s]+$"),                                  # 纯英文行（拼音标题）
)

#: 目录行：以 “/页码” 收尾
_TOC_LINE = re.compile(r"/\s*\d{1,3}\s*$")

#: 连续重复的同一短语（“视频讲答案视频讲答案”这类）
_DUP_PHRASE = re.compile(r"(.{2,12}?)\1{1,}")


def collapse_spaces(text: str) -> str:
    """把各类空白折成单个半角空格并去首尾。

    Args:
        text: 原始文本。

    Returns:
        规整后的文本。
    """
    return re.sub(r"[\s\u00a0\u3000]+", " ", text or "").strip()


def dedupe_phrases(text: str) -> str:
    """折叠紧邻重复的短语（PDF 转档常见的双份文字）。

    Args:
        text: 原始文本。

    Returns:
        折叠后的文本。
    """
    previous = ""
    current = text
    # 折叠可能级联（AABB→AB），最多迭代 3 轮防止病态回溯
    for _ in range(3):
        if current == previous:
            break
        previous = current
        current = _DUP_PHRASE.sub(r"\1", current)
    return current


def strip_inline_noise(text: str) -> str:
    """抠掉行内噪声片段。

    Args:
        text: 原始文本。

    Returns:
        清洗后的文本。
    """
    out = text or ""
    for pattern in INLINE_NOISE_PATTERNS:
        out = pattern.sub("", out)
    return out


def is_toc_line(text: str) -> bool:
    """是否是目录行（``… /页码`` 结尾）。

    Args:
        text: 段落文本。

    Returns:
        是目录行返回 True。
    """
    return bool(_TOC_LINE.search(text or ""))


def is_drop_line(text: str) -> bool:
    """是否整段丢弃。

    Args:
        text: 段落文本（建议先过 :func:`strip_inline_noise`）。

    Returns:
        应丢弃返回 True。
    """
    token = (text or "").strip()
    if not token:
        return True
    if len(token) <= 1:
        return True
    for pattern in DROP_LINE_PATTERNS:
        if pattern.search(token):
            return True
    return False


def clean_line(text: str) -> str:
    """完整清洗一行：抠噪声 → 折重复 → 折空白。

    Args:
        text: 原始段落文本。

    Returns:
        清洗后的文本；整段是噪声时返回空串。
    """
    out = strip_inline_noise(text or "")
    out = dedupe_phrases(out)
    out = collapse_spaces(out)
    if is_drop_line(out):
        return ""
    return out


def clean_lines(texts: List[str], *, drop_toc: bool = True) -> List[str]:
    """批量清洗段落。

    Args:
        texts: 原始段落文本列表。
        drop_toc: 是否丢弃目录行。

    Returns:
        清洗后的非空段落列表。
    """
    out: List[str] = []
    for raw in texts:
        if drop_toc and is_toc_line(raw):
            continue
        line = clean_line(raw)
        if line:
            out.append(line)
    return out


# ==================================================================== 题型判定
#: 题型关键词表。**顺序即优先级**，先命中先返回。
#: 取值必须落在 ``QUESTION_TYPE_VALUES``（6 类）之内。
TYPE_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("判断", ("判断", "对的打", "错的打", "对的在括号里打", "打“√”", "打√", "√”“×", "正确的画")),
    ("选择", ("选择", "选一选", "将正确答案的序号", "把正确答案的序号", "正确答案的字母", "选出")),
    ("图形", ("操作", "画一画", "画出", "连一连", "连线", "量一量", "折一折", "涂一涂",
              "圈一圈", "摆一摆", "作图", "图形题", "统计图", "对称")),
    ("计算", ("计算", "直接写出得数", "脱式", "解方程", "口算", "笔算", "递等式",
              "算一算", "简算", "竖式", "求未知数")),
    ("应用", ("解决问题", "应用题", "解答", "列式解答", "走进生活", "实际问题")),
    ("填空", ("填空", "填一填", "填上", "在括号里填", "写出", "填入")),
)


def detect_question_type(*texts: str) -> Optional[str]:
    """按「大题标题 → 题干」的顺序判题型。

    Args:
        *texts: 参与判定的文本，按优先级从高到低传入（通常是大题标题、题干）。

    Returns:
        6 类题型之一；判不出来返回 ``None``。
    """
    for text in texts:
        token = text or ""
        if not token:
            continue
        for q_type, keywords in TYPE_KEYWORDS:
            for keyword in keywords:
                if keyword in token:
                    return q_type
    # 兜底：有 A./B./C. 选项的按选择题算
    for text in texts:
        if extract_options(text or ""):
            return "选择"
    return None


#: 选择题选项：``A.xxx  B.xxx``（允许全角点/顿号/括号）
_OPTION_TOKEN = re.compile(r"(?:^|[\s\t])([A-D])\s*[.、．]\s*")


def extract_options(text: str) -> Optional[List[str]]:
    """从一行里抽取 A/B/C/D 选项。

    Args:
        text: 题干或选项行。

    Returns:
        选项文本列表（不含 ``A.`` 前缀）；不足 2 个选项时返回 ``None``。
    """
    token = text or ""
    matches = list(_OPTION_TOKEN.finditer(token))
    if len(matches) < 2:
        return None
    # 必须从 A 开始且字母连续，否则大概率是误判（如算式里的字母）
    letters = [m.group(1) for m in matches]
    expected = [chr(ord("A") + i) for i in range(len(letters))]
    if letters != expected:
        return None

    options: List[str] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(token)
        options.append(collapse_spaces(token[start:end]))
    return [opt for opt in options if opt] or None


def validate_question_type(q_type: Optional[str]) -> Optional[str]:
    """把题型收敛到白名单内，非法值返回 ``None``。

    Args:
        q_type: 待校验题型。

    Returns:
        合法题型或 ``None``。
    """
    if q_type and q_type in QUESTION_TYPE_VALUES:
        return q_type
    return None


__all__ = [
    "cn_number",
    "parse_cn_number",
    "collapse_spaces",
    "dedupe_phrases",
    "strip_inline_noise",
    "is_toc_line",
    "is_drop_line",
    "clean_line",
    "clean_lines",
    "detect_question_type",
    "extract_options",
    "validate_question_type",
    "TYPE_KEYWORDS",
    "INLINE_NOISE_PATTERNS",
    "DROP_LINE_PATTERNS",
]
