"""Makers 云函数题库逻辑层（serverless 版，纯内存、零外部依赖）。

本模块是本地 ``app.services.judge_engine`` + ``app.services.practice_service``
的 **serverless 移植**：

* 题库来自同目录的 ``bank_data.py``（由 ``scripts/export_makers_bank.py`` 从
  ``dev.db`` 导出），冷启动全部读进内存；
* 判分口径**逐字复用** ``judge_engine``（``normalize_options`` / ``is_l1_safe`` /
  ``leaks_answer`` 等），保证上云判分与本地后端**完全一致的口径**；
* 抽题的 L1 安全池口径同样以 ``is_l1_safe`` 为唯一真相源（bank_data 导出时已过滤，
  这里再复核一遍，双保险）；
* 知识点树 ``rollup`` / ``subtree_ids`` 由 ``KP_NODES`` 重建（与本地 ``kp_tree`` 同算法）。

⚠️ 与本地后端的唯一差异（serverless 限制，已在商业落地文档里写明）：
1. **无逐生历史**：``_recent_question_ids``（回避最近 7 天）在 serverless 里没有
   per-student 持久化，故抽题第 2 档（取消 7 天回避）与第 1 档池子相同，不会真的放宽；
2. **结算不落库**：``finish`` 计算出的错题 / 掌握度**不写持久层**（Python 云函数无
   Blob/DB 写能力）。``mistakes_created`` 仅报告「本会话会建几条」，正式版接 Blob/DB
   后改为幂等建档。进度跨端同步请走 Node 函数 ``progress.js``（Blob）。

模块不依赖 fastapi，可单独单测。
"""
from __future__ import annotations

import json
import logging
import os
import random
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from bank_data import (
    BAND_GRADE_RANGE,
    EDITION_VALUES,
    GRADE_BANDS,
    KP_NODES,
    QUESTIONS,
    SECTION_VALUES,
)

logger = logging.getLogger("makers.bank")

# ===================================================================== 异常
class ApiError(Exception):
    """与本地 ``app.core.response.ApiError`` 对齐：code + msg。"""

    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code
        self.msg = msg


CODE_BAD_REQUEST = 400
CODE_FORBIDDEN = 403
CODE_NOT_FOUND = 404
CODE_SERVER_ERROR = 500


# ===================================================================== 常量（与 judge_engine 一致）
LEVEL_RULE: int = 1
LEVEL_FALLBACK: int = 4

TYPE_CHOICE: str = "选择"
TYPE_JUDGE: str = "判断"
SUPPORTED_TYPES: tuple[str, ...] = (TYPE_CHOICE, TYPE_JUDGE)

INDEX_KEYS: tuple[str, ...] = ("A", "B", "C", "D", "E", "F", "G", "H")

_CHOICE_PREFIXED_RE = __import__("re").compile(r"选\s*([A-Za-z])")
_CHOICE_BARE_RE = __import__("re").compile(r"^\s*([A-Za-z])\s*[．.、)）:：,，]?\s*$")
_CHOICE_LEADING_RE = __import__("re").compile(r"^\s*([A-Za-z])\s*[．.、)）:：]\s*\S")

_TRUE_TOKENS: frozenset[str] = frozenset(
    {"√", "✔", "✓", "对", "正确", "对的", "是", "t", "true", "y", "yes", "1"}
)
_FALSE_TOKENS: frozenset[str] = frozenset(
    {"×", "✗", "x", "✘", "错", "错误", "错的", "否", "不对", "f", "false", "n", "no", "0"}
)
_JUDGE_STRIP_CHARS: str = " \t\r\n。，,．.、;；:：()（）[]【】\"'“”‘’"
_SUBQUESTION_RE = __import__("re").compile(r"[(（]\s*[1-9１-９][0-9０-９]*\s*[)）]")
COMPOUND_MARK_THRESHOLD: int = 2

_LEAK_TRUE_SYMBOLS: tuple[str, ...] = ("√", "✔", "✓")
_LEAK_FALSE_SYMBOLS: tuple[str, ...] = ("×", "✗", "✘")
_LEAK_TRUE_WORDS: tuple[str, ...] = ("正确", "对的", "是对", "成立")
_LEAK_FALSE_WORDS: tuple[str, ...] = ("错误", "错的", "是错", "不对", "不成立")
_LEAK_VERDICT_LEAD: str = r"(?:答案|正确答案|结论|所以|因此|应该|应当|正解)"
MIN_LEAK_OPTION_TEXT: int = 2

CHOICE_WRONG_REPLY: str = "这道选错了，我们一起看看是哪一步想岔了"
JUDGE_WRONG_REPLY: str = "判断反了，我们一起把题目再捋一遍"
CHOICE_CORRECT_REPLY: str = "答对了，你选的这项就是正确答案"
JUDGE_CORRECT_REPLY: str = "判断准确，这道你想清楚了"

# ===================================================================== 练习模块常量（与 practice_service 一致）
DEFAULT_COUNT: int = 10
MIN_COUNT: int = 1
MAX_COUNT: int = 30
RECENT_DAYS: int = 7
WEAK_POINT_LIMIT: int = 5

ITEM_PENDING: str = "pending"
ITEM_DONE: str = "done"
SESSION_DOING: str = "doing"
SESSION_DONE: str = "done"

GUIDE_FALLBACK_REPLY: str = "我们一步步来：先说说这道题告诉了我们什么条件？"

RELAXATION_LABELS: Dict[int, str] = {
    1: "优先使用可归因题库（同筛选条件）",
    2: "已放开：包含暂未关联知识点的题",
    3: "已放开：放宽到全部可自动判分的题",
}

HUMAN_ERROR_TEXT_LIMIT: int = 100

OPTIONS_STATUS_COMPLETE: str = "complete"
OPTIONS_STATUS_PARTIAL: str = "partial"
OPTIONS_STATUS_MISSING: str = "missing"
OPTIONS_STATUS_NA: str = "n/a"


# ===================================================================== 数据结构
@dataclass(frozen=True)
class OptionItem:
    key: str
    text: str

    def to_dict(self) -> Dict[str, str]:
        return {"key": self.key, "text": self.text}


@dataclass(frozen=True)
class JudgeOutcome:
    is_correct: bool
    level: int
    reason: str
    audit_reason: str = ""

    def __post_init__(self) -> None:
        if not self.audit_reason:
            object.__setattr__(self, "audit_reason", self.reason)

    def to_dict(self) -> Dict[str, Any]:
        # ⚠️ 刻意不含 audit_reason（防泄题）
        return {"is_correct": self.is_correct, "level": self.level, "reason": self.reason}


# ===================================================================== 选项归一
def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_key(raw: Any) -> Optional[str]:
    text = _clean_text(raw)
    if not text:
        return None
    match = __import__("re").search(r"[A-Za-z]", text)
    if match is None:
        return None
    if len(text) > 4:
        return None
    return match.group(0).upper()


def _option_from_mapping(item: Dict[str, Any], index: int) -> Optional[OptionItem]:
    key = _normalize_key(item.get("key") or item.get("label") or item.get("option"))
    if key is None:
        key = INDEX_KEYS[index] if index < len(INDEX_KEYS) else None
    if key is None:
        return None
    text = _clean_text(item.get("text") or item.get("value") or item.get("content"))
    return OptionItem(key=key, text=text)


def normalize_options(options: Any) -> List[OptionItem]:
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

    seen: Dict[str, OptionItem] = {}
    for item in items:
        if item.key not in seen:
            seen[item.key] = item
    return [seen[key] for key in sorted(seen)]


def option_keys(options: Any) -> List[str]:
    return [item.key for item in normalize_options(options)]


def extract_choice_key(raw: Any, *, allowed: Optional[Sequence[str]] = None) -> Optional[str]:
    text = _clean_text(raw)
    if not text:
        return None
    re = __import__("re")
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
        letters = {ch.upper() for ch in text if ch.isalpha() and ch.isascii()}
        hits = letters & set(allowed)
        if len(letters) == 1 and len(hits) == 1:
            found = [next(iter(hits))]
    if not found:
        return None
    unique = {key for key in found}
    if len(unique) != 1:
        return None
    key = found[0]
    if allowed is not None and key not in set(allowed):
        return None
    return key


def normalize_judge_value(raw: Any) -> Optional[bool]:
    text = _clean_text(raw)
    if not text:
        return None
    stripped = text.strip(_JUDGE_STRIP_CHARS).strip()
    if not stripped:
        stripped = text
    lowered = stripped.lower()
    if lowered in _TRUE_TOKENS:
        return True
    if lowered in _FALSE_TOKENS:
        return False
    hits_true = [ch for ch in stripped if ch in {"√", "✔", "✓"}]
    hits_false = [ch for ch in stripped if ch in {"×", "✗", "✘"}]
    if len(hits_true) == 1 and not hits_false:
        return True
    if len(hits_false) == 1 and not hits_true:
        return False
    return None


def _judge_choice(options, correct_answer, selected_answer) -> JudgeOutcome:
    items = normalize_options(options)
    if not items:
        return JudgeOutcome(False, LEVEL_FALLBACK, "这道题的选项数据缺失，已记录，稍后由老师确认")
    keys = [item.key for item in items]
    std_key = extract_choice_key(correct_answer, allowed=keys)
    if std_key is None:
        return JudgeOutcome(False, LEVEL_FALLBACK, "这道题的标准答案无法自动识别，已记录")
    user_key = extract_choice_key(selected_answer, allowed=keys)
    if user_key is None:
        typed = _clean_text(selected_answer)
        matched = [item.key for item in items if item.text and item.text == typed]
        if len(matched) == 1:
            user_key = matched[0]
    if user_key is None:
        return JudgeOutcome(False, LEVEL_FALLBACK, "你的作答无法自动判定，已记录")
    if user_key == std_key:
        return JudgeOutcome(True, LEVEL_RULE, CHOICE_CORRECT_REPLY,
                            audit_reason=f"选择题判对：学生选 {user_key}，标准答案 {std_key}")
    return JudgeOutcome(False, LEVEL_RULE, CHOICE_WRONG_REPLY,
                        audit_reason=f"选择题判错：学生选 {user_key}，标准答案 {std_key}")


def _judge_label(value: bool) -> str:
    return "√（对）" if value else "×（错）"


def _judge_true_false(correct_answer, selected_answer) -> JudgeOutcome:
    std = normalize_judge_value(correct_answer)
    if std is None:
        return JudgeOutcome(False, LEVEL_FALLBACK, "这道题的标准答案无法自动识别，已记录")
    user = normalize_judge_value(selected_answer)
    if user is None:
        return JudgeOutcome(False, LEVEL_FALLBACK, "你的作答无法自动判定，已记录")
    std_label = _judge_label(std)
    if user == std:
        return JudgeOutcome(True, LEVEL_RULE, JUDGE_CORRECT_REPLY,
                            audit_reason=f"判断题判对：学生答 {_judge_label(user)}，标准答案 {std_label}")
    return JudgeOutcome(False, LEVEL_RULE, JUDGE_WRONG_REPLY,
                        audit_reason=f"判断题判错：学生答 {_judge_label(user)}，标准答案 {std_label}")


def judge(q_type, options, correct_answer, selected_answer) -> JudgeOutcome:
    typed = _clean_text(selected_answer)
    if not typed:
        return JudgeOutcome(False, LEVEL_FALLBACK, "还没作答，先选一个答案吧")
    kind = _clean_text(q_type)
    if kind == TYPE_CHOICE:
        return _judge_choice(options, correct_answer, selected_answer)
    if kind == TYPE_JUDGE:
        return _judge_true_false(correct_answer, selected_answer)
    return JudgeOutcome(False, LEVEL_FALLBACK, f"「{kind or '未知题型'}」暂不支持自动判分，已记录，稍后由老师点评")


def is_l1_judgeable(q_type) -> bool:
    return _clean_text(q_type) in SUPPORTED_TYPES


def is_compound(raw) -> bool:
    text = _clean_text(raw)
    if not text:
        return False
    return len(_SUBQUESTION_RE.findall(text)) >= COMPOUND_MARK_THRESHOLD


def _standard_answer_resolvable(kind, options, answer) -> bool:
    if kind == TYPE_CHOICE:
        keys = option_keys(options)
        if not keys:
            return False
        return extract_choice_key(answer, allowed=keys) is not None
    if kind == TYPE_JUDGE:
        return normalize_judge_value(answer) is not None
    return False


def is_l1_safe(q_type, options, correct_answer) -> bool:
    kind = _clean_text(q_type)
    if kind not in SUPPORTED_TYPES:
        return False
    answer = _clean_text(correct_answer)
    if not answer:
        return False
    if is_compound(answer):
        return False
    return _standard_answer_resolvable(kind, options, answer)


# ===================================================================== no-leak 闸门
def _leaks_choice_answer(reply, options, correct_answer) -> bool:
    re = __import__("re")
    items = normalize_options(options)
    keys = [item.key for item in items]
    std_key = extract_choice_key(correct_answer, allowed=keys) if keys else None
    if std_key is None:
        return False
    key = re.escape(std_key)
    patterns = (
        rf"选\s*[（(【\[]?\s*{key}\s*[)）】\]]?(?![A-Za-z])",
        rf"{_LEAK_VERDICT_LEAD}[^。！？\n]{{0,8}}?[（(【\[]?\s*{key}\s*[)）】\]]?(?![A-Za-z])",
        rf"[（(【\[]?\s*{key}\s*[)）】\]]?\s*(?:这一?项|选?项)?\s*(?:才)?(?:是|为)\s*(?:正确|对的|答案|正解)",
    )
    for pattern in patterns:
        if re.search(pattern, reply, flags=re.IGNORECASE):
            return True
    std_text = next((item.text for item in items if item.key == std_key), "")
    if len(std_text) >= MIN_LEAK_OPTION_TEXT and std_text in reply:
        return True
    return False


def _leaks_judge_answer(reply, correct_answer) -> bool:
    re = __import__("re")
    std = normalize_judge_value(correct_answer)
    if std is None:
        return False
    symbols = _LEAK_TRUE_SYMBOLS if std else _LEAK_FALSE_SYMBOLS
    if any(symbol in reply for symbol in symbols):
        return True
    words = _LEAK_TRUE_WORDS if std else _LEAK_FALSE_WORDS
    alternation = "|".join(re.escape(word) for word in words)
    pattern = rf"{_LEAK_VERDICT_LEAD}[^。！？\n]{{0,8}}?(?:{alternation})"
    return re.search(pattern, reply) is not None


def leaks_answer(reply, q_type, options, correct_answer) -> bool:
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


# ===================================================================== 内存题库索引
class _Bank:
    """冷启动把 bank_data 建好索引，供抽题 / 判分 / 结算复用。"""

    def __init__(self) -> None:
        self.by_id: Dict[str, Dict[str, Any]] = {}
        self.attributable: List[str] = []
        for q in QUESTIONS:
            qid = q["id"]
            self.by_id[qid] = q
            if q.get("kp_id") and q.get("kp_verified"):
                self.attributable.append(qid)
        self.kp = _KpIndex(KP_NODES)


_BANK: Optional[_Bank] = None


def bank() -> _Bank:
    global _BANK
    if _BANK is None:
        _BANK = _Bank()
    return _BANK


# ===================================================================== 知识点树
class _KpIndex:
    def __init__(self, nodes: List[Dict[str, Any]]) -> None:
        self.parent_of: Dict[str, Optional[str]] = {}
        self.names: Dict[str, str] = {}
        self.sort_of: Dict[str, int] = {}
        for n in nodes:
            self.parent_of[n["id"]] = n["parent_id"]
            self.names[n["id"]] = n["name"]
            self.sort_of[n["id"]] = int(n.get("sort") or 0)
        for kid, pid in list(self.parent_of.items()):
            if pid is not None and pid not in self.parent_of:
                self.parent_of[kid] = None
        self.children_of: Dict[str, List[str]] = {}
        for kid, pid in self.parent_of.items():
            if pid is None:
                self.children_of.setdefault(kid, [])
        for n in nodes:
            kid = n["id"]
            pid = self.parent_of.get(kid)
            if kid is None or pid is None:
                continue
            self.children_of.setdefault(pid, []).append(kid)
        for pid in self.children_of:
            self.children_of[pid].sort(key=lambda c: (self.sort_of.get(c, 0), c))

    def rollup(self, kp_id: Optional[str]) -> Optional[str]:
        if kp_id is None or kp_id not in self.parent_of:
            return kp_id
        return self.parent_of[kp_id] or kp_id

    def subtree_ids(self, kp_id: Optional[str]) -> List[str]:
        if kp_id is None:
            return []
        result: List[str] = [kp_id]
        for child in self.children_of.get(kp_id, ()):
            if child != kp_id:
                result.append(child)
        return result

    def name(self, kp_id: Optional[str]) -> Optional[str]:
        if kp_id is None:
            return None
        return self.names.get(kp_id)


# ===================================================================== 选项完整性
def _options_status(q_type: Optional[str], options: Any) -> str:
    if (q_type or "").strip() != "选择":
        return OPTIONS_STATUS_NA
    count = len(normalize_options(options))
    if count >= 4:
        return OPTIONS_STATUS_COMPLETE
    if count >= 1:
        return OPTIONS_STATUS_PARTIAL
    return OPTIONS_STATUS_MISSING


def _question_payload(q: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "question_id": q["id"],
        "type": q.get("type") or "",
        "stem": q.get("content") or "",
        "options": [item.to_dict() for item in normalize_options(q.get("options"))],
        "options_status": _options_status(q.get("type"), q.get("options")),
        "difficulty": q.get("difficulty"),
        "grade": q.get("grade"),
        "edition": q.get("edition"),
        "section": q.get("section"),
        "kp_id": q.get("kp_id"),
    }


# ===================================================================== 抽题
def _clamp_count(count: Optional[int]) -> int:
    if count is None:
        return DEFAULT_COUNT
    try:
        value = int(count)
    except (TypeError, ValueError):
        return DEFAULT_COUNT
    return max(MIN_COUNT, min(value, MAX_COUNT))


def _candidate_ids(
    *,
    attributable_only: bool,
    grade=None,
    grade_band=None,
    edition=None,
    section=None,
    kp_id=None,
    difficulty=None,
) -> List[str]:
    # 容忍空字符串（与后端 practice_service 的 ``to_uuid(kp_id) if kp_id else None`` 一致：
    # 前端不传时这些字段为 null，但个别调用方可能误传 ""，这里统一归一成 None 避免误过滤）
    if isinstance(grade_band, str) and not grade_band.strip():
        grade_band = None
    if isinstance(edition, str) and not edition.strip():
        edition = None
    if isinstance(section, str) and not section.strip():
        section = None
    if isinstance(kp_id, str) and not kp_id.strip():
        kp_id = None
    b = bank()
    out: List[str] = []
    for qid in (b.attributable if attributable_only else b.by_id.keys()):
        q = b.by_id[qid]
        # serverless：无最近 7 天回避（没有 per-student 持久化）
        if grade is not None and q.get("grade") != int(grade):
            continue
        if grade_band is not None and grade_band in BAND_GRADE_RANGE:
            lo, hi = BAND_GRADE_RANGE[grade_band]
            g = q.get("grade")
            if g is None or not (int(lo) <= int(g) <= int(hi)):
                continue
        if edition and q.get("edition") != edition:
            continue
        if section and q.get("section") != section:
            continue
        if kp_id is not None:
            if q.get("kp_id") not in b.kp.subtree_ids(kp_id):
                continue
        if difficulty is not None and q.get("difficulty") != int(difficulty):
            continue
        # 双保险：再判一遍 L1 安全池
        if not is_l1_safe(q.get("type"), q.get("options"), q.get("correct_answer")):
            continue
        out.append(qid)
    return out


def _unmapped_count(ids: Sequence[str]) -> int:
    b = bank()
    return sum(1 for qid in ids if not (b.by_id[qid].get("kp_id") and b.by_id[qid].get("kp_verified")))


def _sample_and_meta(rows, want, level) -> Tuple[List[str], Dict[str, Any]]:
    pool_size = len(rows)
    sample_size = max(0, min(want, pool_size))
    picked = random.sample(list(rows), k=sample_size) if sample_size > 0 else []
    miss_count = max(0, want - len(picked))
    meta = {
        "pool_size": pool_size,
        "unmapped_count": _unmapped_count(rows),
        "auto_relaxed": level > 1,
        "requested_count": want,
        "actual_count": len(picked),
        "miss_count": miss_count,
        "relaxed_to_level": level,
    }
    return picked, meta


def draw_questions(
    *,
    grade=None,
    grade_band=None,
    edition=None,
    section=None,
    kp_id=None,
    difficulty=None,
    count=None,
) -> Dict[str, Any]:
    """抽一组题（serverless 版，逐档 fallback 与原 practice_service 同构）。

    返回与后端 ``DrawResponse`` 完全一致的字段，前端无需改动。
    """
    import uuid as _uuid

    want = _clamp_count(count)
    b = bank()

    common = dict(grade=grade, grade_band=grade_band, edition=edition,
                  section=section, kp_id=kp_id, difficulty=difficulty)
    steps: List[Dict[str, Any]] = []

    # 第 1 档：可归因
    rows_1 = _candidate_ids(attributable_only=True, **common)
    steps.append({"level": 1, "label": RELAXATION_LABELS[1], "pool_size": len(rows_1),
                  "applied": len(rows_1) > 0, "applied_at": _now_iso()})
    if len(rows_1) >= want:
        picked, meta = _sample_and_meta(rows_1, want, 1)
        return _draw_result(picked, steps, meta)

    # 第 3 档：放开 kp（serverless 无 7 天回避，第 2 档与第 1 档池相同，跳过）
    rows_3 = _candidate_ids(attributable_only=False, **common)
    if len(rows_3) > len(rows_1):
        steps.append({"level": 2, "label": RELAXATION_LABELS[2], "pool_size": len(rows_3),
                      "applied": True, "applied_at": _now_iso()})
        picked, meta = _sample_and_meta(rows_3, want, 2)
        return _draw_result(picked, steps, meta)

    if not rows_1:
        steps.append({"level": 6, "label": "兜底：当前条件下没有可练的题", "pool_size": 0,
                      "applied": False, "applied_at": _now_iso()})
        raise ApiError(CODE_NOT_FOUND, "这个筛选条件下暂时没有可以练的题，换个条件试试")

    picked, meta = _sample_and_meta(rows_1, want, 1)
    return _draw_result(picked, steps, meta)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _draw_result(picked: List[str], steps: List[Dict[str, Any]], meta: Dict[str, Any]) -> Dict[str, Any]:
    b = bank()
    import uuid as _uuid
    session_id = str(_uuid.uuid4())
    questions = [_question_payload(b.by_id[qid]) for qid in picked if qid in b.by_id]
    return {
        "session_id": session_id,
        "questions": questions,
        "pool_size": meta["pool_size"],
        "unmapped_count": meta["unmapped_count"],
        "auto_relaxed": meta["auto_relaxed"],
        "requested_count": meta["requested_count"],
        "actual_count": meta["actual_count"],
        "miss_count": meta["miss_count"],
        "relaxed_to_level": meta["relaxed_to_level"],
        "relaxation_steps": steps,
    }


# ===================================================================== 判分
def submit_answer(*, question_id, selected_answer, duration_sec=None) -> Dict[str, Any]:
    """提交单题作答（纯函数判分，不落库）。返回与后端一致的出参。"""
    b = bank()
    q = b.by_id.get(question_id)
    if q is None:
        raise ApiError(CODE_NOT_FOUND, "题目不存在")
    outcome = judge(q.get("type"), q.get("options"), q.get("correct_answer"), selected_answer)
    safe_duration = max(0, min(int(duration_sec or 0), 600)) if duration_sec else 0
    logger.info("练习判分 question=%s level=%s correct=%s 依据=%s",
                question_id, outcome.level, outcome.is_correct, outcome.audit_reason)
    result = outcome.to_dict()
    result["question_id"] = question_id
    result["duration_sec"] = safe_duration
    return result


# ===================================================================== 看答案
def reveal_answer(*, question_id, selected_answer=None) -> Dict[str, Any]:
    """看参考答案。

    产品约束（与后端一致）：只有**答错**的题才给看；答对的题返 403。
    前端已按 ``canReveal()`` 控制入口，这里再做一道服务端闸门：若前端把
    selected_answer 带上，则严格复核「必须已作答且判错」。
    """
    b = bank()
    q = b.by_id.get(question_id)
    if q is None:
        raise ApiError(CODE_NOT_FOUND, "题目不存在")
    if selected_answer is not None:
        outcome = judge(q.get("type"), q.get("options"), q.get("correct_answer"), selected_answer)
        if outcome.is_correct:
            raise ApiError(CODE_FORBIDDEN, "这道题你已经做对啦，不用看参考答案")
    return {
        "question_id": question_id,
        "correct_answer": (q.get("correct_answer") or "") if q.get("correct_answer") is not None else "",
        "analysis": (q.get("analysis") or "") if q.get("analysis") is not None else "",
    }


# ===================================================================== 结算（stateless）
def _weak_points(wrong_ids: Sequence[str]) -> List[Dict[str, Any]]:
    b = bank()
    counted: Dict[str, int] = {}
    rolled_ids: List[str] = []
    for qid in wrong_ids:
        q = b.by_id.get(qid)
        if not q or not q.get("kp_id"):
            continue
        rolled = b.kp.rollup(q["kp_id"])
        if rolled is None:
            continue
        counted[rolled] = counted.get(rolled, 0) + 1
        if rolled not in rolled_ids:
            rolled_ids.append(rolled)
    bucket: Dict[str, Dict[str, Any]] = {}
    for key, wrong_count in counted.items():
        name = b.kp.name(key) or "未命名知识点"
        bucket[key] = {"kp_id": key, "kp_name": name, "wrong_count": wrong_count}
    ordered = sorted(bucket.values(), key=lambda it: (-int(it["wrong_count"]), str(it["kp_name"])))
    return ordered[:WEAK_POINT_LIMIT]


def finish_session(*, answers: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """结算一次练习（stateless，从前端传来的作答列表重算）。

    ``answers``：``[{"question_id", "selected_answer"}]``（前端 practice store 的
    ``selected`` 映射）。正确率与错题**只从这里重算**，与本地后端 C3 口径一致。

    ⚠️ serverless 不落库：``mistakes_created`` 仅报告「本会话会建几条错题」，
    不真正写持久层；正式版接 Blob/DB 后改为幂等建档。
    """
    b = bank()
    items: List[Dict[str, Any]] = []
    correct_ids: List[str] = []
    wrong_ids: List[str] = []
    unmapped = 0
    for a in answers:
        qid = a.get("question_id")
        q = b.by_id.get(qid)
        if q is None:
            continue
        outcome = judge(q.get("type"), q.get("options"), q.get("correct_answer"),
                       a.get("selected_answer"))
        items.append({
            "question_id": qid,
            "status": ITEM_DONE,
            "is_correct": outcome.is_correct,
            "level": outcome.level,
            "selected_answer": _clean_text(a.get("selected_answer")) or None,
            "duration_sec": None,
        })
        (correct_ids if outcome.is_correct else wrong_ids).append(qid)
        if not (q.get("kp_id") and q.get("kp_verified")):
            unmapped += 1

    total = len(items)
    correct = len(correct_ids)
    wrong = len(wrong_ids)
    answered = correct + wrong
    mapped = [qid for qid in (correct_ids + wrong_ids)
              if (q := b.by_id.get(qid)) and q.get("kp_id")]

    return {
        "session_id": "",
        "total": total,
        "correct": correct,
        "wrong": wrong,
        # serverless 草稿版：不真正落库，仅报告会建几条
        "mistakes_created": wrong,
        "mastery_refreshed": bool(mapped),
        "answered": answered,
        "accuracy": round(correct / answered, 4) if answered else 0.0,
        "unmapped_count": unmapped,
        "items": items,
        "weak_points": _weak_points(wrong_ids),
    }


# ===================================================================== AI 引导（no-leak 闸门 + 可选 Ark）
GUIDE_PROMPT: str = """你现在是一位正在辅导孩子做相似题的中小学数学老师。
题目是：
{{QUESTION}}

孩子刚才的作答记录是：
{{WORK}}

你是会看人、会读题的老师，坚持“不直接给答案、一次只说一件事、引导孩子自己讲出思路”的苏格拉底式辅导。

【铁律（永远不许破）】
1. 绝不直接给答案，也不直接写解题步骤。先弄清楚孩子现在怎么想。
2. 一次只说一件事：每次回复最多一个问题。
3. 不用“你错了”“不对”这类词。
4. 孩子答案对了也要追问“你是怎么想出来的”。
5. 不公布答案。即使孩子问你答案，也用问题引导他自己走到结果。

【分步追问节奏】
1. 每轮回复 2-4 句话：先肯定/点拨已成立的部分，再抛一个贴合题意的追问。
2. 每轮只问一个问题。
3. 连续 2 轮孩子都没推进 → 第 3 轮把当前问题拆成更小颗粒度的子问题。
4. 拆小不是降级到“给答案”。

【给提示 vs 沉默判定表】
- 在写中间步骤 → 只肯定不推进。
- 明确说不会 → 给轻提示。
- 方向错了 → 抛矛盾问题不纠正。
- 已完整 → 问“这道题答完了吗？确定这是你的最终答案吗？”。

【语气与节奏】
亲切，像大哥哥大姐姐。多用“我们”“你看”“你觉得呢”。每轮 2-4 句。
每次回复贴着这道题具体的数字和情境。

只返回 JSON，不要代码块，不要 Markdown：
{"reply":"给孩子看的话","askFinalConfirm":true或false}"""


def _ark_respond(messages) -> str:
    """调火山方舟（OpenAI 兼容）。无 ARK_API_KEY 时抛异常，由调用方降级。"""
    api_key = os.environ.get("ARK_API_KEY")
    if not api_key:
        raise RuntimeError("ARK_API_KEY 未配置")
    model = os.environ.get("ARK_MODEL", "deepseek-chat")
    url = os.environ.get("ARK_ENDPOINT", "https://ark.cn-beijing.volces.com/api/v3/chat/completions")
    payload = {"model": model, "messages": messages, "temperature": 0.7}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


def sanitize_guide_reply(reply, *, q_type, options, correct_answer) -> str:
    text = _clean_text(reply)
    if not text:
        return GUIDE_FALLBACK_REPLY
    if leaks_answer(text, q_type, options, correct_answer):
        logger.warning("练习引导疑似泄题，已丢弃改用兜底话术")
        return GUIDE_FALLBACK_REPLY
    return text


def guide_question(*, question_id, work=None) -> Dict[str, Any]:
    """「让老师带我想一想」。无 Ark key 或调用失败时返回引导式兜底话术。"""
    b = bank()
    q = b.by_id.get(question_id)
    if q is None:
        raise ApiError(CODE_NOT_FOUND, "题目不存在")

    stem = q.get("content") or ""
    options = normalize_options(q.get("options"))
    if options:
        stem = stem + "\n" + "\n".join(f"{item.key}. {item.text}" for item in options)
    prompt = (GUIDE_PROMPT.replace("{{QUESTION}}", stem)
              .replace("{{WORK}}", _clean_text(work) or "（孩子还没写下过程，只是点开了求助）"))

    reply = GUIDE_FALLBACK_REPLY
    ask_final_confirm = False
    try:
        raw = _ark_respond(messages=[{"role": "user", "content": prompt}])
        reply = _clean_text(raw)
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            reply = _clean_text(parsed.get("reply")) or reply
            ask_final_confirm = bool(parsed.get("askFinalConfirm") or parsed.get("ask_final_confirm"))
    except Exception as exc:
        logger.warning("练习引导调用失败，改用兜底话术：%s", exc)

    reply = sanitize_guide_reply(
        reply,
        q_type=q.get("type"),
        options=q.get("options"),
        correct_answer=q.get("correct_answer"),
    )
    return {
        "question_id": question_id,
        "reply": reply,
        "ask_final_confirm": ask_final_confirm,
        "prompt_version": "1.2.0-serverless",
    }


__all__ = [
    "ApiError", "normalize_options", "is_l1_safe", "judge", "leaks_answer",
    "draw_questions", "submit_answer", "reveal_answer", "finish_session",
    "guide_question", "GUIDE_FALLBACK_REPLY", "OPTIONS_STATUS_COMPLETE",
    "OPTIONS_STATUS_PARTIAL", "OPTIONS_STATUS_MISSING", "OPTIONS_STATUS_NA",
]
