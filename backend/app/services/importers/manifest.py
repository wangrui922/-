"""源文件白名单登记（T05 · T3）。

唯一真相来源：``docs/question-bank/qingdao-docx-manifest.md``。
**只有登记在册的文件才会被导入**——目录里的人教版、版本不明的"专项复习卷"、
以及 3 个字节级重复副本一律不碰。

━━━━━━━━━━━━━━━━━━━━━━━━ U1 年级语义（老板拍板，禁止改动）━━━━━━━━━━━━━━━━━━━━━━━━
``grade`` 存的是**升入年级**：
    青岛版衔接卷：一升二=2、二升三=3、三升四=4、四升五=5、五升六=6
    苏教版下册卷：3下=3、4下=4、5下=5、6下=6
册内的「温故（复习上学期）」与「预习（新课衔接）」用 ``section`` 区分，**不拆年级**。
⚠️ 本文件及下游任何代码都**严禁**出现 ``grade - 1`` 之类的换算。

━━━━━━━━━━━━━━━━━━━━━━━━ R-02 苏教版 A 组配对 ━━━━━━━━━━━━━━━━━━━━━━━━
A 组「正文 / 答案」跨文件，配对关系在此**硬登记**为 ``pair_key = (grade, paper_no)``，
**绝不做文件名模糊匹配、绝不做顺序对齐**。运行时还有三重闸门（见 ``sujiao.py``）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

#: 源目录（本机绝对路径；换机/上云需改这里或用 CLI 的 ``--source-dir``）
DEFAULT_SOURCE_DIR = Path(r"D:\我的文档\Documents\数学题")

#: 解析器标识
PARSER_QINGDAO = "qingdao"
PARSER_SUJIAO_A = "sujiao_a"
PARSER_SUJIAO_B = "sujiao_b"

#: 文件在配对中的角色
ROLE_SINGLE = "single"
ROLE_BODY = "body"
ROLE_ANSWER = "answer"


@dataclass(frozen=True)
class DocxSource:
    """一个登记在册的 docx 源文件。

    Attributes:
        key: 稳定标识（CLI ``--only`` 用）。
        filename: 文件名（不含路径）。
        edition: 教材版本。
        source_type: 来源类型（衔接 / 期末）。
        grade: **升入年级**（见模块 docstring 的 U1）。
        parser: 解析器标识。
        role: 在配对中的角色。
        pair_key: A 组配对键 ``(grade, paper_no)``；非 A 组为 ``None``。
        section: 册内分区默认值。
        label: 人类可读标签（报告里显示）。
    """

    key: str
    filename: str
    edition: str
    source_type: str
    grade: int
    parser: str
    role: str
    pair_key: Optional[Tuple[int, str]]
    section: Optional[str]
    label: str

    def path(self, source_dir: Path = DEFAULT_SOURCE_DIR) -> Path:
        """拼出绝对路径。

        Args:
            source_dir: 源目录。

        Returns:
            文件绝对路径。
        """
        return Path(source_dir) / self.filename


# ==================================================================== 青岛版
#: 青岛版暑假衔接 5 个真身（一升二 → 五升六）。grade = 升入年级。
QINGDAO_SOURCES: Tuple[DocxSource, ...] = (
    DocxSource(
        key="qd-g2",
        filename="实验班暑假衔接一升二数学青岛版h_decrypted.docx",
        edition="青岛版",
        source_type="衔接",
        grade=2,  # 一升二 → 升入 2 年级
        parser=PARSER_QINGDAO,
        role=ROLE_SINGLE,
        pair_key=None,
        section=None,
        label="青岛版·暑假衔接·一升二",
    ),
    DocxSource(
        key="qd-g3",
        filename="实验班暑假衔接二升三数学青岛版h_decrypted.docx",
        edition="青岛版",
        source_type="衔接",
        grade=3,  # 二升三 → 升入 3 年级
        parser=PARSER_QINGDAO,
        role=ROLE_SINGLE,
        pair_key=None,
        section=None,
        label="青岛版·暑假衔接·二升三",
    ),
    DocxSource(
        key="qd-g4",
        filename="实验班暑假衔接三升四数学青岛版h_decrypted.docx",
        edition="青岛版",
        source_type="衔接",
        grade=4,  # 三升四 → 升入 4 年级
        parser=PARSER_QINGDAO,
        role=ROLE_SINGLE,
        pair_key=None,
        section=None,
        label="青岛版·暑假衔接·三升四",
    ),
    DocxSource(
        key="qd-g5",
        filename="实验班暑假衔接四升五数学青岛版h_decrypted.docx",
        edition="青岛版",
        source_type="衔接",
        grade=5,  # 四升五 → 升入 5 年级
        parser=PARSER_QINGDAO,
        role=ROLE_SINGLE,
        pair_key=None,
        section=None,
        label="青岛版·暑假衔接·四升五",
    ),
    DocxSource(
        key="qd-g6",
        filename="实验班暑假衔接五升六数学青岛版h_decrypted.docx",
        edition="青岛版",
        source_type="衔接",
        grade=6,  # 五升六 → 升入 6 年级
        parser=PARSER_QINGDAO,
        role=ROLE_SINGLE,
        pair_key=None,
        section=None,
        label="青岛版·暑假衔接·五升六",
    ),
)

# ==================================================================== 苏教版 A 组
#: 苏教版期末摸底诊断卷（正文 / 答案分离，靠 ``pair_key`` 硬绑）。
SUJIAO_A_SOURCES: Tuple[DocxSource, ...] = (
    DocxSource(
        key="sj-a-g5-p1-body",
        filename="2026年（下册）5年级 数学 苏教版 期末摸底诊断卷（一）正文(1).docx",
        edition="苏教版", source_type="期末", grade=5,
        parser=PARSER_SUJIAO_A, role=ROLE_BODY, pair_key=(5, "一"),
        section="摸底", label="苏教版·5下·期末摸底诊断卷(一)·正文",
    ),
    DocxSource(
        key="sj-a-g5-p1-answer",
        filename="2026年（下册）5年级 数学 苏教版 期末摸底诊断卷（一）答案(1).docx",
        edition="苏教版", source_type="期末", grade=5,
        parser=PARSER_SUJIAO_A, role=ROLE_ANSWER, pair_key=(5, "一"),
        section="摸底", label="苏教版·5下·期末摸底诊断卷(一)·答案",
    ),
    DocxSource(
        key="sj-a-g5-p2-body",
        filename="2026年（下册）5年级 数学 苏教版 期末摸底诊断卷（二）正文(1).docx",
        edition="苏教版", source_type="期末", grade=5,
        parser=PARSER_SUJIAO_A, role=ROLE_BODY, pair_key=(5, "二"),
        section="摸底", label="苏教版·5下·期末摸底诊断卷(二)·正文",
    ),
    DocxSource(
        key="sj-a-g5-p2-answer",
        filename="2026年（下册）5年级 数学 苏教版 期末摸底诊断卷（二）答案(1).docx",
        edition="苏教版", source_type="期末", grade=5,
        parser=PARSER_SUJIAO_A, role=ROLE_ANSWER, pair_key=(5, "二"),
        section="摸底", label="苏教版·5下·期末摸底诊断卷(二)·答案",
    ),
    DocxSource(
        key="sj-a-g6-p1-body",
        filename="2026年（下册）6年级 数学 苏教版 期末摸底诊断卷（一）正文(1).docx",
        edition="苏教版", source_type="期末", grade=6,
        parser=PARSER_SUJIAO_A, role=ROLE_BODY, pair_key=(6, "一"),
        section="摸底", label="苏教版·6下·期末摸底诊断卷(一)·正文",
    ),
    DocxSource(
        key="sj-a-g6-p1-answer",
        filename="2026年（下册）6年级 数学 苏教版 期末摸底诊断卷（一）答案(1).docx",
        edition="苏教版", source_type="期末", grade=6,
        parser=PARSER_SUJIAO_A, role=ROLE_ANSWER, pair_key=(6, "一"),
        section="摸底", label="苏教版·6下·期末摸底诊断卷(一)·答案",
    ),
    DocxSource(
        key="sj-a-g6-p2-body",
        filename="2026年（下册）6年级 数学 苏教版 期末摸底诊断卷（二）正文(1).docx",
        edition="苏教版", source_type="期末", grade=6,
        parser=PARSER_SUJIAO_A, role=ROLE_BODY, pair_key=(6, "二"),
        section="摸底", label="苏教版·6下·期末摸底诊断卷(二)·正文",
    ),
    DocxSource(
        key="sj-a-g6-p2-answer",
        filename="2026年（下册）6年级 数学 苏教版 期末摸底诊断卷（二）答案(1).docx",
        edition="苏教版", source_type="期末", grade=6,
        parser=PARSER_SUJIAO_A, role=ROLE_ANSWER, pair_key=(6, "二"),
        section="摸底", label="苏教版·6下·期末摸底诊断卷(二)·答案",
    ),
)

# ==================================================================== 苏教版 B 组
#: 苏教版期末冲刺专项复习卷（正文 + 答案合一，单文件含 5 套）。
SUJIAO_B_SOURCES: Tuple[DocxSource, ...] = (
    DocxSource(
        key="sj-b-g5",
        filename="苏教五下期末冲刺专项复习卷5套(1).docx",
        edition="苏教版", source_type="期末", grade=5,
        parser=PARSER_SUJIAO_B, role=ROLE_SINGLE, pair_key=None,
        section="专项", label="苏教版·5下·期末冲刺专项复习卷5套",
    ),
    DocxSource(
        key="sj-b-g4",
        filename="苏教四下期末冲刺专项复习卷5套(1).docx",
        edition="苏教版", source_type="期末", grade=4,
        parser=PARSER_SUJIAO_B, role=ROLE_SINGLE, pair_key=None,
        section="专项", label="苏教版·4下·期末冲刺专项复习卷5套",
    ),
    # ---------------------------------------------------------------- 苏教 3 下
    # 文件名不带年级前缀（``专项复习卷N(1).docx``），年级由本清单**硬登记**为 3。
    # ⚠️ 同目录下还有 ``期末复习冲刺卷   专项复习卷N(5)/(6).docx``（苏教 1 下/2 下），
    #    本轮 DEFER，未登记 → 不会被导入。
    DocxSource(
        key="sj-b-g3-p1",
        filename="专项复习卷1(1).docx",
        edition="苏教版", source_type="专项复习", grade=3,
        parser=PARSER_SUJIAO_B, role=ROLE_SINGLE, pair_key=None,
        section="专项", label="苏教版·3下·专项复习卷1",
    ),
    DocxSource(
        key="sj-b-g3-p2",
        filename="专项复习卷2(1).docx",
        edition="苏教版", source_type="专项复习", grade=3,
        parser=PARSER_SUJIAO_B, role=ROLE_SINGLE, pair_key=None,
        section="专项", label="苏教版·3下·专项复习卷2",
    ),
    DocxSource(
        key="sj-b-g3-p3",
        filename="专项复习卷3(1).docx",
        edition="苏教版", source_type="专项复习", grade=3,
        parser=PARSER_SUJIAO_B, role=ROLE_SINGLE, pair_key=None,
        section="专项", label="苏教版·3下·专项复习卷3",
    ),
    DocxSource(
        key="sj-b-g3-p4",
        filename="专项复习卷4(1).docx",
        edition="苏教版", source_type="专项复习", grade=3,
        parser=PARSER_SUJIAO_B, role=ROLE_SINGLE, pair_key=None,
        section="专项", label="苏教版·3下·专项复习卷4",
    ),
    DocxSource(
        key="sj-b-g3-p5",
        filename="专项复习卷5(1).docx",
        edition="苏教版", source_type="专项复习", grade=3,
        parser=PARSER_SUJIAO_B, role=ROLE_SINGLE, pair_key=None,
        section="专项", label="苏教版·3下·专项复习卷5",
    ),
)

#: 全部登记在册的源文件（20 个）
ALL_SOURCES: Tuple[DocxSource, ...] = QINGDAO_SOURCES + SUJIAO_A_SOURCES + SUJIAO_B_SOURCES

#: ❌ 明确排除的字节级重复副本（切勿入库）
EXCLUDED_FILENAMES: Tuple[str, ...] = (
    "实验班暑假衔接一升二数学青岛版h_decrypted-aee1a7974422.docx",
    "实验班暑假衔接一升二数学青岛版h_decrypted-fec7ae76086c.docx",
    "实验班暑假衔接三升四数学青岛版h_decrypted(1).docx",
)


def sources_by_key() -> Dict[str, DocxSource]:
    """按 ``key`` 建索引。

    Returns:
        ``{key: DocxSource}``。
    """
    return {src.key: src for src in ALL_SOURCES}


def sujiao_a_pairs() -> Dict[Tuple[int, str], List[DocxSource]]:
    """按 ``pair_key`` 归拢苏教版 A 组文件。

    Returns:
        ``{(grade, paper_no): [DocxSource, ...]}``。
        正常情况下每个 key 恰好 2 个（1 正文 + 1 答案）；
        数量不对由 ``sujiao.py`` 的闸门①负责拦截。
    """
    grouped: Dict[Tuple[int, str], List[DocxSource]] = {}
    for src in SUJIAO_A_SOURCES:
        if src.pair_key is None:  # pragma: no cover - A 组恒有 pair_key
            continue
        grouped.setdefault(src.pair_key, []).append(src)
    return grouped


def is_excluded(filename: str) -> bool:
    """文件名是否在排除清单里。

    Args:
        filename: 文件名（不含路径）。

    Returns:
        在排除清单里返回 True。
    """
    return filename in EXCLUDED_FILENAMES


def missing_files(source_dir: Path = DEFAULT_SOURCE_DIR) -> List[str]:
    """列出登记在册但磁盘上找不到的文件。

    Args:
        source_dir: 源目录。

    Returns:
        缺失的文件名列表。
    """
    return [src.filename for src in ALL_SOURCES if not src.path(source_dir).exists()]


__all__ = [
    "DEFAULT_SOURCE_DIR",
    "PARSER_QINGDAO",
    "PARSER_SUJIAO_A",
    "PARSER_SUJIAO_B",
    "ROLE_SINGLE",
    "ROLE_BODY",
    "ROLE_ANSWER",
    "DocxSource",
    "QINGDAO_SOURCES",
    "SUJIAO_A_SOURCES",
    "SUJIAO_B_SOURCES",
    "ALL_SOURCES",
    "EXCLUDED_FILENAMES",
    "sources_by_key",
    "sujiao_a_pairs",
    "is_excluded",
    "missing_files",
]
