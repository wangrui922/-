"""docx 段落读取器（T05 · T2 解析内核）。

**零新增依赖**：只用标准库 ``zipfile`` + ``xml.etree.ElementTree``，
不引 ``python-docx``（``requirements.txt`` 一行不改）。

原理：
    .docx 是一个 zip 包，正文在 ``word/document.xml``。
    ``w:p``（段落）→ 其下若干 ``w:r``（Run）→ 每个 Run 里的 ``w:t`` 才是文本。
    Word 会把同一句话切成很多 Run（字体/加粗一变就断），所以必须**按段落拼接**。

图片 / 公式对象（``w:drawing`` / ``w:pict`` / ``m:oMath``）直接忽略正文，
仅在段落上打 ``has_drawing`` 标记，供脏数据过滤参考。
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Union
from xml.etree import ElementTree as ET

#: WordprocessingML 主命名空间
NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
#: DrawingML(WordprocessingDrawing) 命名空间
NS_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
#: OfficeMath 命名空间
NS_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"

_P_TAG = f"{{{NS_W}}}p"
_T_TAG = f"{{{NS_W}}}t"
_TAB_TAG = f"{{{NS_W}}}tab"
_BR_TAG = f"{{{NS_W}}}br"
_CR_TAG = f"{{{NS_W}}}cr"
_DRAWING_TAG = f"{{{NS_W}}}drawing"
_PICT_TAG = f"{{{NS_W}}}pict"
_OMATH_TAG = f"{{{NS_M}}}oMath"
_BODY_TAG = f"{{{NS_W}}}body"

#: 正文 XML 在 zip 包内的路径
DOCUMENT_XML = "word/document.xml"


@dataclass(frozen=True)
class DocxParagraph:
    """一个 docx 段落。

    Attributes:
        index: 在文档中的顺序号（从 0 开始，仅计非空段落之外的全部段落）。
        text: 段落纯文本（已拼接全部 Run，制表/换行折成空格）。
        has_drawing: 段落内是否含图片 / 图形 / 公式对象。
    """

    index: int
    text: str
    has_drawing: bool = False


def _iter_paragraph_elements(node: ET.Element) -> Iterator[ET.Element]:
    """深度优先产出全部 ``w:p`` 元素（含表格、文本框内的嵌套段落）。

    Args:
        node: 起始元素。

    Yields:
        ``w:p`` 元素，按文档顺序。
    """
    for child in list(node):
        if child.tag == _P_TAG:
            yield child
            # 文本框（w:txbxContent）里还会嵌套 w:p，继续下钻
            yield from _iter_paragraph_elements(child)
        else:
            yield from _iter_paragraph_elements(child)


def _paragraph_text(paragraph: ET.Element) -> str:
    """抽取单个段落的纯文本。

    嵌套在本段落内部的 ``w:p``（文本框）会被跳过——它们由
    :func:`_iter_paragraph_elements` 单独产出一次，避免文本被重复计入。

    Args:
        paragraph: ``w:p`` 元素。

    Returns:
        拼接后的段落文本（未做清洗）。
    """
    parts: List[str] = []

    def walk(node: ET.Element) -> None:
        for child in list(node):
            tag = child.tag
            if tag == _P_TAG:
                continue  # 嵌套段落交给它自己那一轮
            if tag == _T_TAG:
                parts.append(child.text or "")
            elif tag in (_TAB_TAG, _BR_TAG, _CR_TAG):
                parts.append(" ")
            else:
                walk(child)

    walk(paragraph)
    return "".join(parts)


def _has_drawing(paragraph: ET.Element) -> bool:
    """判断段落内是否含图片 / 图形 / 公式对象。

    Args:
        paragraph: ``w:p`` 元素。

    Returns:
        含对象返回 True。
    """
    for child in paragraph.iter():
        if child.tag in (_DRAWING_TAG, _PICT_TAG, _OMATH_TAG):
            return True
    return False


def read_document_xml(path: Union[str, Path]) -> str:
    """读出 docx 的 ``word/document.xml`` 原文。

    Args:
        path: docx 文件路径。

    Returns:
        XML 文本（UTF-8 解码）。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: 不是合法 zip 包，或包内没有 ``word/document.xml``。
    """
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"docx 文件不存在: {target}")
    try:
        with zipfile.ZipFile(target) as zf:
            names = set(zf.namelist())
            if DOCUMENT_XML not in names:
                raise ValueError(f"docx 包内缺少 {DOCUMENT_XML}: {target.name}")
            return zf.read(DOCUMENT_XML).decode("utf-8", errors="replace")
    except zipfile.BadZipFile as exc:  # pragma: no cover - 依赖损坏文件
        raise ValueError(f"不是合法的 docx(zip) 文件: {target.name}") from exc


def read_paragraphs(path: Union[str, Path], *, keep_empty: bool = False) -> List[DocxParagraph]:
    """读出 docx 的全部段落。

    Args:
        path: docx 文件路径。
        keep_empty: 是否保留空段落；默认丢弃（题库解析用不上）。

    Returns:
        ``DocxParagraph`` 列表，按文档顺序；``index`` 是**过滤后**的连续序号，
        便于定位调试。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: 文件格式非法。
    """
    xml_text = read_document_xml(path)
    root = ET.fromstring(xml_text)
    body: Optional[ET.Element] = root.find(_BODY_TAG)
    scope: ET.Element = body if body is not None else root

    result: List[DocxParagraph] = []
    for element in _iter_paragraph_elements(scope):
        raw = _paragraph_text(element).strip()
        if not raw and not keep_empty:
            continue
        result.append(
            DocxParagraph(index=len(result), text=raw, has_drawing=_has_drawing(element))
        )
    return result


def read_texts(path: Union[str, Path]) -> List[str]:
    """只要段落文本的便捷版。

    Args:
        path: docx 文件路径。

    Returns:
        非空段落文本列表。
    """
    return [p.text for p in read_paragraphs(path)]


__all__ = [
    "NS_W",
    "DOCUMENT_XML",
    "DocxParagraph",
    "read_document_xml",
    "read_paragraphs",
    "read_texts",
]
