"""练习业务服务（题库消费层 · B3）。

职责（全部同步函数，sync 路由可直接调；async 路由须 ``run_in_threadpool`` 包裹）：

* ``draw_questions``  抽题（A+B 两级回退池，诚实披露 ``unmapped_count``）；
* ``draw_questions_by_plan`` 按 T07 自适应计划的题量/档位逐条捞题（薄包装）；
* ``create_session``  落 ``LearningTask`` 练习会话（复用，不建表）；
* ``submit_answer``   L1 判分 + ``attempts`` 落库（幂等）+ 回填 items 快照；
* ``reveal_answer``   Q3 分级解锁：只对做错的题返回参考答案 + 解析；
* ``finish_session``  结算：错题入本 + 掌握度缓存失效 + 统计出参；
* ``guide_question``  Q3 主按钮：走 ``practice_guide`` 提示词请方舟带着想。

硬约束落地
----------
* **C1 跨方言**：全部标准 SQL，不出现 ``date()``/``DATE_TRUNC``/``strftime``；
  时间窗口在 Python 侧算好再作为参数下发；随机抽题在 Python 侧 ``random.sample``，
  不依赖 ``ORDER BY RANDOM()``。
* **C2 幂等**：写作答走 ``learning_service.record_attempt``（``client_event_id`` 唯一键）。
* **C3 真相源**：``attempts`` 是唯一真相源；``LearningTask.items`` 只是逐题快照，
  ``finish_session`` 的正确率**从 attempts 重算**，不读 items。
* **C4 错题入本**：仅 ``is_correct=False`` 的题在 finish 时 ``record_mistake``（幂等）。
* **C6 抽题池**：默认可归因池（``kp_verified=1 AND kp_id IS NOT NULL``）；
  不够时自动放开到全 L1 安全池，并在出参里给 ``auto_relaxed``/``unmapped_count``。
* **C7 判分锁 L1**：抽题池口径**唯一真相源**是
  ``judge_engine.is_l1_safe()``（题型受支持 + 答案非空 + 非复合多小问 +
  选择题必须有可用选项）；``_candidate_rows`` 只做 SQL 粗筛，最终由它复核。
* **C11/C12 no-leak**：``guide`` 返回前过 ``sanitize_guide_reply()``，
  模型一旦抖出标准答案就整条丢弃、换兜底话术，不靠提示词裸奔。
* **C8 duration_sec**：后端 ``clamp_duration`` 再兜一次 ``min(d, 600)``。
* **C9 复用不新建**：``LearningTask``（``task_type`` 沿用既有 CHECK 枚举，
  **不新增** ``'practice'``——主理人裁决①）、``Mistake``、``profile_service``。

⚠️ ``LearningTask.task_type`` 的库级 CHECK 约束只允许 ``daily/review/exam``，
本模块统一写 ``daily``，靠 ``items`` 里的 ``source='practice'`` 标记来源，
零 DDL 改动。
"""

from __future__ import annotations

import logging
import random
import re
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import ark_client
from app.ai.agents._json_utils import extract_json
from app.ai.prompts import practice_guide
from app.core.error_categories import error_label
from app.engines.generation import (
    FALLBACK_KNOWLEDGE_POINT_ID,
    FALLBACK_LEVEL,
    STRATEGY_DIAGNOSTIC_FALLBACK,
)
from app.engines.types import DIFFICULTY_LEVELS
from app.core.response import (
    ApiError,
    CODE_BAD_REQUEST,
    CODE_FORBIDDEN,
    CODE_NOT_FOUND,
    CODE_SERVER_ERROR,
)
from app.models.base import utcnow
from app.models.content import (
    BAND_GRADE_RANGE,
    GRADE_BANDS,
    QUESTION_STATUS_ACTIVE,
    Answer,
    KnowledgePoint,
    Question,
)
from app.models.learning import Attempt, LearningTask, Mistake
from app.services import (
    adaptive_service,
    judge_engine,
    learning_service,
    profile_service,
    review_service,
)
from app.services.judge_engine import JudgeOutcome
from app.services.student_dna_service import record_learning_event
from app.services.kp_tree import load_kp_tree
from app.services.learning_service import clamp_duration, to_uuid

logger = logging.getLogger("app.services.practice")

# ------------------------------------------------------------------ 常量
#: 练习会话在 ``learning_tasks`` 里的 task_type。
#:
#: ⚠️ 主理人裁决①：**不新增** ``'practice'`` 枚举值（库级 CHECK 约束限制
#: 只能是 daily/review/exam），练习会话复用 ``daily``，来源靠 items 里的
#: ``source`` 字段区分。
PRACTICE_TASK_TYPE: str = "daily"
#: items 快照里的来源标记，用于把练习会话与将来的每日任务区分开
PRACTICE_SOURCE: str = "practice"

#: 抽题默认题量
DEFAULT_COUNT: int = 10
#: 抽题题量下限 / 上限
MIN_COUNT: int = 1
MAX_COUNT: int = 30

#: 「最近做过」的回避窗口（天）
RECENT_DAYS: int = 7

#: 逐题状态
ITEM_PENDING: str = "pending"
ITEM_DONE: str = "done"

#: 会话状态
SESSION_DOING: str = "doing"
SESSION_DONE: str = "done"

#: 结果页薄弱知识点最多展示几个
WEAK_POINT_LIMIT: int = 5

#: 引导语兜底话术：模型回复涉嫌泄题（或为空）时改说这句，绝不把答案递出去
GUIDE_FALLBACK_REPLY: str = "我们一步步来：先说说这道题告诉了我们什么条件？"

# 学生明确卡住时，不再把这句话当成普通回答继续追问同一个问题。
_STUCK_TEXT_RE = re.compile(
    r"(?:不知道|不记得|想不出来|不会|没思路|没头绪|看不懂|卡住了|不会做|不会算|不会解)"
)
GUIDE_HISTORY_KEY = "guide_history"

#: 抽题 fallback 档位的人话标签（设计 §1.5）。
#: 第 4/5 档本期不实现（Q8 拍板），但保留字典给未来 P2 阶段用。
RELAXATION_LABELS: Dict[int, str] = {
    1: "优先使用可归因题库（同筛选条件 + 回避最近 7 天）",
    2: "已放开：包含最近做过的题（同样条件）",
    3: "已放开：包含暂未关联知识点的题",
    4: "已放开：放宽题型限制",  # P2 留 TODO，不输出
    5: "已放开：缩放到邻近年级",  # P2 留 TODO，不输出
}

#: ``error_reason`` 文案上限（设计 §8.3 TD-6：超过 100 字截断 + 省略号）
HUMAN_ERROR_TEXT_LIMIT: int = 100


def _emit_dna_event(
    db: Session,
    *,
    student_id: uuid.UUID,
    event_type: str,
    question_id: Optional[uuid.UUID] = None,
    knowledge_point_id: Optional[uuid.UUID] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """DNA-004 行为事件埋点（Sprint 2 T2）。

    ⚠️ 埋点是**旁路**：任何异常（含事件枚举/落库失败）只记日志，绝不
    拖垮判分主流程。本函数内部 ``commit``（与 ``_patch_item`` 同风格），
    调用方拿到结果时事件已独立落库。
    """
    try:
        record_learning_event(
            db,
            student_id=student_id,
            event_type=event_type,
            question_id=question_id,
            knowledge_point_id=knowledge_point_id,
            meta=meta,
        )
        db.commit()
    except Exception:  # noqa: BLE001 - 埋点失败不影响主流程
        logger.warning(
            "DNA 事件埋点失败 event=%s question=%s",
            event_type,
            question_id,
            exc_info=True,
        )


# ================================================================== 内部工具
def _clean(value: Any) -> str:
    """任意值 → 去首尾空白的字符串。"""
    return "" if value is None else str(value).strip()


def _clamp_count(count: Optional[int]) -> int:
    """把请求题量夹到 ``[MIN_COUNT, MAX_COUNT]``，非法值退回默认。"""
    if count is None:
        return DEFAULT_COUNT
    try:
        value = int(count)
    except (TypeError, ValueError):
        return DEFAULT_COUNT
    return max(MIN_COUNT, min(value, MAX_COUNT))


def _recent_question_ids(
    db: Session, student_id: uuid.UUID, days: int = RECENT_DAYS
) -> List[uuid.UUID]:
    """取该学生最近 ``days`` 天做过的题目 ID（抽题时回避）。

    C1：时间边界在 Python 侧算好再下发参数，SQL 里不出现任何日期函数。

    Args:
        db: 数据库会话。
        student_id: 学生 ID。
        days: 回避窗口天数。

    Returns:
        题目 ID 列表（去重，可能为空）。
    """
    since = utcnow() - timedelta(days=max(0, int(days)))
    rows = db.execute(
        select(Attempt.question_id)
        .where(
            Attempt.student_id == student_id,
            Attempt.question_id.isnot(None),
            Attempt.created_at >= since,
        )
        .distinct()
    ).scalars()
    return [row for row in rows if row is not None]


def _candidate_rows(
    db: Session,
    *,
    attributable_only: bool,
    grade: Optional[int],
    grade_band: Optional[str] = None,
    edition: Optional[str],
    section: Optional[str],
    kp_id: Optional[uuid.UUID],
    difficulty: Optional[int],
    exclude_ids: Sequence[uuid.UUID] = (),
) -> List[Tuple[uuid.UUID, Optional[uuid.UUID], bool]]:
    """查 L1 安全池候选题，返回 ``[(question_id, kp_id, kp_verified)]``。

    **L1 安全池的口径不写在这里**——唯一真相源是
    ``judge_engine.is_l1_safe(q_type, options, correct_answer)``。本函数只做
    「SQL 粗筛 + Python 复核」两段式：

    1. **SQL 粗筛**（能下推的条件）：``status='active'`` + 题型在
       ``judge_engine.SUPPORTED_TYPES`` 内 + 标准答案非空，外加调用方的
       年级/版本/分区/知识点/难度/回避集等业务筛选；
    2. **Python 复核**：取回后逐行过 ``is_l1_safe()``，踢掉复合多小问
       （铁律 C2）与「选择题却没有可用选项」的脏题。

    为什么复核放 Python 层：``options`` 是 JSON 列、复合题判据要正则数
    ``(n)`` 编号，这两件事用 SQL 表达在 PostgreSQL / SQLite 双方言下都不可靠
    （C1 跨方言）。放 Python 层既准确又只有一套口径。

    ⚠️ 这里**不写死池子规模**。历史上「591 / 589 / 594」这类数字被硬编码进
    docstring 后，每次入库都要人肉重算、四轮都对不上。规模是 ``is_l1_safe()``
    的派生值，想核对请跑 ``tests/test_practice.py`` 里的 V3 不变式。

    Args:
        db: 数据库会话。
        attributable_only: ``True`` 时只要可归因题（``kp_verified=1 AND kp_id IS NOT NULL``）。
        grade: 年级（升入年级口径，不做任何 ``grade-1`` 换算）。
        edition: 教材版本。
        section: 册内分区。
        kp_id: 指定知识点。
        difficulty: 难度 1/3/5。
        exclude_ids: 需要回避的题目 ID。

    Returns:
        候选行列表 ``[(question_id, kp_id, kp_verified)]``，全部已通过
        ``is_l1_safe()`` 复核。
    """
    stmt = (
        select(
            Question.id,
            Question.kp_id,
            Question.kp_verified,
            Question.type_,
            Question.options,
            Answer.correct_answer,
        )
        .join(Answer, Answer.question_id == Question.id)
        .where(
            Question.status == QUESTION_STATUS_ACTIVE,
            Question.type_.in_(judge_engine.SUPPORTED_TYPES),
            Answer.correct_answer.isnot(None),
            Answer.correct_answer != "",
        )
    )

    if attributable_only:
        stmt = stmt.where(Question.kp_id.isnot(None), Question.kp_verified.is_(True))
    if grade is not None:
        stmt = stmt.where(Question.grade == int(grade))
    # 学段（小学/初中/高中）按年级区间过滤：识别不出的 band 直接忽略，
    # 不影响其余筛选条件。与单 grade 等值过滤可叠加（交集语义）。
    if grade_band is not None and grade_band in GRADE_BANDS:
        lo, hi = BAND_GRADE_RANGE[grade_band]
        stmt = stmt.where(Question.grade.between(int(lo), int(hi)))
    if edition:
        stmt = stmt.where(Question.edition == edition)
    if section:
        stmt = stmt.where(Question.section == section)
    if kp_id is not None:
        # KF 知识树 L2（design §4.5 R3）：前端按 L1 请求练习，而 backfill 已把题目
        # 下沉到 L2。这里必须展开成「L1 自身 + 全部 L2」，否则抽不到任何题、练习页白屏。
        # 传入的若本来就是 L2，``subtree_ids`` 返回 [自身]，语义与等值筛选一致。
        stmt = stmt.where(Question.kp_id.in_(load_kp_tree(db).subtree_ids(kp_id)))
    if difficulty is not None:
        stmt = stmt.where(Question.difficulty == int(difficulty))
    if exclude_ids:
        stmt = stmt.where(Question.id.notin_(list(exclude_ids)))

    rows: List[Tuple[uuid.UUID, Optional[uuid.UUID], bool]] = []
    for qid, question_kp_id, kp_verified, type_, options, correct_answer in db.execute(
        stmt
    ).all():
        if not judge_engine.is_l1_safe(type_, options, correct_answer):
            continue
        rows.append((qid, question_kp_id, bool(kp_verified)))
    return rows


def _unmapped_of(rows: Sequence[Tuple[uuid.UUID, Optional[uuid.UUID], bool]]) -> int:
    """统计候选池里「不可归因」的题数（``kp_id`` 为空或未复核）。"""
    return sum(1 for _, kp, verified in rows if kp is None or not verified)


#: 选择题选项完整性枚举（设计 §3.1 / §1.4）。
#: 前端 ``BankQuestionCard`` 按这个值渲染兜底态。
#:
#: - ``complete``  ：归一化后 ≥4 个选项；
#: - ``partial``   ：1~3 个选项（孩子点不到完整 ABCD）；
#: - ``missing``   ：0 个选项（前端「没有可选项」态）；
#: - ``n/a``       ：非选择题，前端无需 disabled。
OPTIONS_STATUS_COMPLETE: str = "complete"
OPTIONS_STATUS_PARTIAL: str = "partial"
OPTIONS_STATUS_MISSING: str = "missing"
OPTIONS_STATUS_NA: str = "n/a"


def _options_status(q_type: Optional[str], options: Any) -> str:
    """选择题选项完整性枚举。判断题返回 ``'n/a'``。

    这是设计 §1.4 单点真相：前后端都按这套判定走，Pool 里不再被前端用
    ``options.length === 1`` 之类魔数兜底。
    """
    if (q_type or "").strip() != "选择":
        return OPTIONS_STATUS_NA
    count = len(judge_engine.normalize_options(options))
    if count >= 4:
        return OPTIONS_STATUS_COMPLETE
    if count >= 1:
        return OPTIONS_STATUS_PARTIAL
    return OPTIONS_STATUS_MISSING


def _question_payload(question: Question) -> Dict[str, Any]:
    """把 ``Question`` 序列化成抽题出参（snake_case，选项已归一）。"""
    return {
        "question_id": str(question.id),
        "type": question.type_ or "",
        "stem": question.content or "",
        "options": [
            item.to_dict() for item in judge_engine.normalize_options(question.options)
        ],
        "options_status": _options_status(question.type_, question.options),
        "difficulty": question.difficulty,
        "grade": question.grade,
        "edition": question.edition,
        "section": question.section,
        "kp_id": str(question.kp_id) if question.kp_id else None,
    }


def _new_item(question_id: uuid.UUID) -> Dict[str, Any]:
    """构造 ``LearningTask.items`` 里的一条初始快照。"""
    return {
        "question_id": str(question_id),
        "status": ITEM_PENDING,
        "is_correct": None,
        "level": None,
        "selected_answer": None,
        "duration_sec": None,
        "revealed": False,
        "source": PRACTICE_SOURCE,
    }


def _normalize_items(raw: Any) -> List[Dict[str, Any]]:
    """把 ``LearningTask.items`` 读成规范的 list[dict]（防脏数据）。"""
    if not isinstance(raw, list):
        return []
    items: List[Dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, dict) and entry.get("question_id"):
            items.append(dict(entry))
    return items


def _load_session(
    db: Session, *, student_id: uuid.UUID, session_id: Any
) -> LearningTask:
    """按 ID 取练习会话并校验归属。

    归属口径与 ``mark_mistake_solved`` 对齐：**别人的会话一律按 404**，
    不返 403，避免通过状态码探测「某个 UUID 是否存在」。

    Args:
        db: 数据库会话。
        student_id: 当前学生 ID。
        session_id: 练习会话 ID。

    Returns:
        ``LearningTask`` 实例。

    Raises:
        ApiError: code=400 ID 非法；code=404 会话不存在或不属于当前学生。
    """
    tid = to_uuid(session_id, "session_id")
    task = db.execute(
        select(LearningTask).where(LearningTask.id == tid)
    ).scalar_one_or_none()
    if task is None or task.student_id != student_id:
        raise ApiError(CODE_NOT_FOUND, "练习会话不存在")
    return task


def _find_item(
    items: Sequence[Dict[str, Any]], question_id: uuid.UUID
) -> Optional[Dict[str, Any]]:
    """在 items 快照里找到指定题目那一条。"""
    target = str(question_id)
    for item in items:
        if str(item.get("question_id")) == target:
            return item
    return None


def _patch_item(
    db: Session,
    task: LearningTask,
    question_id: uuid.UUID,
    patch: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """就地更新 items 里某道题的快照并落库。

    ⚠️ ``JSONType`` 在 SQLite 下是 ``TEXT``，原地改 list 元素不会触发脏检测，
    必须**整体重新赋值**才会 flush，所以这里重建一个新 list 再赋给 ``task.items``。

    Args:
        db: 数据库会话（本函数内部 commit）。
        task: 练习会话。
        question_id: 目标题目。
        patch: 要合并进快照的字段。

    Returns:
        更新后的完整 items 列表。
    """
    items = _normalize_items(task.items)
    target = str(question_id)
    merged: List[Dict[str, Any]] = []
    for item in items:
        if str(item.get("question_id")) == target:
            new_item = dict(item)
            new_item.update(patch)
            merged.append(new_item)
        else:
            merged.append(item)
    task.items = merged
    if task.status == "pending":
        task.status = SESSION_DOING
    db.add(task)
    db.commit()
    db.refresh(task)
    return _normalize_items(task.items)


def _is_stuck_text(work: Optional[str]) -> bool:
    """识别学生明确求助的短句，避免把「不知道」误当成普通步骤。"""
    return bool(_STUCK_TEXT_RE.search(_clean(work)))


def _recent_guide_history(item: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    """读取题目快照中最近的引导轮次，防止老师重复自己的问题。"""
    if not item or not isinstance(item.get(GUIDE_HISTORY_KEY), list):
        return []
    history: List[Dict[str, str]] = []
    for turn in item[GUIDE_HISTORY_KEY][-6:]:
        if not isinstance(turn, dict):
            continue
        work = _clean(turn.get("work"))
        reply = _clean(turn.get("reply"))
        if work or reply:
            history.append({"work": work, "reply": reply})
    return history


def _question_numbers(stem: str) -> List[str]:
    """提取题干里的少量数字，帮助兜底提示仍然贴着当前题目。"""
    return re.findall(r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万]+)", stem or "")[
        :4
    ]


def _stuck_hint_reply(question: Question, stuck_round: int) -> str:
    """模型重复或无法接住时，按卡住次数递进给一个最小台阶。"""
    stem = _clean(question.content)
    if len(stem) > 180:
        stem = stem[:177] + "..."
    numbers = _question_numbers(stem)
    number_hint = f"题目里先出现了 {numbers[0]}" if numbers else "题目里的第一个条件"
    if stuck_round <= 1:
        return (
            f"没关系，老师先把题目里的关键条件抓出来：{stem}。"
            "我们先不急着算，你只告诉我：题目最后要求我们求什么？"
        )
    if stuck_round == 2:
        return (
            f"没关系，我们把题目再拆小一点。{number_hint}，你觉得它表示的是总量、"
            "其中一部分，还是一次变化？先说这一个判断就好。"
        )
    return (
        f"我们先只做一个小判断：回到题目里的“{number_hint}”，它在题目中是用来说明"
        "谁的数量或大小的？你先把对应的对象说出来，后面老师陪你接着走。"
    )


def _save_guide_turn(
    db: Session,
    task: LearningTask,
    question_id: uuid.UUID,
    *,
    work: Optional[str],
    reply: str,
) -> None:
    """把引导轮次写入当前题目快照，供下一轮避开重复。"""
    item = _find_item(_normalize_items(task.items), question_id)
    history = _recent_guide_history(item)
    history.append({"work": _clean(work), "reply": _clean(reply)})
    _patch_item(db, task, question_id, {GUIDE_HISTORY_KEY: history[-6:]})


def _load_question(
    db: Session, question_id: uuid.UUID
) -> Tuple[Question, Optional[Answer]]:
    """取题目与其标准答案（题目不存在按 404）。"""
    question = db.execute(
        select(Question).where(Question.id == question_id)
    ).scalar_one_or_none()
    if question is None:
        raise ApiError(CODE_NOT_FOUND, "题目不存在")
    answer = db.execute(
        select(Answer).where(Answer.question_id == question_id)
    ).scalar_one_or_none()
    return question, answer


# ================================================================== 抽题
def _draw_with_relaxation(
    db: Session,
    *,
    common: Dict[str, Any],
    recent: Sequence[uuid.UUID],
    want: int,
) -> Tuple[List[uuid.UUID], List[Dict[str, Any]], Dict[str, Any]]:
    """逐档抽题（设计 §1.5）。

    6 档 fallback 仅本期实现 1-3 档 + 兜底 404（Q8 拍板）；4/5 档留 TODO。
    每档**只有真的放宽（候选池扩增）**时才追加 ``steps``——避免「挨个跑一遍
    档但啥也没松」刷屏；同时 ``relaxed_to_level`` 也只在档位**真的进了一步**
    时才升档。

    关键口径（设计 §1.5）：

    * 第 1 档：可归因 + 回避 7 天；够用即停；
    * 第 2 档：可归因 + 取消回避 7 天（候选多了才算放宽）；
    * 第 3 档：放开 kp（attributable_only=False）+ 取消回避（候选多了才算放宽）；
    * 第 4/5 档：本期不实现（影响判分口径 / 产品卖点）；TODO 留 P2；
    * 兜底：3 档全空 → 返回 ApiError 404。

    注意：**``common`` 字典里始终带着 ``kp_id``**。放宽档只放宽「是否只要人工
    核对过的题」与「是否回避最近 7 天」，所以**指定知识点时抽到的题必然还
    是这个知识点的**——这点很重要，避免孩子看到「练了别的知识点」这种假披露。

    Returns:
        ``(picked_ids, steps, meta)``：

        * ``picked_ids``：本轮抽到的题 ID 列表（最多 want 道）；
        * ``steps``：逐档尝试记录（仅放宽过的档），前端原样展示；
        * ``meta``：抽题池元数据（pool_size / auto_relaxed / actual_count /
          miss_count / relaxed_to_level）。
    """
    steps: List[Dict[str, Any]] = []

    # ---- 第 1 档：可归因 + 回避 7 天 ----------------------------------------
    rows_1 = _candidate_rows(db, attributable_only=True, exclude_ids=recent, **common)
    steps.append(
        {
            "level": 1,
            "label": RELAXATION_LABELS[1],
            "pool_size": len(rows_1),
            "applied_at": utcnow().isoformat(),
            "applied": len(rows_1) > 0,
        }
    )
    if len(rows_1) >= want:
        return _sample_and_meta(rows_1, want, steps, level=1)

    chosen_rows: List[Tuple[uuid.UUID, Optional[uuid.UUID], bool]] = list(rows_1)
    chosen_level = 1

    # ---- 第 2 档：可归因 + 取消回避 7 天 ------------------------------------
    rows_2 = _candidate_rows(db, attributable_only=True, exclude_ids=(), **common)
    # 只有真的让候选池扩大才记录为「放宽」——避免「同池空跑」刷屏也避免误标
    # auto_relaxed=True。
    if len(rows_2) > len(chosen_rows):
        chosen_rows = list(rows_2)
        chosen_level = 2
        steps.append(
            {
                "level": 2,
                "label": RELAXATION_LABELS[2],
                "pool_size": len(rows_2),
                "applied_at": utcnow().isoformat(),
                "applied": True,
            }
        )
    if len(chosen_rows) >= want:
        return _sample_and_meta(chosen_rows, want, steps, level=chosen_level)

    # ---- 第 3 档：放开 kp（不可归因）+ 取消回避 7 天 ------------------------
    rows_3 = _candidate_rows(db, attributable_only=False, exclude_ids=(), **common)
    if len(rows_3) > len(chosen_rows):
        chosen_rows = list(rows_3)
        chosen_level = 3
        steps.append(
            {
                "level": 3,
                "label": RELAXATION_LABELS[3],
                "pool_size": len(rows_3),
                "applied_at": utcnow().isoformat(),
                "applied": True,
            }
        )

    # ---- 第 4 档（TODO 不实现）-----------------------------------------------
    # 设计 §1.5：第 4 档是「题型放宽」，会影响 judge_engine，必须等 P2 阶段
    # 与判分口径合并讨论后再开。本期不输出 steps、不抽样。
    # ---- 第 5 档（TODO 不实现）-----------------------------------------------
    # 设计 §1.5：第 5 档是「年级缩放」，会影响产品卖点「筛年级练」的语义，
    # 也留 P2，本期不输出 steps、不抽样。

    # ---- 兜底：完全抽不到题 ------------------------------------------------
    if not chosen_rows:
        steps.append(
            {
                "level": 6,
                "label": "兜底：当前条件下没有可练的题",
                "pool_size": 0,
                "applied_at": utcnow().isoformat(),
                "applied": False,
            }
        )
        return (
            [],
            steps,
            {
                "pool_size": 0,
                "unmapped_count": 0,
                "auto_relaxed": False,
                "requested_count": want,
                "actual_count": 0,
                "miss_count": want,
                "relaxed_to_level": 6,
            },
        )

    return _sample_and_meta(chosen_rows, want, steps, level=chosen_level)


def _sample_and_meta(
    rows: Sequence[Tuple[uuid.UUID, Optional[uuid.UUID], bool]],
    want: int,
    steps: List[Dict[str, Any]],
    level: int,
) -> Tuple[List[uuid.UUID], List[Dict[str, Any]], Dict[str, Any]]:
    """从候选池随机抽样 + 拼 meta（Q7 拍板：少给不凑数）。"""
    pool_size = len(rows)
    # :func:`random.sample` 的 ``k`` 必须 ≥0，否则抛 ValueError
    sample_size = max(0, min(want, pool_size))
    picked: List[Tuple[uuid.UUID, Optional[uuid.UUID], bool]] = (
        random.sample(list(rows), k=sample_size) if sample_size > 0 else []
    )
    picked_ids = [row[0] for row in picked]
    miss_count = max(0, want - len(picked))
    return (
        picked_ids,
        steps,
        {
            "pool_size": pool_size,
            "unmapped_count": _unmapped_of(rows),
            "auto_relaxed": level > 1,
            "requested_count": want,
            "actual_count": len(picked_ids),
            "miss_count": miss_count,
            "relaxed_to_level": level,
        },
    )


def draw_questions(
    db: Session,
    *,
    student_id: Any,
    grade: Optional[int] = None,
    grade_band: Optional[str] = None,
    edition: Optional[str] = None,
    section: Optional[str] = None,
    kp_id: Optional[Any] = None,
    difficulty: Optional[int] = None,
    count: Optional[int] = DEFAULT_COUNT,
    adaptive: bool = False,
) -> Dict[str, Any]:
    """抽一组题并开一个练习会话（C6 逐档 fallback）。

    6 档 fallback 本期只做 1-3 档 + 兜底 404（Q8 拍板）；4/5 档留 P2。

    **自适应难度（#4 draw_adaptive）**：``adaptive=True`` 时忽略 ``difficulty``，
    改按学生画像推荐的主难度抽约 70% 题 + 相邻难度抽约 30% 题（Python 侧
    ``random.sample`` 混池，C1 跨方言约束不变）。学生尚未建立画像时回退默认
    难度 3，行为与显式 ``difficulty=3`` 一致。

    出参新增 4 字段（与 ``DrawResponse`` 一一对应）：

    * ``actual_count``：本轮实际抽到的题数（可能 < requested_count，少给不凑数）；
    * ``miss_count`` = ``requested_count - actual_count``；
    * ``relaxed_to_level``：1-3（实际放宽到的档）+ 6（兜底完全没题）；
    * ``relaxation_steps``：逐档尝试记录，前端原样逐行展示。

    出参同时保留旧字段 ``auto_relaxed`` 作为兼容老客户端的布尔位
    （``auto_relaxed = (relaxed_to_level > 1)``，与旧版语义一致）。

    Args:
        db: 数据库会话（内部 commit）。
        student_id: 学生 ID。
        grade: 年级（升入年级口径）。
        edition: 教材版本（青岛版/苏教版）。
        section: 册内分区。
        kp_id: 指定知识点 ID。
        difficulty: 难度 1/3/5（``adaptive=True`` 时忽略）。
        count: 题量，夹在 ``[1, 30]``，默认 10。
        adaptive: 是否开启自适应难度（#4）。

    Returns:
        ``{session_id, questions, pool_size, unmapped_count, auto_relaxed,
        requested_count, actual_count, miss_count, relaxed_to_level,
        relaxation_steps, adaptive_used, adaptive_difficulty}``。

    Raises:
        ApiError: code=400 参数非法；code=404 当前筛选条件下没有可练的题（3 档全空）。
    """
    sid = to_uuid(student_id, "student_id")
    want = _clamp_count(count)
    target_kp = to_uuid(kp_id, "kp_id") if kp_id else None

    recent = _recent_question_ids(db, sid)
    common: Dict[str, Any] = {
        "grade": grade,
        "grade_band": grade_band,
        "edition": edition,
        "section": section,
        "kp_id": target_kp,
        "difficulty": difficulty,
    }

    # ---- #4 自适应难度：主难度 70% + 相邻难度 30%（均复用逐档回退）----
    adaptive_used = False
    adaptive_difficulty: Optional[int] = None
    if adaptive:
        base = adaptive_service.recommend_difficulty(db, student_id=sid)
        adaptive_difficulty = base
        adaptive_used = True
        neighbors = adaptive_service.neighbor_difficulties(base)
        main_count = max(1, int(round(want * 0.7)))
        side_count = want - main_count

        picked_ids, steps, meta = _draw_with_relaxation(
            db,
            common={**common, "difficulty": base},
            recent=recent,
            want=main_count,
        )
        # 主难度凑不满时，从相邻难度补齐（各相邻档依次尝试，不重复已抽题）
        seen: set[uuid.UUID] = set(picked_ids)
        if len(picked_ids) < want and neighbors:
            for neighbor in neighbors:
                if len(seen) >= want:
                    break
                need = want - len(seen)
                neighbor_ids, neighbor_steps, _neighbor_meta = _draw_with_relaxation(
                    db,
                    common={**common, "difficulty": neighbor},
                    recent=list(recent) + list(seen),
                    want=need,
                )
                added = [qid for qid in neighbor_ids if qid not in seen]
                picked_ids.extend(added)
                seen.update(added)
                steps.extend(neighbor_steps)

        # 自适应池完全为空：回退普通全难度抽题，避免 404 裸奔
        if not picked_ids:
            picked_ids, steps, meta = _draw_with_relaxation(
                db, common=common, recent=recent, want=want
            )
    else:
        picked_ids, steps, meta = _draw_with_relaxation(
            db,
            common=common,
            recent=recent,
            want=want,
        )

    # ---- 兜底 404：所有档位都没有可抽题 --------------------------------------
    if not picked_ids:
        raise ApiError(CODE_NOT_FOUND, "这个筛选条件下暂时没有可以练的题，换个条件试试")

    task = create_session(db, student_id=sid, question_ids=picked_ids)

    questions = (
        db.execute(select(Question).where(Question.id.in_(picked_ids))).scalars().all()
    )
    by_id = {q.id: q for q in questions}
    payload = [_question_payload(by_id[qid]) for qid in picked_ids if qid in by_id]

    return {
        "session_id": str(task.id),
        "questions": payload,
        "pool_size": meta["pool_size"],
        "unmapped_count": meta["unmapped_count"],
        # 旧字段保留，老客户端不破
        "auto_relaxed": meta["auto_relaxed"],
        "requested_count": meta["requested_count"],
        # 新增 4 字段（设计 §1.5 / §3.3）
        "actual_count": meta["actual_count"],
        "miss_count": meta["miss_count"],
        "relaxed_to_level": meta["relaxed_to_level"],
        "relaxation_steps": steps,
        # #4 自适应难度披露字段
        "adaptive_used": adaptive_used,
        "adaptive_difficulty": adaptive_difficulty,
    }


def draw_questions_by_plan(
    db: Session,
    *,
    student_id: Any,
    plans: Sequence[Any],
    grade: Optional[int] = None,
    grade_band: Optional[str] = None,
    edition: Optional[str] = None,
    section: Optional[str] = None,
    recent_days: int = RECENT_DAYS,
) -> Dict[str, Any]:
    """按 ``plan_exercises`` 产出的题量计划逐条捞题（T07-S3，**薄包装**）。

    ⚠️ **不重复实现放宽与去重**：逐条 ``ExercisePlan`` 直接复调
    ``_draw_with_relaxation``（含它的三档 fallback 语义），回避集用现成的
    ``_recent_question_ids``；本函数只负责「计划 → 多次抽题」的编排与汇总。

    逐计划执行口径：

    * ``kp_id`` 恒定为计划里的知识点；``strategy == "diagnostic_fallback"``
      （队列为空的综合摸底）时置 ``None`` 走综合题池；
    * ``difficulty`` 逐档取 ``plan.difficulty_levels``，凑不满再换下一档，
      **少给不凑数**（继承 ``_sample_and_meta`` 的 ``miss_count`` 语义）；
    * 已抽到的题跨计划、跨档位全局去重，保证一次计划里不出现重复题。

    Args:
        db: 数据库会话（本函数不 commit——抽题是只读操作）。
        student_id: 学生 ID。
        plans: ``ExercisePlan`` 序列（也可以是带同名字段的对象/字典）。
        grade: 年级。
        grade_band: 学段（小学/初中/高中）。
        edition: 教材版本。
        section: 册内分区。
        recent_days: 回避窗口天数。

    Returns:
        ``{"question_ids", "relaxation_steps", "total", "actual_count",
        "miss_count"}``，其中 ``total`` 是计划题数之和，``miss_count`` 是
        各计划缺口之和。**一道题都抽不到也不抛异常**（``actual_count=0``），
        由上层决定是否降级。
    """
    sid = to_uuid(student_id, "student_id")
    recent = _recent_question_ids(db, sid, days=recent_days)

    picked: List[uuid.UUID] = []
    seen: set = set()
    steps: List[Dict[str, Any]] = []
    total_requested = 0
    miss_count = 0

    for plan in _as_plans(plans):
        want = max(0, int(getattr(plan, "question_count", 0) or 0))
        if want <= 0:
            continue
        total_requested += want

        target_kp = _plan_kp_id(plan)
        plan_picked: List[uuid.UUID] = []
        for level in _plan_levels(plan):
            need = want - len(plan_picked)
            if need <= 0:
                break
            level_ids, level_steps, _meta = _draw_with_relaxation(
                db,
                common={
                    "grade": grade,
                    "grade_band": grade_band,
                    "edition": edition,
                    "section": section,
                    "kp_id": target_kp,
                    "difficulty": int(level),
                },
                # 回避：最近做过的 + 前面计划已抽的 + 本计划已抽的
                recent=list(recent) + picked + plan_picked,
                want=need,
            )
            steps.extend(level_steps)
            for qid in level_ids:
                if qid not in seen:
                    seen.add(qid)
                    plan_picked.append(qid)

        picked.extend(plan_picked)
        miss_count += max(0, want - len(plan_picked))

    return {
        "question_ids": [str(qid) for qid in picked],
        "relaxation_steps": steps,
        "total": total_requested,
        "actual_count": len(picked),
        "miss_count": miss_count,
    }


def _as_plans(plans: Any) -> List[Any]:
    """把入参归一成计划列表；``None`` / 单个计划都按列表处理。"""
    if plans is None:
        return []
    if isinstance(plans, (list, tuple)):
        return list(plans)
    return [plans]


def _plan_field(plan: Any, names: Sequence[str], default: Any = None) -> Any:
    """从 ``ExercisePlan`` 数据类（或兼容对象/字典）里取一个字段。"""
    for name in names:
        try:
            if hasattr(plan, name):
                return getattr(plan, name)
        except Exception:  # noqa: BLE001 - 脏对象的属性访问可能抛异常
            continue
    if isinstance(plan, dict):
        for name in names:
            if name in plan:
                return plan[name]
    return default


def _plan_kp_id(plan: Any) -> Optional[uuid.UUID]:
    """取计划的知识点 ID；兜底计划（综合摸底）返回 ``None`` 走综合题池。"""
    strategy = _clean(_plan_field(plan, ("strategy",), ""))
    if strategy == STRATEGY_DIAGNOSTIC_FALLBACK:
        return None
    raw = _plan_field(plan, ("knowledge_point_id", "kp_id"))
    if not raw or _clean(raw) == FALLBACK_KNOWLEDGE_POINT_ID:
        return None
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        logger.warning("自适应计划里有非法知识点 ID，已按综合题池处理: %r", raw)
        return None


def _plan_levels(plan: Any) -> List[int]:
    """取计划的难度档序列；脏值回退 ``[3]``。"""
    raw = _plan_field(plan, ("difficulty_levels", "levels"))
    values = raw if isinstance(raw, (list, tuple, set, frozenset)) else [raw]
    levels: List[int] = []
    for value in values:
        try:
            level = int(value)
        except (TypeError, ValueError):
            continue
        if level in DIFFICULTY_LEVELS and level not in levels:
            levels.append(level)
    return levels or [FALLBACK_LEVEL]


def create_session(
    db: Session, *, student_id: Any, question_ids: Sequence[uuid.UUID]
) -> LearningTask:
    """开一个练习会话（复用 ``LearningTask``，不建新表）。

    Args:
        db: 数据库会话（本函数内部 commit）。
        student_id: 学生 ID。
        question_ids: 本次练习的题目 ID 列表（保持抽题顺序）。

    Returns:
        新建的 ``LearningTask``。

    Raises:
        ApiError: code=400，题目列表为空。
    """
    sid = to_uuid(student_id, "student_id")
    if not question_ids:
        raise ApiError(CODE_BAD_REQUEST, "练习题目列表不能为空")

    task = LearningTask(
        student_id=sid,
        task_type=PRACTICE_TASK_TYPE,
        items=[_new_item(qid) for qid in question_ids],
        status=SESSION_DOING,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


# ================================================================== 判分
def submit_answer(
    db: Session,
    *,
    student_id: Any,
    session_id: Any,
    question_id: Any,
    selected_answer: Optional[str],
    duration_sec: Optional[int] = None,
    client_event_id: Optional[str] = None,
) -> Dict[str, Any]:
    """提交一道题的作答：L1 判分 → attempts 落库（幂等）→ 回填 items 快照。

    Args:
        db: 数据库会话（内部 commit）。
        student_id: 学生 ID。
        session_id: 练习会话 ID。
        question_id: 题目 ID。
        selected_answer: 学生作答（选择题传键位 ``A``，判断题传 ``√``/``×``）。
        duration_sec: 单题耗时（秒），后端再兜一次 ``min(d, 600)``。
        client_event_id: 客户端幂等键。

    Returns:
        ``{"is_correct", "level", "reason", "question_id", "duration_sec"}``。

        ⚠️ ``reason`` 是**对外**文案，答错时**不含标准答案**（Q3：默认不给答案，
        想看必须走 ``/reveal``）。完整判分依据在 ``outcome.audit_reason``，
        只写日志、**不进返回值**——本函数的返回值会被路由原样塞进响应体。

    Raises:
        ApiError: code=400 参数非法 / 题目不在本次练习中；code=404 会话或题目不存在。
    """
    sid = to_uuid(student_id, "student_id")
    qid = to_uuid(question_id, "question_id")
    task = _load_session(db, student_id=sid, session_id=session_id)

    items = _normalize_items(task.items)
    if _find_item(items, qid) is None:
        raise ApiError(CODE_BAD_REQUEST, "这道题不在本次练习中")

    question, answer = _load_question(db, qid)
    outcome: JudgeOutcome = judge_engine.judge(
        question.type_,
        question.options,
        answer.correct_answer if answer else None,
        selected_answer,
    )

    safe_duration = clamp_duration(duration_sec)
    learning_service.record_attempt(
        db,
        student_id=sid,
        question_id=qid,
        selected_answer=_clean(selected_answer) or None,
        is_correct=outcome.is_correct,
        duration_sec=safe_duration,
        client_event_id=client_event_id,
    )

    _patch_item(
        db,
        task,
        qid,
        {
            "status": ITEM_DONE,
            "is_correct": outcome.is_correct,
            "level": outcome.level,
            "selected_answer": _clean(selected_answer) or None,
            "duration_sec": safe_duration,
        },
    )

    # 审计线索只落日志：audit_reason 含标准答案，是排查「判分口径对不对」的唯一依据，
    # 但绝不能出现在响应体里。出参一律走 outcome.to_dict()（该方法刻意不含 audit_reason），
    # 不要在这里手工拼 reason，否则又会退回「一个字段两个受众」的老坑。
    logger.info(
        "练习判分 session=%s question=%s level=%s correct=%s 依据=%s",
        task.id,
        qid,
        outcome.level,
        outcome.is_correct,
        outcome.audit_reason,
    )

    result = outcome.to_dict()
    result["question_id"] = str(qid)
    result["duration_sec"] = safe_duration

    # ---- #4 自适应难度：每次判分后更新学生的难度画像（失败不拖垮主流程）----
    try:
        adaptive_service.record_attempt_result(
            db, student_id=sid, is_correct=outcome.is_correct
        )
    except Exception:  # noqa: BLE001 - 自适应更新失败只影响难度推荐，不影响判分
        logger.warning(
            "自适应难度更新失败 session=%s question=%s", task.id, qid, exc_info=True
        )

    # ---- DNA-004 行为事件埋点：一次作答 = ANSWER +（CORRECT 或 ERROR）----
    _emit_dna_event(
        db,
        student_id=sid,
        event_type="ANSWER",
        question_id=qid,
        knowledge_point_id=question.kp_id if question is not None else None,
        meta={
            "session_id": str(task.id),
            "is_correct": outcome.is_correct,
            "level": outcome.level,
            "duration_sec": safe_duration,
        },
    )
    _emit_dna_event(
        db,
        student_id=sid,
        event_type="CORRECT" if outcome.is_correct else "ERROR",
        question_id=qid,
        knowledge_point_id=question.kp_id if question is not None else None,
        meta={"session_id": str(task.id), "level": outcome.level},
    )

    return result


# ================================================================== 看答案
def reveal_answer(
    db: Session,
    *,
    student_id: Any,
    session_id: Any,
    question_id: Any,
) -> Dict[str, Any]:
    """Q3 分级解锁：只对**做错**的题返回参考答案与解析。

    做对的题不给答案（防止刷答案）；还没作答的题也不给，必须先自己试一次。

    Args:
        db: 数据库会话（内部 commit，用于记 ``revealed`` 标记）。
        student_id: 学生 ID。
        session_id: 练习会话 ID。
        question_id: 题目 ID。

    Returns:
        ``{"question_id", "correct_answer", "analysis"}``。

    Raises:
        ApiError: code=400 还没作答；code=403 这道题已经做对；code=404 会话/题目不存在。
    """
    sid = to_uuid(student_id, "student_id")
    qid = to_uuid(question_id, "question_id")
    task = _load_session(db, student_id=sid, session_id=session_id)

    item = _find_item(_normalize_items(task.items), qid)
    if item is None:
        raise ApiError(CODE_BAD_REQUEST, "这道题不在本次练习中")
    if item.get("status") != ITEM_DONE:
        raise ApiError(CODE_BAD_REQUEST, "先自己写一遍答案，再看参考答案吧")
    if item.get("is_correct") is True:
        raise ApiError(CODE_FORBIDDEN, "这道题你已经做对啦，不用看参考答案")

    question, answer = _load_question(db, qid)
    _patch_item(db, task, qid, {"revealed": True})

    # ---- DNA-004 行为事件埋点：看参考答案算一次 REVIEW 复习行为 ----
    _emit_dna_event(
        db,
        student_id=sid,
        event_type="REVIEW",
        question_id=qid,
        knowledge_point_id=question.kp_id if question is not None else None,
        meta={"session_id": str(task.id)},
    )

    return {
        "question_id": str(question.id),
        "correct_answer": (answer.correct_answer if answer else None) or "",
        "analysis": (answer.analysis if answer else None) or "",
    }


# ================================================================== 结算
def _latest_attempts(
    db: Session,
    *,
    student_id: uuid.UUID,
    question_ids: Sequence[uuid.UUID],
    since: Any,
) -> Dict[uuid.UUID, Attempt]:
    """按题取本次练习期间的**最后一次**作答（C3：正确率只认 attempts）。

    Args:
        db: 数据库会话。
        student_id: 学生 ID。
        question_ids: 本次练习的题目。
        since: 会话开始时间（早于它的历史作答不算本次）。

    Returns:
        ``{question_id: Attempt}``，只含本次练习内答过的题。
    """
    if not question_ids:
        return {}
    rows = (
        db.execute(
            select(Attempt)
            .where(
                Attempt.student_id == student_id,
                Attempt.question_id.in_(list(question_ids)),
                Attempt.created_at >= since,
            )
            .order_by(Attempt.created_at.asc())
        )
        .scalars()
        .all()
    )
    latest: Dict[uuid.UUID, Attempt] = {}
    for attempt in rows:
        if attempt.question_id is not None:
            latest[attempt.question_id] = attempt  # 升序遍历，最后一条即最新
    return latest


def _aggregate_kp_results(
    db: Session,
    latest: Dict[uuid.UUID, Attempt],
    question_ids: Sequence[uuid.UUID],
) -> Dict[str, Dict[str, Any]]:
    """把本次练习的答题结果按知识点聚合（复习卡流建档用）。

    Args:
        db: 数据库会话。
        latest: ``_latest_attempts`` 返回值（{question_id: Attempt}）。
        question_ids: 本次练习的题目 ID。

    Returns:
        ``{kp_id(str): {"correct": bool, "attempts": int}}``
        —— 只含**有知识点归属**的题：该 kp 下所有题全对则 correct=True，
        有任一题答错则 False；确实无 kp 的题不参与聚合（不进复习卡）。
    """
    if not latest:
        return {}
    mapped = list(
        db.execute(
            select(Question.id, Question.kp_id).where(
                Question.id.in_(list(question_ids)),
                Question.kp_id.isnot(None),
            )
        ).all()
    )
    results: Dict[str, Dict[str, Any]] = {}
    for qid, kp_id in mapped:
        attempt = latest.get(qid)
        if attempt is None or kp_id is None:
            continue
        key = str(kp_id)
        cur = results.setdefault(key, {"correct": True, "attempts": 0})
        cur["attempts"] += 1
        if not attempt.is_correct:
            cur["correct"] = False
    return results


def _weak_points(
    db: Session,
    wrong_question_ids: Sequence[uuid.UUID],
) -> List[Dict[str, Any]]:
    """把做错的题按知识点聚合成薄弱点清单（结果页用）。

    KF 知识树 L2（design §4.5 R4）：分组键先 ``rollup()`` 到 L1、``kp_name`` 取 L1 名。
    不聚合的话，一次 10 题的练习会被稀疏 L2 打散成 7~8 个"薄弱点"，每个只错 1 题，
    TopN 完全失真。

    Args:
        db: 数据库会话。
        wrong_question_ids: 做错的题目 ID。

    Returns:
        ``[{"kp_id","kp_name","wrong_count"}]``，按错题数降序，最多 5 条。
        ``kp_id`` 为空的新题不计入（它们本来就不进掌握度）。
    """
    if not wrong_question_ids:
        return []
    rows = db.execute(
        select(Question.kp_id).where(
            Question.id.in_(list(wrong_question_ids)), Question.kp_id.isnot(None)
        )
    ).all()

    index = load_kp_tree(db)

    # 先 rollup 计数，再一次性取 L1 名称（避免 JOIN 出 L2 名）
    counted: Dict[str, int] = {}
    rolled_ids: List[uuid.UUID] = []
    for (kp_id,) in rows:
        if kp_id is None:
            continue
        rolled = index.rollup(kp_id)
        if rolled is None:  # pragma: no cover - rollup 对非空入参恒有返回
            continue
        key = str(rolled)
        if key not in counted:
            rolled_ids.append(rolled)
        counted[key] = counted.get(key, 0) + 1

    if not counted:
        return []

    name_rows = db.execute(
        select(KnowledgePoint.id, KnowledgePoint.name).where(
            KnowledgePoint.id.in_(rolled_ids)
        )
    ).all()
    names: Dict[str, str] = {
        str(kid): (kname or "未命名知识点") for kid, kname in name_rows
    }

    bucket: Dict[str, Dict[str, Any]] = {}
    for key, wrong_count in counted.items():
        # 与原 JOIN 语义对齐：知识点行不存在（脏外键）的不计入
        if key not in names:
            continue
        bucket[key] = {"kp_id": key, "kp_name": names[key], "wrong_count": wrong_count}

    ordered = sorted(
        bucket.values(),
        key=lambda item: (-int(item["wrong_count"]), str(item["kp_name"])),
    )
    return ordered[:WEAK_POINT_LIMIT]


def _resolve_kp_name(db: Session, question_id: uuid.UUID) -> Optional[str]:
    """根据 ``Question.kp_id`` 取出 L1/L2 知识点名（仅展示用，可空）。"""
    row = db.execute(
        select(KnowledgePoint.name)
        .join(Question, Question.kp_id == KnowledgePoint.id)
        .where(Question.id == question_id)
    ).first()
    if not row:
        return None
    name = row[0]
    return str(name).strip() if name else None


def _human_error_text(kp_name: Optional[str], error_type_code: str) -> str:
    """错题入本时的人话 error_reason 文案（设计 §1.7 / §5.2 P1-I）。

    格式：「知识点「{kp_name}」{五类中文标签}」；kp_name 为空时退化为「该题{标签}」。
    上限 ``HUMAN_ERROR_TEXT_LIMIT``（=100）字；超过截断并加省略号。
    """
    label = error_label(error_type_code) or "其他"
    if kp_name:
        text = f"知识点「{kp_name}」{label}"
    else:
        text = f"该题{label}"
    if len(text) > HUMAN_ERROR_TEXT_LIMIT:
        text = text[: HUMAN_ERROR_TEXT_LIMIT - 1] + "…"
    return text


def finish_session(db: Session, *, student_id: Any, session_id: Any) -> Dict[str, Any]:
    """结算一次练习：错题入本 + 掌握度缓存失效 + 统计出参。

    口径：

    * **C3** 正确率从 ``attempts`` 重算，``items`` 只用来确定「本次练了哪些题」；
    * **C4** 仅 ``is_correct=False`` 的题 ``record_mistake``（幂等，同题未攻克不重复建档）；
    * **裁决②** 只对 ``kp_id`` 非空的题调 ``invalidate_cache_for_question``，
      ``kp_id IS NULL`` 的 docx 新题直接跳过（它们本来就不进掌握度/周报）；
    * ``mastery_refreshed`` = 「本次是否练到了**可归因**知识点」，而不是
      缓存 UPDATE 的行数（新学生没有缓存行，按行数判断会恒 False，闭环断裂）；
    * 幂等：重复 finish 不会重复建错题（``record_mistake`` 内置幂等），
      ``mistakes_created`` 会如实变成 0。

    Args:
        db: 数据库会话（内部 commit）。
        student_id: 学生 ID。
        session_id: 练习会话 ID。

    Returns:
        契约字段 ``{"session_id","total","correct","wrong","mistakes_created","mastery_refreshed"}``
        **超集**，另附结果页需要的 ``accuracy``/``answered``/``unmapped_count``/
        ``items``/``weak_points``（附加字段，不破坏契约）。

    Raises:
        ApiError: code=404 会话不存在或不属于当前学生。
    """
    sid = to_uuid(student_id, "student_id")
    task = _load_session(db, student_id=sid, session_id=session_id)

    items = _normalize_items(task.items)
    question_ids: List[uuid.UUID] = []
    for item in items:
        try:
            question_ids.append(uuid.UUID(str(item.get("question_id"))))
        except (TypeError, ValueError):
            logger.warning(
                "练习会话 %s 的 items 里有非法 question_id：%s", task.id, item
            )

    latest = _latest_attempts(
        db, student_id=sid, question_ids=question_ids, since=task.generated_at
    )

    correct_ids: List[uuid.UUID] = []
    wrong_ids: List[uuid.UUID] = []
    for qid in question_ids:
        attempt = latest.get(qid)
        if attempt is None:
            continue
        (correct_ids if attempt.is_correct else wrong_ids).append(qid)

    # ---- C4 错题自动入本（幂等）------------------------------------------
    # 设计 §1.7 / Q5 拍板：
    #   - ``error_type`` 仍写五类机器码（聚合 / 画像 / 雷达图 / 家长周报需要）；
    #   - ``error_reason`` 走人话（家长可读），由 ``_human_error_text`` 生成。
    # 练习模块入口走这里；AI 老师入口另走 ``ai_exercise_service._safe_error_code``，
    # 两条路径共存，机器码落库口径一处不改。
    mistakes_created = 0
    # 缓存一次题 → 知识点名查表，循环里 N+1 会拖垮结算（最多几十道题）
    kp_name_cache: Dict[uuid.UUID, Optional[str]] = {}
    for qid in wrong_ids:
        before = db.execute(
            select(Mistake.id).where(
                Mistake.student_id == sid,
                Mistake.question_id == qid,
                Mistake.solved.is_(False),
            )
        ).first()
        if qid not in kp_name_cache:
            kp_name_cache[qid] = _resolve_kp_name(db, qid)
        kp_name = kp_name_cache[qid]
        # 五类机器码：默认 CALC（设计 §1.7：占位以「计算错」为兜底；后续如要更
        # 细分可接 judge_engine 的 outcome.reason 走 classify_error()）。本期简化
        # 为 CALC 不引入新枚举值，**这不影响 AI 老师入口**（它走自己的归一路径）。
        human_reason = _human_error_text(kp_name, "CALC")
        learning_service.record_mistake(
            db,
            student_id=sid,
            question_id=qid,
            error_type="CALC",
            error_reason=human_reason,
        )
        if before is None:
            mistakes_created += 1

    # ---- 裁决②：只对可归因的题失效掌握度缓存 -------------------------------
    #
    # ⚠️ ``mastery_refreshed`` 的语义是「本次练习**是否触达了可归因知识点**」，
    # 不是「UPDATE 影响了几行」。``profile_service.touch_mastery_practiced()``
    # 只更新**已存在**的 ``student_mastery`` 缓存行，新学生第一次练习时压根
    # 没有缓存行 → 返回 0 → 若按行数判断会恒为 False，闭环信号直接断裂。
    # 但语义上这次练习确实影响了掌握度（下次读画像时会懒计算算进去），
    # 所以这里用 ``mapped``（本次练到的可归因题）来判定；``refreshed_rows``
    # 降级为内部日志用的观测值，不再进出参。
    refreshed_rows = 0
    stamp = utcnow()
    practiced_ids = correct_ids + wrong_ids
    mapped: List[uuid.UUID] = []
    if practiced_ids:
        mapped = list(
            db.execute(
                select(Question.id).where(
                    Question.id.in_(practiced_ids), Question.kp_id.isnot(None)
                )
            )
            .scalars()
            .all()
        )
        for qid in mapped:
            try:
                refreshed_rows += profile_service.invalidate_cache_for_question(
                    db,
                    student_id=sid,
                    question_id=qid,
                    practiced_at=stamp,
                )
            except Exception:  # noqa: BLE001 - 缓存失效失败不能拖垮结算主流程
                logger.warning(
                    "练习结算刷新画像缓存失败 session=%s question=%s",
                    task.id,
                    qid,
                    exc_info=True,
                )
        if refreshed_rows:
            db.commit()
        logger.debug(
            "练习结算掌握度：session=%s 可归因题 %d 道，缓存失效 %d 行",
            task.id,
            len(mapped),
            refreshed_rows,
        )

    # ---- V6.5 复习卡流联动：为本次练到的知识点自动建档 FSRS 复习卡片 -------
    # Review.vue 复习页的卡片来自 review_cards 表；若练习结算不建档，
    # 学生永远没有复习卡片可刷。这里按知识点聚合本次答题结果：
    #   答对 → 初始 rating=3(Good，隔日到期)；答错 → 1(Again，近期再出现)。
    # 幂等（ReviewService.create_card_for_knowledge_point 已存在则跳过），
    # 单独 try/except 兜底——建卡失败不影响错题/掌握度主链路。
    review_synced = review_service.upsert_cards_from_practice(
        sid,
        _aggregate_kp_results(db, latest, practiced_ids),
        now=stamp,
    )
    logger.debug(
        "练习结算复习卡流：session=%s 新建 %d 张，跳过 %d 张（共 %d 个知识点）",
        task.id,
        review_synced["created"],
        review_synced["skipped"],
        review_synced["total"],
    )

    # ---- 会话收尾 ---------------------------------------------------------
    task.status = SESSION_DONE
    task.finished_at = stamp
    db.add(task)
    db.commit()
    db.refresh(task)

    unmapped_count = 0
    if question_ids:
        unmapped_count = int(
            db.execute(
                select(func.count())
                .select_from(Question)
                .where(Question.id.in_(question_ids), Question.kp_id.is_(None))
            ).scalar_one()
        )

    total = len(question_ids)
    correct = len(correct_ids)
    wrong = len(wrong_ids)
    answered = correct + wrong

    detail: List[Dict[str, Any]] = []
    for item in items:
        raw_id = str(item.get("question_id"))
        detail.append(
            {
                "question_id": raw_id,
                "status": item.get("status", ITEM_PENDING),
                "is_correct": item.get("is_correct"),
                "level": item.get("level"),
                "selected_answer": item.get("selected_answer"),
                "duration_sec": item.get("duration_sec"),
            }
        )

    return {
        "session_id": str(task.id),
        "total": total,
        "correct": correct,
        "wrong": wrong,
        "mistakes_created": mistakes_created,
        # 语义 = 本次练习是否触达了可归因知识点（见上方注释），不是 UPDATE 行数
        "mastery_refreshed": bool(mapped),
        # ---- 契约超集：结果页（F4）需要，纯附加不破坏既有字段 ----
        "answered": answered,
        "accuracy": round(correct / answered, 4) if answered else 0.0,
        "unmapped_count": unmapped_count,
        "items": detail,
        "weak_points": _weak_points(db, wrong_ids),
    }


# ================================================================== AI 引导
def sanitize_guide_reply(
    reply: Optional[str],
    *,
    q_type: Optional[str],
    options: Any,
    correct_answer: Optional[str],
    context: str = "",
) -> str:
    """引导语出库前的 **no-leak 闸门**（V1 铁律 / C11+C12 的代码层兜底）。

    这是个**独立的可单测函数**：不碰数据库、不抛异常，只做「判泄题 → 换话术」。
    ``guide_question`` 在返回前必须过一遍它。

    Args:
        reply: 模型给出的引导语（可能为空）。
        q_type: 题型。
        options: ``Question.options``。
        correct_answer: ``Answer.correct_answer``。
        context: 打日志用的上下文（会话/题目 ID），仅用于告警可回溯。

    Returns:
        安全的引导语：原文没泄题就原样返回；空回复或涉嫌泄题一律返回
        ``GUIDE_FALLBACK_REPLY``。
    """
    text = _clean(reply)
    if not text:
        return GUIDE_FALLBACK_REPLY

    if judge_engine.leaks_answer(text, q_type, options, correct_answer):
        logger.warning(
            "练习引导疑似泄题，已丢弃模型回复改用兜底话术（%s）：%s",
            context or "no-context",
            text[:120],
        )
        return GUIDE_FALLBACK_REPLY

    return text


def guide_question(
    db: Session,
    *,
    student_id: Any,
    session_id: Any,
    question_id: Any,
    work: Optional[str] = None,
) -> Dict[str, Any]:
    """Q3 主按钮「让老师带我想一想」：走 ``practice_guide`` 提示词请方舟点评。

    该函数是**同步**的（内部调 ``ark_respond``），async 路由必须用
    ``run_in_threadpool`` 包裹。

    **首轮不给答案（V1 铁律 / C11+C12）有两道闸门**：

    1. 提示词层：``practice_guide.PROMPT`` 约束「不要立刻判对错，也不要直接
       公布答案」；
    2. **代码层兜底**：返回前用 ``judge_engine.leaks_answer()`` 复核，
       一旦模型抖出了标准答案（``选B`` / ``答案是B`` / ``√`` / ``×`` /
       标准选项正文），**整条回复直接丢弃**，改用引导式兜底话术
       ``GUIDE_FALLBACK_REPLY``，并打一条 ``logger.warning`` 便于回溯。

    只靠提示词是不够的——模型只要回一句「答案是 B，因为…」，孩子就直接看到
    答案，V1 铁律当场破功。

    Args:
        db: 数据库会话。
        student_id: 学生 ID。
        session_id: 练习会话 ID。
        question_id: 题目 ID。
        work: 孩子当前的作答过程；为空时给一句占位说明。

    Returns:
        ``{"reply", "ask_final_confirm"}``。

    Raises:
        ApiError: code=400 题目不在本次练习中；code=404 会话/题目不存在；
            code=500 方舟调用失败（由 ``ArkError`` 转成统一响应）。
    """
    sid = to_uuid(student_id, "student_id")
    qid = to_uuid(question_id, "question_id")
    task = _load_session(db, student_id=sid, session_id=session_id)

    if _find_item(_normalize_items(task.items), qid) is None:
        raise ApiError(CODE_BAD_REQUEST, "这道题不在本次练习中")

    question, answer = _load_question(db, qid)
    item = _find_item(_normalize_items(task.items), qid)
    guide_history = _recent_guide_history(item)
    stuck = _is_stuck_text(work)

    stem = question.content or ""
    options = judge_engine.normalize_options(question.options)
    if options:
        stem = stem + "\n" + "\n".join(f"{item.key}. {item.text}" for item in options)

    history_text = (
        "\n".join(
            f"第 {index} 轮：学生：{turn['work'] or '（未写过程）'}；老师：{turn['reply']}"
            for index, turn in enumerate(guide_history, start=1)
        )
        or "（这是第一轮，还没有之前的问答）"
    )
    prompt = practice_guide.PROMPT.replace("{{QUESTION}}", stem).replace(
        "{{WORK}}", _clean(work) or "（孩子还没写下过程，只是点开了求助）"
    )
    prompt += (
        "\n\n【本题之前的引导记录】\n"
        f"{history_text}\n"
        "请先看清之前老师已经问过什么。绝对不要原样重复之前的问题。"
    )
    if stuck:
        prompt += (
            "\n孩子这次明确表示不会/不知道。先接住他，再从这道题的数字、单位、图形或"
            "已知条件里给一个具体小线索，最后只问一个更小的问题；不要继续问上一轮同一个问题。"
        )

    try:
        raw = ark_client.ark_respond(messages=[{"role": "user", "content": prompt}])
    except ark_client.ArkError as exc:
        logger.warning(
            "练习引导调用方舟失败 session=%s question=%s: %s", task.id, qid, exc
        )
        raise ApiError(CODE_SERVER_ERROR, "老师这会儿有点忙，稍后再点一次试试") from exc

    reply = _clean(raw)
    ask_final_confirm = False
    try:
        parsed = extract_json(raw)
    except Exception:  # noqa: BLE001 - 模型没给 JSON 时按纯文本处理，不影响主流程
        parsed = None
    if isinstance(parsed, dict):
        reply = _clean(parsed.get("reply")) or reply
        ask_final_confirm = bool(
            parsed.get("askFinalConfirm") or parsed.get("ask_final_confirm")
        )

    reply = sanitize_guide_reply(
        reply,
        q_type=question.type_,
        options=question.options,
        correct_answer=answer.correct_answer if answer else None,
        context=f"session={task.id} question={qid}",
    )

    previous_replies = {turn["reply"] for turn in guide_history if turn.get("reply")}
    if (stuck and reply in previous_replies) or reply in previous_replies:
        logger.warning(
            "练习引导检测到重复回复，已切换为题目关键条件提示 session=%s question=%s",
            task.id,
            qid,
        )
        stuck_round = (
            sum(1 for turn in guide_history if _is_stuck_text(turn.get("work"))) + 1
        )
        reply = _stuck_hint_reply(question, stuck_round)

    _save_guide_turn(db, task, qid, work=work, reply=reply)

    return {
        "question_id": str(qid),
        "reply": reply,
        "ask_final_confirm": ask_final_confirm,
        "prompt_version": practice_guide.VERSION,
    }


async def guide_question_async(
    db: Session,
    *,
    student_id: Any,
    session_id: Any,
    question_id: Any,
    work: Optional[str] = None,
) -> Dict[str, Any]:
    """``guide_question`` 的异步版：await ``ark_client.ark_respond_async``。

    Args、Returns、Raises 与 ``guide_question`` 完全一致（DB 写操作仍是同步调用）。
    """
    sid = to_uuid(student_id, "student_id")
    qid = to_uuid(question_id, "question_id")
    task = _load_session(db, student_id=sid, session_id=session_id)

    if _find_item(_normalize_items(task.items), qid) is None:
        raise ApiError(CODE_BAD_REQUEST, "这道题不在本次练习中")

    question, answer = _load_question(db, qid)
    item = _find_item(_normalize_items(task.items), qid)
    guide_history = _recent_guide_history(item)
    stuck = _is_stuck_text(work)

    stem = question.content or ""
    options = judge_engine.normalize_options(question.options)
    if options:
        stem = stem + "\n" + "\n".join(f"{item.key}. {item.text}" for item in options)

    history_text = (
        "\n".join(
            f"第 {index} 轮：学生：{turn['work'] or '（未写过程）'}；老师：{turn['reply']}"
            for index, turn in enumerate(guide_history, start=1)
        )
        or "（这是第一轮，还没有之前的问答）"
    )
    prompt = practice_guide.PROMPT.replace("{{QUESTION}}", stem).replace(
        "{{WORK}}", _clean(work) or "（孩子还没写下过程，只是点开了求助）"
    )
    prompt += (
        "\n\n【本题之前的引导记录】\n"
        f"{history_text}\n"
        "请先看清之前老师已经问过什么。绝对不要原样重复之前的问题。"
    )
    if stuck:
        prompt += (
            "\n孩子这次明确表示不会/不知道。先接住他，再从这道题的数字、单位、图形或"
            "已知条件里给一个具体小线索，最后只问一个更小的问题；不要继续问上一轮同一个问题。"
        )

    try:
        raw = await ark_client.ark_respond_async(
            messages=[{"role": "user", "content": prompt}]
        )
    except ark_client.ArkError as exc:
        logger.warning(
            "练习引导调用方舟失败 session=%s question=%s: %s", task.id, qid, exc
        )
        raise ApiError(CODE_SERVER_ERROR, "老师这会儿有点忙，稍后再点一次试试") from exc

    reply = _clean(raw)
    ask_final_confirm = False
    try:
        parsed = extract_json(raw)
    except Exception:  # noqa: BLE001 - 模型没给 JSON 时按纯文本处理，不影响主流程
        parsed = None
    if isinstance(parsed, dict):
        reply = _clean(parsed.get("reply")) or reply
        ask_final_confirm = bool(
            parsed.get("askFinalConfirm") or parsed.get("ask_final_confirm")
        )

    reply = sanitize_guide_reply(
        reply,
        q_type=question.type_,
        options=question.options,
        correct_answer=answer.correct_answer if answer else None,
        context=f"session={task.id} question={qid}",
    )

    previous_replies = {turn["reply"] for turn in guide_history if turn.get("reply")}
    if (stuck and reply in previous_replies) or reply in previous_replies:
        logger.warning(
            "练习引导检测到重复回复，已切换为题目关键条件提示 session=%s question=%s",
            task.id,
            qid,
        )
        stuck_round = (
            sum(1 for turn in guide_history if _is_stuck_text(turn.get("work"))) + 1
        )
        reply = _stuck_hint_reply(question, stuck_round)

    _save_guide_turn(db, task, qid, work=work, reply=reply)

    return {
        "question_id": str(qid),
        "reply": reply,
        "ask_final_confirm": ask_final_confirm,
        "prompt_version": practice_guide.VERSION,
    }


__all__ = [
    "draw_questions",
    "draw_questions_by_plan",
    "create_session",
    "submit_answer",
    "reveal_answer",
    "finish_session",
    "guide_question",
    "guide_question_async",
    "sanitize_guide_reply",
    "GUIDE_FALLBACK_REPLY",
    "PRACTICE_TASK_TYPE",
    "PRACTICE_SOURCE",
    "DEFAULT_COUNT",
    "MIN_COUNT",
    "MAX_COUNT",
    "RECENT_DAYS",
    "ITEM_PENDING",
    "ITEM_DONE",
    "SESSION_DOING",
    "SESSION_DONE",
    # T5/T11 新增（保持稳定导入点，方便测试与脚本复用）
    "RELAXATION_LABELS",
    "HUMAN_ERROR_TEXT_LIMIT",
    "_options_status",
    "_draw_with_relaxation",
    "_sample_and_meta",
    "_human_error_text",
    "OPTIONS_STATUS_COMPLETE",
    "OPTIONS_STATUS_PARTIAL",
    "OPTIONS_STATUS_MISSING",
    "OPTIONS_STATUS_NA",
]  # noqa: E501
