"""L1 规则判分引擎（题库消费层 · B2）。

定位
----
**纯函数模块**：不碰数据库、不碰 AI、不抛业务异常，输入题型 / 选项 / 标准答案 /
学生作答，输出 ``JudgeOutcome``。这样它既能被 ``practice_service`` 直接调用，
也能被单测无夹具地覆盖。

判分口径（C7 判分锁 L1）
------------------------
MVP 只判两种题型，且只在 **L1 安全池** 内抽题。

⚠️ **L1 安全池的唯一真相源是本模块的 ``is_l1_safe()``**（配套 ``is_compound()``）。
历史上这个池子的规模在文档里被人肉重算过四轮（595→591→589→594），根因是
「口径散落在临时脚本里、没有单一真相源」。因此本模块只描述**口径**、不写死数字：
抽题层（``practice_service._candidate_rows``）与不变式测试都必须调用
``is_l1_safe()``，规则一改，两边同时生效，数字自动派生。

两种受支持题型的判法：

* ``选择``：从标准答案与学生作答里各抽出一个 ``A~Z`` 键位再比对。
  抽取规则两条并集——

    1. ``选\\s*([A-Z])``  命中「选B」「答案：选 C」这类中文表述；
    2. ``^\\s*([A-Z])[\\s．.、)）:：]?``  命中「B」「B．」「C)」这类裸键位。

  ``options`` 支持 **dict / list 双形态**（见 ``normalize_options``）。

* ``判断``：把两边都归一到布尔——
  ``√ ✔ ✓ 对 正确 T true yes 是`` → True；
  ``× ✗ x X 错 错误 F false no 否`` → False。

兜底（不误判优先）
------------------
任何一步解析不出确定结论（题库脏数据、选项缺失、答案含多小问如
``(1)× (2)√``、学生作答无法识别），一律返回 ``level=4``、``is_correct=False``，
文案说明「无法自动判定，已记录」。**宁可记为待人工，也不猜对错**。

⚠️ ``level=4`` 时 ``is_correct`` 恒为 ``False``，这是接口契约里写死的
（见设计 §③ 接口2 的脏数据兜底示例），下游 ``finish`` 会把它当错题入本。

判分文案的 no-leak 契约（V1 铁律 / Q3）
--------------------------------------
Q3 拍板的口径是「**默认不给答案**，主按钮是让老师带我想」——想看标准答案必须
显式走 ``/reveal``。因此 ``JudgeOutcome`` 把说明拆成**两个受众互斥**的字段：
``reason``（对外，**全分支**都绝不含答案）与 ``audit_reason``（对内，含答案供排查）。

⚠️ **不变量（写在代码里，不只写在测试里）**：``reason`` 对外、在**任何分支**
（答对 / 答错 / L4 兜底 / 不支持题型）下都**不含标准答案**；一旦需要带答案的完整说明
（键位 / ``√`` ``×`` / 标准选项正文），一律走 ``audit_reason``，且**只进日志 / 审计，
绝不进 ``to_dict()``、绝不进 API 响应体**。该不变量由
``tests/test_practice.py::TestAnswerNoLeak`` **无条件**守卫（覆盖全分支，无答对豁免）。
详见 ``JudgeOutcome`` 的 docstring。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

# ------------------------------------------------------------------ 常量
#: 规则判分（L1）
LEVEL_RULE: int = 1
#: 兜底：无法自动判定（L4）
LEVEL_FALLBACK: int = 4

#: 选择题题型名
TYPE_CHOICE: str = "选择"
#: 判断题题型名
TYPE_JUDGE: str = "判断"
#: MVP 支持自动判分的题型（= L1 安全池的题型口径）
SUPPORTED_TYPES: tuple[str, ...] = (TYPE_CHOICE, TYPE_JUDGE)

#: list 形态 options 的索引 → 键位映射表
INDEX_KEYS: tuple[str, ...] = ("A", "B", "C", "D", "E", "F", "G", "H")

#: 「选X」式表述
_CHOICE_PREFIXED_RE = re.compile(r"选\s*([A-Za-z])")
#: 裸键位「B」「B．」「C)」「D、」
_CHOICE_BARE_RE = re.compile(r"^\s*([A-Za-z])\s*[．.、)）:：,，]?\s*$")
#: 行首键位「B. 12 米」这类「键位 + 分隔符 + 文本」
_CHOICE_LEADING_RE = re.compile(r"^\s*([A-Za-z])\s*[．.、)）:：]\s*\S")

#: 判断题「对」的表述
_TRUE_TOKENS: frozenset[str] = frozenset(
    {"√", "✔", "✓", "对", "正确", "对的", "是", "t", "true", "y", "yes", "1"}
)
#: 判断题「错」的表述
_FALSE_TOKENS: frozenset[str] = frozenset(
    {"×", "✗", "x", "✘", "错", "错误", "错的", "否", "不对", "f", "false", "n", "no", "0"}
)

#: 判断题归一时需要剥掉的装饰字符
_JUDGE_STRIP_CHARS: str = " \t\r\n。，,．.、;；:：()（）[]【】\"'“”‘’"

#: 多小问编号标记：``(1)`` ``（2）`` ``(3）``（半/全角数字都算）
_SUBQUESTION_RE = re.compile(r"[(（]\s*[1-9１-９][0-9０-９]*\s*[)）]")
#: 一道题被判为「复合多小问」的编号出现次数阈值
COMPOUND_MARK_THRESHOLD: int = 2

#: 判断题「对」在引导语里的泄题符号
_LEAK_TRUE_SYMBOLS: tuple[str, ...] = ("√", "✔", "✓")
#: 判断题「错」在引导语里的泄题符号
_LEAK_FALSE_SYMBOLS: tuple[str, ...] = ("×", "✗", "✘")
#: 判断题「对」的泄题词
_LEAK_TRUE_WORDS: tuple[str, ...] = ("正确", "对的", "是对", "成立")
#: 判断题「错」的泄题词
_LEAK_FALSE_WORDS: tuple[str, ...] = ("错误", "错的", "是错", "不对", "不成立")
#: 判定式引导词：这些词后面近距离出现结论词，才算「公布答案」
_LEAK_VERDICT_LEAD: str = r"(?:答案|正确答案|结论|所以|因此|应该|应当|正解)"
#: 选项正文被原样抄进引导语时，至少要这么长才算泄题（避免 "7" 这类单字符误伤）
MIN_LEAK_OPTION_TEXT: int = 2

#: 选择题答错时的**对外**文案（Q3：不公布答案，把孩子引向「让老师带我想」）。
#: ⚠️ 刻意不含任何拉丁字母 —— 这样它在结构上就不可能带出 A/B/C/D 键位。
CHOICE_WRONG_REPLY: str = "这道选错了，我们一起看看是哪一步想岔了"
#: 判断题答错时的**对外**文案。
#: ⚠️ 刻意不含 √ / × 符号，也不含「正确 / 错误」这类结论词。
JUDGE_WRONG_REPLY: str = "判断反了，我们一起把题目再捋一遍"

#: 选择题**答对**时的对外文案（与 ``CHOICE_WRONG_REPLY`` 并列）。
#: ⚠️ 刻意不点名键位（不写「你选的 B 是对的」）—— 全分支 no-leak 不变量要求
#: ``reason`` 任何分支都不得带答案；键位只进 ``audit_reason``。
CHOICE_CORRECT_REPLY: str = "答对了，你选的这项就是正确答案"
#: 判断题**答对**时的对外文案（与 ``JUDGE_WRONG_REPLY`` 并列）。
#: ⚠️ 刻意不念 ``√`` / ``×`` 符号，也不写「正确 / 错误」结论词。
JUDGE_CORRECT_REPLY: str = "判断准确，这道你想清楚了"


# ------------------------------------------------------------------ 数据结构
@dataclass(frozen=True)
class OptionItem:
    """归一化后的单个选项。

    Attributes:
        key: 键位，恒为大写单字母（``A``/``B``/``C``/``D`` ...）。
        text: 选项正文（已去首尾空白，可能为空串）。
    """

    key: str
    text: str

    def to_dict(self) -> Dict[str, str]:
        """转成接口出参用的普通 dict（snake_case）。"""
        return {"key": self.key, "text": self.text}


@dataclass(frozen=True)
class JudgeOutcome:
    """一次判分的结论。

    ⚠️ **两个说明字段受众互斥，不要混用**：

    * ``reason`` —— **对外**，直接展示给孩子。**任何情况下都不得包含标准答案**
      （选项键位 / ``√``/``×`` / 标准选项正文）。答错时它只负责「点出这题错了」
      并把孩子自然引向「让老师带我想」，**不公布结论**。
    * ``audit_reason`` —— **对内**，完整判分依据（含标准答案），供日志 / 审计 /
      排查判分口径对不对。**绝不能进 API 响应体**（见 ``to_dict``）。

    为什么要拆：这两件事原先挤在 ``reason`` 一个字段里，docstring 写的是
    「直接透给前端与日志」—— 一个字段同时服务两个互斥受众，必然按最宽的那个
    来写。结果 ``/answer`` 答错时把 ``标准答案是 B`` 明文吐给了前端，F12 一开
    就绕过了 Q3「默认不给答案」的产品口径。前端不回显只是第一层，接口本身不吐
    才是纵深防御。想看答案必须显式走 ``/reveal``（且只对做错的题开放）。

    Attributes:
        is_correct: 是否判对；``level=4`` 时恒为 ``False``。
        level: ``1`` = 规则判分（L1）；``4`` = 无法自动判定（L4 兜底）。
        reason: 对外文案，**不含标准答案**。
        audit_reason: 对内完整说明，**可含标准答案**；留空时自动回落为
            ``reason``，因此它恒为非空。答对 / 答错分支都显式传第四个参数
            （带答案，供排查「判分判得对不对」）；只有 L4 兜底这类无答案可带的
            分支才靠回落，调用方不必关心第四个。
    """

    is_correct: bool
    level: int
    reason: str
    audit_reason: str = ""

    def __post_init__(self) -> None:
        """``audit_reason`` 留空时回落为 ``reason``（frozen dataclass 的标准写法）。"""
        if not self.audit_reason:
            object.__setattr__(self, "audit_reason", self.reason)

    @property
    def is_fallback(self) -> bool:
        """是否落到了 L4 兜底。"""
        return self.level == LEVEL_FALLBACK

    def to_dict(self) -> Dict[str, Any]:
        """转成**对外**接口出参用的普通 dict（snake_case）。

        ⚠️ 刻意**不含** ``audit_reason``：``POST /api/practice/answer`` 路由是把
        本方法的返回值原样塞进响应体的（``app/api/practice.py``），这里每多带一个
        字段就是一次潜在泄题。审计信息请从 ``outcome.audit_reason`` 单独取，进日志。
        """
        return {"is_correct": self.is_correct, "level": self.level, "reason": self.reason}


# ------------------------------------------------------------------ 选项归一
def _clean_text(value: Any) -> str:
    """任意值 → 去首尾空白的字符串（``None`` → 空串）。"""
    if value is None:
        return ""
    return str(value).strip()


def _normalize_key(raw: Any) -> Optional[str]:
    """把候选键位归一成单个大写字母；不合法返回 ``None``。"""
    text = _clean_text(raw)
    if not text:
        return None
    # 允许 "A" / "a" / "A." / "(A)" 这类带装饰的键位
    match = re.search(r"[A-Za-z]", text)
    if match is None:
        return None
    # 仅当整体足够短时才认为它是键位而不是一整句话
    if len(text) > 4:
        return None
    return match.group(0).upper()


def _option_from_mapping(item: Dict[str, Any], index: int) -> Optional[OptionItem]:
    """从 ``{"key": "A", "text": "..."}`` 形态的元素造 ``OptionItem``。"""
    key = _normalize_key(item.get("key") or item.get("label") or item.get("option"))
    if key is None:
        key = INDEX_KEYS[index] if index < len(INDEX_KEYS) else None
    if key is None:
        return None
    text = _clean_text(item.get("text") or item.get("value") or item.get("content"))
    return OptionItem(key=key, text=text)


def normalize_options(options: Any) -> List[OptionItem]:
    """把题库里的 ``Question.options`` 归一成 ``[OptionItem]``。

    支持四种真实形态：

    1. ``{"A": "12 米", "B": "15 米"}``          —— dict 键位映射；
    2. ``["12 米", "15 米"]``                    —— list 按下标映射 A/B/C/D；
    3. ``[{"key": "A", "text": "12 米"}, ...]``  —— list of dict；
    4. ``["A. 12 米", "B. 15 米"]``              —— list 且元素自带键位前缀。

    Args:
        options: ``Question.options`` 原始值（dict / list / None / 其它）。

    Returns:
        归一化后的选项列表，按键位字典序升序；无法解析时返回空列表。
    """
    if not options:
        return []

    items: List[OptionItem] = []

    if isinstance(options, dict):
        for raw_key, raw_text in options.items():
            key = _normalize_key(raw_key)
            if key is None:
                continue
            items.append(OptionItem(key=key, text=_clean_text(raw_text)))
    elif isinstance(options, (list, tuple)):
        for index, raw in enumerate(options):
            if isinstance(raw, dict):
                built = _option_from_mapping(raw, index)
                if built is not None:
                    items.append(built)
                continue
            text = _clean_text(raw)
            if not text:
                continue
            leading = _CHOICE_LEADING_RE.match(text)
            if leading is not None:
                key = leading.group(1).upper()
                body = text[leading.end(1):].lstrip(" .．、)）:：").strip()
                items.append(OptionItem(key=key, text=body))
                continue
            if index < len(INDEX_KEYS):
                items.append(OptionItem(key=INDEX_KEYS[index], text=text))
    else:
        return []

    # 去重（同一键位只留第一次出现），再按键位排序，保证前端渲染顺序稳定
    seen: Dict[str, OptionItem] = {}
    for item in items:
        if item.key not in seen:
            seen[item.key] = item
    return [seen[key] for key in sorted(seen)]


def option_keys(options: Any) -> List[str]:
    """取归一化后的键位列表（便于校验标准答案是否落在选项内）。"""
    return [item.key for item in normalize_options(options)]


# ------------------------------------------------------------------ 键位抽取
def extract_choice_key(raw: Any, *, allowed: Optional[Sequence[str]] = None) -> Optional[str]:
    """从一段文本里抽出唯一的选项键位。

    抽取顺序：``选X`` → 裸键位 → ``X.`` 前缀。抽到**多个不同**键位时判为歧义，
    返回 ``None``（交给 L4 兜底，不猜）。

    Args:
        raw: 标准答案或学生作答文本。
        allowed: 允许的键位白名单（通常是该题的选项键位）；``None`` 表示不校验。

    Returns:
        大写单字母键位；无法唯一确定时返回 ``None``。
    """
    text = _clean_text(raw)
    if not text:
        return None

    found: List[str] = [m.group(1).upper() for m in _CHOICE_PREFIXED_RE.finditer(text)]

    if not found:
        bare = _CHOICE_BARE_RE.match(text)
        if bare is not None:
            found = [bare.group(1).upper()]

    if not found:
        leading = _CHOICE_LEADING_RE.match(text)
        if leading is not None:
            found = [leading.group(1).upper()]

    if not found and allowed:
        # 末位兜底：整段文本里只出现了唯一一个合法键位（如「答案：B」）。
        # 必须有白名单约束，否则任何含字母的句子都会被误判成选项。
        letters = {ch.upper() for ch in text if ch.isalpha() and ch.isascii()}
        hits = letters & set(allowed)
        if len(letters) == 1 and len(hits) == 1:
            found = [next(iter(hits))]

    if not found:
        return None

    unique = {key for key in found}
    if len(unique) != 1:
        # 形如「(1)选A (2)选B」的复合题：不判，兜底 L4
        return None

    key = found[0]
    if allowed is not None and key not in set(allowed):
        return None
    return key


def normalize_judge_value(raw: Any) -> Optional[bool]:
    """把判断题的表述归一成布尔。

    Args:
        raw: ``√`` / ``对`` / ``×`` / ``错误`` / ``T`` / ``false`` 等。

    Returns:
        ``True`` / ``False``；无法唯一确定时返回 ``None``（脏数据兜底）。
    """
    text = _clean_text(raw)
    if not text:
        return None

    stripped = text.strip(_JUDGE_STRIP_CHARS).strip()
    if not stripped:
        # 全是装饰字符时退回原文再试一次（如 "（√）"）
        stripped = text

    lowered = stripped.lower()
    if lowered in _TRUE_TOKENS:
        return True
    if lowered in _FALSE_TOKENS:
        return False

    # 单字符符号（可能夹在别的字符里，如 "答案：√"）。
    # ⚠️ 必须「有且仅有一个」符号才敢判：题库里存在
    # ``(1)× (2)√ ×1...`` 这类把多小问答案挤在一格的脏数据，
    # 多符号一律判为歧义 → 交给 L4 兜底，绝不猜。
    hits_true = [ch for ch in stripped if ch in {"√", "✔", "✓"}]
    hits_false = [ch for ch in stripped if ch in {"×", "✗", "✘"}]
    if len(hits_true) == 1 and not hits_false:
        return True
    if len(hits_false) == 1 and not hits_true:
        return False

    return None


# ------------------------------------------------------------------ 主入口
def _judge_choice(
    options: Any,
    correct_answer: Optional[str],
    selected_answer: Optional[str],
) -> JudgeOutcome:
    """选择题判分。"""
    items = normalize_options(options)
    if not items:
        return JudgeOutcome(False, LEVEL_FALLBACK, "这道题的选项数据缺失，已记录，稍后由老师确认")

    keys = [item.key for item in items]
    std_key = extract_choice_key(correct_answer, allowed=keys)
    if std_key is None:
        return JudgeOutcome(False, LEVEL_FALLBACK, "这道题的标准答案无法自动识别，已记录")

    user_key = extract_choice_key(selected_answer, allowed=keys)
    if user_key is None:
        # 学生可能直接抄了选项正文，再按正文精确匹配一次
        typed = _clean_text(selected_answer)
        matched = [item.key for item in items if item.text and item.text == typed]
        if len(matched) == 1:
            user_key = matched[0]
    if user_key is None:
        return JudgeOutcome(False, LEVEL_FALLBACK, "你的作答无法自动判定，已记录")

    if user_key == std_key:
        # 答对了：对外文案不点名键位（全分支 no-leak 不变量），完整依据进 audit_reason
        return JudgeOutcome(
            True,
            LEVEL_RULE,
            CHOICE_CORRECT_REPLY,
            audit_reason=f"选择题判对：学生选 {user_key}，标准答案 {std_key}",
        )
    # 答错了：对外只说「错了，一起看看」，标准答案退到 audit_reason（只进日志）
    return JudgeOutcome(
        False,
        LEVEL_RULE,
        CHOICE_WRONG_REPLY,
        audit_reason=f"选择题判错：学生选 {user_key}，标准答案 {std_key}",
    )


def _judge_label(value: bool) -> str:
    """判断题布尔 → 人类可读标签（仅用于对内审计文案与答对回显）。"""
    return "√（对）" if value else "×（错）"


def _judge_true_false(
    correct_answer: Optional[str],
    selected_answer: Optional[str],
) -> JudgeOutcome:
    """判断题判分。"""
    std = normalize_judge_value(correct_answer)
    if std is None:
        return JudgeOutcome(False, LEVEL_FALLBACK, "这道题的标准答案无法自动识别，已记录")

    user = normalize_judge_value(selected_answer)
    if user is None:
        return JudgeOutcome(False, LEVEL_FALLBACK, "你的作答无法自动判定，已记录")

    std_label = _judge_label(std)
    if user == std:
        # 答对了：对外文案不念 √/× 符号（全分支 no-leak 不变量），完整依据进 audit_reason
        return JudgeOutcome(
            True,
            LEVEL_RULE,
            JUDGE_CORRECT_REPLY,
            audit_reason=f"判断题判对：学生答 {_judge_label(user)}，标准答案 {std_label}",
        )
    # 答错了：对外只说「判反了，一起捋」，标准答案退到 audit_reason（只进日志）
    return JudgeOutcome(
        False,
        LEVEL_RULE,
        JUDGE_WRONG_REPLY,
        audit_reason=f"判断题判错：学生答 {_judge_label(user)}，标准答案 {std_label}",
    )


def judge(
    q_type: Optional[str],
    options: Any,
    correct_answer: Optional[str],
    selected_answer: Optional[str],
) -> JudgeOutcome:
    """L1 规则判分主入口。

    Args:
        q_type: 题型（``选择`` / ``判断`` / 其它）。
        options: ``Question.options``（dict / list / None）。
        correct_answer: ``Answer.correct_answer``。
        selected_answer: 学生作答。

    Returns:
        ``JudgeOutcome``；无法自动判定时 ``level=4`` 且 ``is_correct=False``。
    """
    typed = _clean_text(selected_answer)
    if not typed:
        return JudgeOutcome(False, LEVEL_FALLBACK, "还没作答，先选一个答案吧")

    kind = _clean_text(q_type)
    if kind == TYPE_CHOICE:
        return _judge_choice(options, correct_answer, selected_answer)
    if kind == TYPE_JUDGE:
        return _judge_true_false(correct_answer, selected_answer)

    return JudgeOutcome(
        False,
        LEVEL_FALLBACK,
        f"「{kind or '未知题型'}」暂不支持自动判分，已记录，稍后由老师点评",
    )


def is_l1_judgeable(q_type: Optional[str]) -> bool:
    """该题型是否在 MVP 的 L1 自动判分范围内。"""
    return _clean_text(q_type) in SUPPORTED_TYPES


# ------------------------------------------------------------------ L1 安全池口径（唯一真相源）
def is_compound(raw: Any) -> bool:
    """标准答案是否属于「复合多小问」（铁律 C2：这类题禁止进 L1 安全池）。

    判据：文本里 ``(n)`` / ``（n）`` 形式的小问编号出现 **≥2 次**。
    例如 ``'(1)C (2)B'``、``'(1)× ×1（2）√'`` 都是复合题——一格里塞了多小问
    的答案，既没法可靠地自动判分，也没法给孩子逐问反馈。

    Args:
        raw: ``Answer.correct_answer`` 原始值。

    Returns:
        ``True`` 表示是复合多小问题，必须踢出 L1 安全池。
    """
    text = _clean_text(raw)
    if not text:
        return False
    return len(_SUBQUESTION_RE.findall(text)) >= COMPOUND_MARK_THRESHOLD


def _standard_answer_resolvable(kind: str, options: Any, answer: str) -> bool:
    """标准答案本身能不能被判分层唯一解析出来。

    **刻意复用 ``judge()`` 用的那两个解析器**（``extract_choice_key`` /
    ``normalize_judge_value``），把「进池」和「可判分」这两件事焊死在同一段
    代码上——只要解析规则改了，两边同时改，不可能再对不上。

    Args:
        kind: 已归一的题型（``选择`` / ``判断``）。
        options: 选项原始值。
        answer: 已去空白的标准答案。

    Returns:
        ``True`` 表示 L1 能从标准答案里唯一确定正确项。
    """
    if kind == TYPE_CHOICE:
        keys = option_keys(options)
        # 没有可用选项 → 孩子没有 ABCD 可点，且 allowed 白名单为空判分不可靠
        if not keys:
            return False
        # 标准答案必须能唯一抽出一个**落在本题选项内**的键位。
        # 真实脏数据两例：答案 'C' 但选项只有 A/B；答案 'A提示在直角三角形中…'
        # （docx 导入把解析串进了答案格）。这类题孩子无论怎么选都判不出对错。
        return extract_choice_key(answer, allowed=keys) is not None
    if kind == TYPE_JUDGE:
        return normalize_judge_value(answer) is not None
    return False


def is_l1_safe(q_type: Optional[str], options: Any, correct_answer: Optional[str]) -> bool:
    """该题是否进 L1 安全池 —— **全项目唯一真相源**。

    四条闸门（全过才准入）：

    1. **题型受支持**：``选择`` / ``判断``（``is_l1_judgeable``）；
    2. **标准答案非空**：去空白后不为空串；
    3. **非复合多小问**：``is_compound()`` 为 ``False``（铁律 C2）——
       ``'(1)C (2)B'`` 这类一格塞多小问的题注定只能自评，进不了掌握度闭环；
    4. **标准答案可被 L1 唯一解析**（``_standard_answer_resolvable``）：
       选择题要有非空 options **且**标准答案能抽出落在选项内的键位；
       判断题的标准答案要能归一成布尔。

    这四条合起来给出一条**可证明的性质**：

        ``is_l1_safe(...) is True``  ⟺  ``judge(..., 标准答案)`` 必然
        返回 ``level=1`` 且 ``is_correct=True``

    即「**进池 ⟺ 可判分**」。这正是根治口径漂移的关键：池子的规模是规则的
    **派生值**，不再是需要人肉重算、写进文档就会过期的常量
    （历史上它漂过 595→591→589→594 四轮）。

    ⚠️ 不要在别处复写这套口径，也不要把池子规模硬编码进任何 docstring /
    断言。抽题层（``practice_service._candidate_rows``）与全库不变式测试
    （``tests/test_practice.py::TestL1PoolInvariantOnRealBank``）都只调本函数。

    Args:
        q_type: 题型（``Question.type_``）。
        options: 选项（``Question.options``，dict / list / None）。
        correct_answer: 标准答案（``Answer.correct_answer``）。

    Returns:
        ``True`` 表示该题可以进抽题池，并且一定能被 L1 规则判分。
    """
    kind = _clean_text(q_type)
    if kind not in SUPPORTED_TYPES:
        return False

    answer = _clean_text(correct_answer)
    if not answer:
        return False

    if is_compound(answer):
        return False

    return _standard_answer_resolvable(kind, options, answer)


# ------------------------------------------------------------------ C11/C12 引导 no-leak
def _leaks_choice_answer(reply: str, options: Any, correct_answer: Optional[str]) -> bool:
    """选择题：引导语里是否把标准键位 / 标准选项正文抖了出来。"""
    items = normalize_options(options)
    keys = [item.key for item in items]
    std_key = extract_choice_key(correct_answer, allowed=keys) if keys else None
    if std_key is None:
        return False

    key = re.escape(std_key)
    # ``(?![A-Za-z])`` 而不是 ``\b``：中文字符在 Unicode 下也是 \w，
    # 「选B吧」里的 B 与「吧」之间不存在 \b 边界，用负向前瞻才准。
    patterns = (
        rf"选\s*[（(【\[]?\s*{key}\s*[)）】\]]?(?![A-Za-z])",
        rf"{_LEAK_VERDICT_LEAD}[^。！？\n]{{0,8}}?[（(【\[]?\s*{key}\s*[)）】\]]?(?![A-Za-z])",
        rf"[（(【\[]?\s*{key}\s*[)）】\]]?\s*(?:这一?项|选?项)?\s*(?:才)?(?:是|为)\s*(?:正确|对的|答案|正解)",
    )
    for pattern in patterns:
        if re.search(pattern, reply, flags=re.IGNORECASE):
            return True

    # 标准选项正文被原样抄进回复（「所以是 12 米」）：单字符正文不算，避免误伤
    std_text = next((item.text for item in items if item.key == std_key), "")
    if len(std_text) >= MIN_LEAK_OPTION_TEXT and std_text in reply:
        return True

    return False


def _leaks_judge_answer(reply: str, correct_answer: Optional[str]) -> bool:
    """判断题：引导语里是否把 √ / × 结论抖了出来。"""
    std = normalize_judge_value(correct_answer)
    if std is None:
        return False

    symbols = _LEAK_TRUE_SYMBOLS if std else _LEAK_FALSE_SYMBOLS
    # √ / × 在「带着想」的引导语里没有任何正当用途，出现即视为泄题
    if any(symbol in reply for symbol in symbols):
        return True

    words = _LEAK_TRUE_WORDS if std else _LEAK_FALSE_WORDS
    alternation = "|".join(re.escape(word) for word in words)
    pattern = rf"{_LEAK_VERDICT_LEAD}[^。！？\n]{{0,8}}?(?:{alternation})"
    return re.search(pattern, reply) is not None


def leaks_answer(
    reply: Optional[str],
    q_type: Optional[str],
    options: Any,
    correct_answer: Optional[str],
) -> bool:
    """AI 引导语是否**泄露了标准答案**（V1 铁律 / C11+C12 的代码层兜底）。

    「让老师带我想一想」首轮只准带着孩子拆条件、理思路，**不准公布答案**。
    光靠提示词约束不够——模型一旦回一句「答案是 B，因为…」，孩子直接看到
    答案，V1 铁律就破功了。本函数是代码层的最后一道闸门。

    命中口径（保守优先，宁可误杀一条引导语也不放过一次泄题）：

    * **选择题**：出现 ``选B``、``答案是 B``、``B 才是正确的`` 这类明确表述，
      或标准选项正文（≥2 字）被原样抄进回复；
    * **判断题**：出现 ``√``/``×`` 符号，或 ``答案…正确``/``所以…错误``
      这类判定式表述。

    纯函数、不碰数据库、不抛异常，便于单测直接覆盖。

    Args:
        reply: 模型返回的引导语。
        q_type: 题型。
        options: ``Question.options``。
        correct_answer: ``Answer.correct_answer``。

    Returns:
        ``True`` 表示该回复涉嫌泄题，调用方应丢弃并改用引导式兜底话术。
    """
    text = _clean_text(reply)
    if not text:
        return False

    answer = _clean_text(correct_answer)
    if not answer:
        return False

    kind = _clean_text(q_type)
    if kind == TYPE_CHOICE:
        return _leaks_choice_answer(text, options, answer)
    if kind == TYPE_JUDGE:
        return _leaks_judge_answer(text, answer)
    return False


__all__ = [
    "OptionItem",
    "JudgeOutcome",
    "judge",
    "normalize_options",
    "option_keys",
    "extract_choice_key",
    "normalize_judge_value",
    "is_l1_judgeable",
    "is_compound",
    "is_l1_safe",
    "leaks_answer",
    "LEVEL_RULE",
    "LEVEL_FALLBACK",
    "TYPE_CHOICE",
    "TYPE_JUDGE",
    "SUPPORTED_TYPES",
    "INDEX_KEYS",
    "COMPOUND_MARK_THRESHOLD",
    "MIN_LEAK_OPTION_TEXT",
    "CHOICE_WRONG_REPLY",
    "JUDGE_WRONG_REPLY",
    "CHOICE_CORRECT_REPLY",
    "JUDGE_CORRECT_REPLY",
]
