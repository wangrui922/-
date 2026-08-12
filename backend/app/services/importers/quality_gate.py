"""新题质量闸门（T05 · P1）。

QA 全量扫描发现：docx 解析出的 1154 道新题里约 21.8% 带**结构性污染**
（多题粘连 / 文本框双份抽取 / 讲解文本窜入 / 纯占位符依赖插图）。这类题
本身不是解析回归，而是青岛版版式的通式——短期内无法靠切分内核根治，
但**绝不能让学生刷到**。

对策：在落库时给每道新题定性，只有 ``clean`` 的题才进推荐池（``active``），
``review`` 的题落 ``archived`` 待人工复核，并把原因回写到答案解析里。

设计取舍：
    * **只读不改**——本模块只做判定，不改写题干（自动清洗误伤风险远大于收益）；
    * **宁可错杀，不可放过**——命中任一规则即 ``review``。归档是可逆的，
      学生刷到垃圾题是不可逆的；
    * **判定纯函数**——不依赖数据库/上下文，可随时对存量题重算，
      因此原因不必持久化也能恢复。

不纳入本闸门的已知问题：
    * 「大数尾零丢失」（P3）——量小且难与正常数字区分，误杀风险高，留待办。
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional, Tuple

from app.services import judge_engine

#: 判定结果：干净，可直接进推荐池
VERDICT_CLEAN = "clean"

#: 判定结果：疑似污染，归档待人工复核
VERDICT_REVIEW = "review"

#: 题干字数上限（去空白后）。超过基本都是多题粘连。
MAX_CONTENT_CHARS = 250

#: 讲解/答案文本窜入题干的标志词
ANSWER_LEAK_MARKERS: Tuple[str, ...] = ("答案详解", "思路点拨", "答：")

#: token 重复率阈值（``1 - 去重数/总数``）。文本框被抽两遍时整体重复率≈0.5。
MAX_DUPLICATE_RATIO = 0.4

#: 施加重复率规则所需的最小 token 数。
#:
#: 短算式天然重复（``3×8=24 8×3=24`` 的 token 为 3/8/24/8/3/24，重复率 0.5），
#: 若不设下限会把大量正常计算题误杀。双份抽取的文本都很长，设 12 足够区分。
MIN_TOKENS_FOR_DUPLICATE_CHECK = 12

#: 题号标记数量上限（达到即判粘连）
MAX_QUESTION_MARKERS = 3

#: 汉字
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

#: 各类空白
_WHITESPACE_RE = re.compile(r"[\s\u00a0\u3000]+")

#: 题号标记：只认 ``1.`` ``2．`` 这一种「独立小题」形态。
#:
#: 三处刻意的收窄（实测校准，见下）——放宽任何一条都会造成大面积误杀：
#:
#: 1. **不认 ``（1）`` / ``(1)``**：它们是同一道题的分小问，
#:    ``blocks.py::_find_minor`` 本身就用负向先行断言把它们排除在切分之外。
#:    认了会把「换一换。(1)…(2)…(3)…」这类**完整单题**judge 成粘连（实测 194 vs 70）。
#: 2. **不认 ``3、``**：会把数字枚举「把5、6、7、8填入算式」当成 4 个题号。
#: 3. **小数要连空格一起排除**（``(?!\s*\d)``）：PDF 转档会把 ``2.4`` 拆成 ``2. 4``，
#:    ``0. 5x＝0. 4y``、选项 ``A. 6. 28 B. 12. 56`` 都会被误判成题号。
_QUESTION_MARKER_RE = re.compile(r"\d{1,2}\s*[.．](?!\s*\d)")

#: 占位符/标点/数字（纯占位题依赖插图，脱离原书无法作答）
_PLACEHOLDER_RE = re.compile(r"[□○()＝=\s\d]")

#: 占位形状（□/○）：含此形状且描述性汉字极少，才算插图依赖题
_PLACEHOLDER_SHAPE_RE = re.compile(r"[□○]")

#: token 切分：按「非汉字且非数字」的连续片段切开
_TOKEN_SPLIT_RE = re.compile(r"[^\u4e00-\u9fff0-9]+")


# ==================================================================== 选择题完整性卡口（P0/P1）
#: 商业落地要求：选择题选项必须正好 A/B/C/D 四键。
REQUIRED_CHOICE_KEYS: frozenset[str] = frozenset({"A", "B", "C", "D"})

#: ``True`` = 严格相等 ``{A,B,C,D}``；``False`` = 子集语义（含额外键如 E 也放过）。
#:
#: 决策 4「不允许合法 3 选项题」的直译。若 T04 全库跑出预期外的归档量、确认
#: 题库存在合法 5 选项题，把本开关置 ``False`` 即切换为 ``⊇`` 语义，无需改其他代码
#: （设计 §5 U1）。
STRICT_ABCD: bool = True


def _coerce_options(options: Any) -> Any:
    """把库里读出的 options 原始值规整成 ``normalize_options`` 可消费的结构。

    SQLite 的 ``JSONType`` 列在裸 ``text()`` 查询下返回的是 JSON 字符串，而 ORM
    读回已是 list/dict。两处调用方（入库卡口用 ORM / 批处理脚本用裸 SQL）形态
    不一，这里统一兜底，保证 ``validate_choice_options`` 是唯一真相源、不各自判断。
    """
    if isinstance(options, str):
        try:
            return json.loads(options)
        except (TypeError, ValueError):
            return None
    if isinstance(options, (bytes, bytearray)):
        try:
            return json.loads(options.decode("utf-8", errors="replace"))
        except (TypeError, ValueError, UnicodeDecodeError):
            return None
    return options


def validate_choice_options(
    options: Any,
    correct_answer: Optional[str] = None,
) -> bool:
    """选择题 options 完整性单点真相（与 ``judge_engine.normalize_options`` 同源）。

    口径（设计 §1.4，宪法条文）：

    ``残缺选择题 ⟺ set(option_keys(options)) != {A,B,C,D}``

    * **严格相等**：``{A,B,C,E}``（4 个键但缺 D）判残缺；``{A,B,C,D,E}``（5 选项）
      在 ``STRICT_ABCD=True`` 时同样判残缺（缺完整的 A-D 才叫完整）。
    * **两个用法，口径不分裂**：
      - 批量归档 / CI 不变式不传 ``correct_answer`` → 纯 key 集合判定（与查实的 312 道严格对齐）；
      - 入库卡口传 ``correct_answer`` → 额外要求答案命中归一化后的键集合（满足 PRD P1.1）。

    Args:
        options: ``Question.options`` 原始值（dict / list / list-of-dict / JSON 串 / None）。
        correct_answer: 可选。传入时额外要求 ``correct_answer`` 落在归一化键集合内
            （入库卡口 P1.1 强制口径）；批量归档不传 → 纯 key 口径。

    Returns:
        合法（完整 A-D 且答案命中）返回 ``True``；否则 ``False``。
    """
    if (options is None) or (not options):
        return False

    keys = set(judge_engine.option_keys(_coerce_options(options)))

    if STRICT_ABCD:
        if keys != REQUIRED_CHOICE_KEYS:
            return False
    else:  # 子集语义：只要含齐 A-D 即放行（允许合法的 5 选项题）
        if not REQUIRED_CHOICE_KEYS.issubset(keys):
            return False

    if correct_answer is not None:
        return (
            judge_engine.extract_choice_key(correct_answer, allowed=tuple(keys)) is not None
        )
    return True


def validate_question_integrity(
    q_type: Optional[str],
    options: Any = None,
    correct_answer: Optional[str] = None,
) -> List[str]:
    """入库完整性卡口（防新增死题）。

    与批处理归档脚本共用 ``judge_engine`` 的解析函数（单点真相），命中任一即判
    ``review``（落 ``archived``），从源头杜绝「缺题型 / 缺答案 / 答案无法解析」的
    死题混进 ``active`` 推荐池。

    判定（与 ``20260812_archive_dead_inventory.py`` 口径一致）：
      * 题型缺失（``q_type`` 为空）→ 无法渲染/筛选；
      * 答案缺失（``correct_answer`` 为空）→ 永远判不了分；
      * 选择题 ``correct_answer`` 抽不出合法键位；
      * 判断题 ``correct_answer`` 无法归一成布尔。

    Args:
        q_type: 题型（``选择`` / ``判断`` / ``填空`` / ``计算`` / ``应用`` / ``图形``）。
        options: 选择题选项（dict / list / None）。
        correct_answer: 标准答案。

    Returns:
        原因列表；空列表代表可入库。
    """
    reasons: List[str] = []
    if not (q_type or "").strip():
        reasons.append("题型缺失")
    if not (correct_answer or "").strip():
        reasons.append("正确答案缺失")
    else:
        t = (q_type or "").strip()
        if t == "选择":
            keys = judge_engine.option_keys(_coerce_options(options))
            if judge_engine.extract_choice_key(
                correct_answer, allowed=tuple(keys) if keys else None
            ) is None:
                reasons.append("选择题正确答案无法解析")
        elif t == "判断":
            if judge_engine.normalize_judge_value(correct_answer) is None:
                reasons.append("判断题正确答案无法解析")
    return reasons


def choice_options_reasons(
    options: Any,
    correct_answer: Optional[str] = None,
) -> List[str]:
    """返回选择题不合法原因列表（空列表代表合法）。供 ``classify_question`` / 日志复用。

    Args:
        options: 同 :func:`validate_choice_options`。
        correct_answer: 可选，传入时额外校验答案是否命中选项键。

    Returns:
        原因字符串列表；合法时返回空列表。
    """
    reasons: List[str] = []
    if options is None or not options:
        return ["选择题无选项"]
    if isinstance(options, str) and not options.strip():
        return ["选择题无选项"]

    keys = set(judge_engine.option_keys(_coerce_options(options)))
    missing = REQUIRED_CHOICE_KEYS - keys
    extra = keys - REQUIRED_CHOICE_KEYS
    if missing:
        reasons.append(f"选择题缺选项键{missing}")
    if extra and STRICT_ABCD:
        reasons.append(f"选择题含非法选项键{extra}")
    if correct_answer is not None and not validate_choice_options(options, correct_answer):
        reasons.append("正确答案未命中任一选项")
    return reasons


def normalize(content: Optional[str]) -> str:
    """去掉全部空白，得到用于计数的紧凑题干。

    Args:
        content: 原始题干。

    Returns:
        去空白后的题干；``None`` 视作空串。
    """
    return _WHITESPACE_RE.sub("", content or "")


def has_chinese(content: Optional[str]) -> bool:
    """题干里是否含汉字。

    ``□○□=□(只)`` 这类纯占位题完全依赖插图，脱离原书无法作答。

    Args:
        content: 原始题干。

    Returns:
        含至少一个汉字返回 ``True``。
    """
    return bool(_CJK_RE.search(content or ""))


def is_placeholder_only(content: Optional[str]) -> bool:
    """题干是否「纯占位」——脱离原书插图无法作答。两类：

    1. 完全无汉字（纯符号/占位，如 ``3+5=``）；
    2. 含占位形状 ``□``/``○`` 且去掉占位符/标点/数字后剩余汉字 ≤ 3
       （如 ``□○□=□(只)``，只含量词「只」）。

    正常短题（如「一、计算。12-5=( )」「列式：3+4=？」）虽汉字少，但
    **不含占位形状**，判为可作答、不误杀。

    Args:
        content: 原始题干。

    Returns:
        占位返回 ``True``（进 review）。
    """
    if not content:
        return True
    if not _CJK_RE.search(content):
        return True  # 完全无汉字
    if _PLACEHOLDER_SHAPE_RE.search(content):
        stripped = _PLACEHOLDER_RE.sub("", content)
        return len(_CJK_RE.findall(stripped)) <= 3
    return False


def tokenize(content: Optional[str]) -> List[str]:
    """把题干切成「汉字串 / 数字串」token。

    Args:
        content: 原始题干。

    Returns:
        token 列表（已剔除空串）。
    """
    return [tok for tok in _TOKEN_SPLIT_RE.split(content or "") if tok]


def duplicate_ratio(content: Optional[str]) -> float:
    """token 重复率 = ``1 - 去重 token 数 / 总 token 数``。

    文本框被 docx 抽取两遍时，每个 token 恰好出现两次，重复率≈0.5。

    Args:
        content: 原始题干。

    Returns:
        ``0.0 ~ 1.0``；token 数不足时返回 ``0.0``。
    """
    tokens = tokenize(content)
    if len(tokens) < MIN_TOKENS_FOR_DUPLICATE_CHECK:
        return 0.0
    return 1.0 - (len(set(tokens)) / len(tokens))


def count_question_markers(content: Optional[str]) -> int:
    """数题干里有几个「独立小题」题号标记（只认 ``1.`` ``2．`` 这种形态）。

    刻意不认 ``（1）`` 分小问与 ``3、`` 枚举，原因见模块顶部
    ``_QUESTION_MARKER_RE`` 注释（放宽任一条都会大面积误杀）。

    Args:
        content: 原始题干。

    Returns:
        题号标记个数。
    """
    return len(_QUESTION_MARKER_RE.findall(content or ""))


def classify_question(
    content: str,
    answer: Optional[str] = None,
    *,
    q_type: Optional[str] = None,
    options: Optional[Any] = None,
) -> Tuple[str, List[str]]:
    """给一道题定性：可上架（``clean``）还是需人工复核（``review``）。

    命中任一规则即 ``review``，并累计**全部**命中原因（不短路），
    方便复核时一眼看全问题。

    Args:
        content: 题干原文。
        answer: 该题答案原文。当前规则只看题干，保留此参数以便后续扩展
            （如「答案与题干重复」判定），传入不影响结果。
        q_type: 可选，题型。传入 ``"选择"`` 且 ``options`` 非空时，追加选择题
            选项完整性卡口（缺 A-D 任一键即 ``review``）。带默认值，**既有调用零改动**。
        options: 可选，选择题选项（dict / list / None）。与 ``q_type`` 配合做卡口。

    Returns:
        ``(判定, 原因列表)``。``clean`` 时原因列表为空。
    """
    reasons: List[str] = []
    text = content or ""
    compact = normalize(text)

    if not compact:
        return VERDICT_REVIEW, ["题干为空"]

    if len(compact) > MAX_CONTENT_CHARS:
        reasons.append(f"题干过长疑似多题粘连（{len(compact)}字）")

    leaked = [mark for mark in ANSWER_LEAK_MARKERS if mark in text]
    if leaked:
        reasons.append("题干混入答案/讲解文本（" + "、".join(leaked) + "）")

    if is_placeholder_only(text):
        reasons.append("题干纯占位依赖插图")

    ratio = duplicate_ratio(text)
    if ratio >= MAX_DUPLICATE_RATIO:
        reasons.append(f"疑似文本框双份抽取（重复率{ratio:.0%}）")

    markers = count_question_markers(text)
    if markers >= MAX_QUESTION_MARKERS:
        reasons.append(f"题干含多个题号疑似粘连（{markers}个）")

    # 选择题硬性卡口（P1 入库卡口）：与批处理轨共用同一口径（不传答案，纯 key 判定）。
    # 命中即 ``review``，绝不自动改写 options。
    if q_type == "选择" and options is not None:
        reasons.extend(choice_options_reasons(options, None))

    if reasons:
        return VERDICT_REVIEW, reasons
    return VERDICT_CLEAN, []


def is_clean(content: str, answer: Optional[str] = None) -> bool:
    """便捷判定：这道题能不能直接进推荐池。

    Args:
        content: 题干原文。
        answer: 答案原文。

    Returns:
        ``clean`` 返回 ``True``。
    """
    verdict, _reasons = classify_question(content, answer)
    return verdict == VERDICT_CLEAN


__all__ = [
    "VERDICT_CLEAN",
    "VERDICT_REVIEW",
    "MAX_CONTENT_CHARS",
    "ANSWER_LEAK_MARKERS",
    "MAX_DUPLICATE_RATIO",
    "MIN_TOKENS_FOR_DUPLICATE_CHECK",
    "MAX_QUESTION_MARKERS",
    "REQUIRED_CHOICE_KEYS",
    "STRICT_ABCD",
    "validate_choice_options",
    "validate_question_integrity",
    "choice_options_reasons",
    "normalize",
    "has_chinese",
    "is_placeholder_only",
    "tokenize",
    "duplicate_ratio",
    "count_question_markers",
    "classify_question",
    "is_clean",
]
