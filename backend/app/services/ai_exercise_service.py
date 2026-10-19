"""AI 练习题服务：落库（D1）+ 对话式引导（D3-b）+ 判错即时入本。

为什么需要这一层
----------------
``orchestrator.generate_exercise()`` 生成的练习题原本**只在内存里飘着**，
没有 ``questions.id`` → ``learning_service.record_mistake()`` 的外键无处可挂 →
「AI 出的题做错了也进不了错题本」（PRD P0-5 硬阻塞）。

D1 的解法是**零迁移**落库：全部复用既有列，靠两个字段把 AI 题与题库题隔离开——

* ``status='archived'``：``practice_service._candidate_rows()`` 的抽题硬条件是
  ``status='active'``，归档题**永远不会**被抽进题库练习，零污染；
* ``source_doc='ai_gen'``：非 NULL，绕开 ``schema_guard`` 的「``source_doc IS NULL``
  必须 ``kp_verified=TRUE``」守卫，同时 ``idx_questions_source_doc`` 现成可用于统计/清理。

事务纪律
--------
* 写库一律用**调用方传入的 ``db``**，本模块不自建会话；
* ``persist_exercises()`` 只 ``flush()`` 不 ``commit()``——由 ``orchestrator`` 统一收口；
* ``judge_and_record()`` 会走 ``record_mistake()``（内部自带 ``commit``），
  所以调用它之前**不能有半截未提交的写**；
* 全部为**同步**函数，内含方舟调用，API 层必须 ``run_in_threadpool`` 包裹。
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import ark_client
from app.ai._num_utils import sole_number
from app.ai.agents._json_utils import extract_json
from app.ai.intent import (
    MODE_DEMO,
    MODE_HINT,
    MODE_JUDGE,
    UNGUARDED_MODES,
    detect_mode,
)
from app.ai.orchestrator import EXERCISE_GUIDE_PREFIX, EXERCISE_JUDGE_PREFIX, EXERCISE_PREFIX
from app.ai.prompts import practice_guide, summary as summary_prompt
from app.core.error_categories import (
    ERROR_CALC,
    ERROR_CATEGORIES,
    error_label,
    normalize_error,
)
from app.core.response import (
    ApiError,
    CODE_BAD_REQUEST,
    CODE_NOT_FOUND,
    CODE_SERVER_ERROR,
)
from app.models.ai import AiMessage, AiSession
from app.models.content import (
    QUESTION_STATUS_ARCHIVED,
    SUBJECT_DEFAULT,
    Answer,
    KnowledgePoint,
    Question,
)
from app.models.learning import Mistake
from app.models.user import Student
from app.services import learning_service, profile_service, sympy_judge
from app.services.learning_service import to_uuid
from app.services.practice_service import GUIDE_FALLBACK_REPLY, sanitize_guide_reply

logger = logging.getLogger("app.services.ai_exercise")

#: AI 生成题在 ``questions.source_doc`` 上的固定标记（D1 隔离位）
AI_SOURCE_DOC: str = "ai_gen"

#: DB CHECK ``difficulty IN (1,3,5)``——落库前的最后一道断言
ALLOWED_DIFFICULTY: tuple[int, ...] = (1, 3, 5)
DEFAULT_DIFFICULTY: int = 3

#: AI 出题枚举 → DB 题型白名单的**安全子集**映射。
#:
#: ``content.py`` 明令「AI 枚举 ≠ DB 白名单，切勿顺手对齐」，所以只映射语义
#: 确定的三项，``函数``/``证明`` 这类小学题库里压根没有的一律留 ``None``。
_TYPE_MAP: Dict[str, str] = {
    "计算": "计算",
    "应用": "应用",
    "几何": "图形",
}

#: 判错入本时写进 ``mistakes.error_reason`` 的前缀
MISTAKE_REASON_PREFIX: str = "AI 练习作答错误"

# ------------------------------------------------------------------ 合法性检查
#: AI 生成题落库前轻量合法性检查的结果标记，写进出题事件 meta（``EXERCISE_PREFIX``
#: 消息）供审计回溯——**只标记不拦截**，避免误杀 LLM 的合理出题。
ANSWER_CHECK_KEY: str = "answer_check"

#: 标准答案非空，且（SymPy 覆盖题型）可被 SymPy 解析
ANSWER_CHECK_OK: str = "ok"
#: 标准答案为空——判题将失去文本比对信号，只能靠模型 verdict
ANSWER_CHECK_EMPTY: str = "empty"
#: SymPy 覆盖题型但标准答案不可解析——判题时 SymPy 仲裁层会静默降级
ANSWER_CHECK_UNPARSEABLE: str = "unparseable"
#: 非 SymPy 覆盖题型（应用题/图形题等主观题）——不做解析校验，属预期形态
ANSWER_CHECK_SKIPPED: str = "skipped"

#: 模型没给出可用点评时的兜底判词
DEFAULT_WRONG_TEXT: str = "这道题的答案还不对，我们回头一起把过程再走一遍。"
DEFAULT_CORRECT_TEXT: str = "答对了！你是怎么想到这一步的？说给我听听。"

#: 无作答记录时喂给提示词的占位说明（与 ``practice_service`` 口径一致）
EMPTY_WORK_PLACEHOLDER: str = "（孩子还没写下过程，只是点开了求助）"


# ============================================================ 通用小工具
def _clean(value: Any) -> str:
    """把任意输入压成去首尾空白的字符串，``None`` → ``""``。"""
    if value is None:
        return ""
    return str(value).strip()


def _map_question_type(ai_type: Any) -> Optional[str]:
    """AI 题型枚举 → DB 题型白名单；无安全对应时返回 ``None``。

    Args:
        ai_type: AI 给的题型（``计算``/``应用``/``几何``/``函数``/``证明``）。

    Returns:
        DB 白名单中的题型字符串，或 ``None``（不写这一列）。
    """
    return _TYPE_MAP.get(_clean(ai_type))


def _normalize_difficulty(raw: Any) -> int:
    """把难度收敛到 DB CHECK 允许的 1/3/5。

    ``agents/exercise.py:_normalize_difficulty`` 已经收敛过一次，这里是**第二道
    断言**——一旦上游回归，宁可落一道难度 3 的题，也不能让整批插入被 CHECK 打回。
    """
    try:
        value = int(float(_clean(raw)))
    except (TypeError, ValueError):
        return DEFAULT_DIFFICULTY
    return value if value in ALLOWED_DIFFICULTY else DEFAULT_DIFFICULTY


def _resolve_kp_id(db: Session, knowledge: Any) -> Optional[uuid.UUID]:
    """按知识点**名称精确匹配** ``knowledge_points.id``，匹配不到返回 ``None``。

    仓库里没有现成的 name → id 解析器，模糊匹配容易把「两位数乘法」错挂到
    「两位数加法」上，宁可不挂：``kp_id=None`` 的题错题仍能入本，只是不进画像。
    """
    name = _clean(knowledge)
    if not name:
        return None
    return db.execute(
        select(KnowledgePoint.id).where(KnowledgePoint.name == name)
    ).scalar_one_or_none()


def _student_grade(db: Session, student_id: Any) -> Optional[int]:
    """取学生年级用于 ``questions.grade``；取不到留 ``None``（不做任何换算）。"""
    try:
        sid = to_uuid(student_id, "student_id")
    except ApiError:
        return None
    return db.execute(select(Student.grade).where(Student.id == sid)).scalar_one_or_none()


def _check_answer_legality(answer_text: str, db_q_type: Optional[str]) -> str:
    """AI 生成题落库前的**轻量**合法性检查（只记录，不拦截出题主链路）。

    检查项：

    1. ``correct_answer`` 非空——空答案会让判题失去文本比对信号，只能靠
       模型 ``verdict`` 兜底；
    2. 题型落在 SymPy 精确判覆盖范围（``sympy_judge.SYMPY_SUPPORTED_TYPES``）内时，
       标准答案必须可被 SymPy 解析——不可解析意味着判题链路的 SymPy 仲裁层会
       静默降级，值得留痕告警。

    应用题/图形题等主观题型**不做**解析校验：它们的答案本就是文字/式子混合，
    SymPy 解析失败是常态，记 warning 只会制造日志噪音。这正对应「SymPy 是仲裁层
    不是替代层——只覆盖可安全解析的数值/表达式题型」的定位。

    Args:
        answer_text: 清洗后的标准答案文本（``_clean(item["a"])``）。
        db_q_type: 映射后的 DB 题型（``Question.type_`` 口径，可为 ``None``）。

    Returns:
        ``ANSWER_CHECK_OK`` / ``ANSWER_CHECK_EMPTY`` /
        ``ANSWER_CHECK_UNPARSEABLE`` / ``ANSWER_CHECK_SKIPPED`` 之一。
    """
    if not answer_text:
        return ANSWER_CHECK_EMPTY
    if db_q_type is None or db_q_type not in sympy_judge.SYMPY_SUPPORTED_TYPES:
        return ANSWER_CHECK_SKIPPED
    try:
        # ``_normalize_answer`` 是 sympy_judge 对「答案文本 → SymPy 对象」归一化的
        # 唯一入口，已列入其 ``__all__`` 供跨模块复用；返回 None 即不可解析。
        parsed = sympy_judge._normalize_answer(answer_text)
    except Exception:  # noqa: BLE001 —— 校验层异常按「不可解析」留痕，绝不拦截落库
        logger.warning("标准答案 SymPy 解析校验异常：%r", answer_text[:120], exc_info=True)
        return ANSWER_CHECK_UNPARSEABLE
    return ANSWER_CHECK_OK if parsed is not None else ANSWER_CHECK_UNPARSEABLE


# ============================================================ T6 落库
def _emit_dna_event(
    db: Session,
    *,
    student_id: Any,
    event_type: str,
    question_id: Optional[uuid.UUID] = None,
    knowledge_point_id: Optional[uuid.UUID] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """DNA-004 行为事件埋点（旁路，容错模式与 practice_service 对齐）。

    任何异常只记 warning，绝不拖垮小结主流程。延迟导入
    ``student_dna_service``：与 orchestrator 同款约定，规避 ``app.services``
    内部模块级 import 环。本函数只 ``flush``，由调用方现有 ``db.commit()``
    收口。
    """
    try:
        from app.services.student_dna_service import record_learning_event

        record_learning_event(
            db,
            student_id=student_id,
            event_type=event_type,
            question_id=question_id,
            knowledge_point_id=knowledge_point_id,
            meta=meta,
        )
    except Exception:  # noqa: BLE001 - 埋点失败不影响主流程
        logger.warning(
            "chat DNA 埋点失败 event=%s meta=%s", event_type, meta, exc_info=True
        )


def persist_exercises(
    db: Session,
    *,
    session: AiSession,
    exercises: Sequence[Dict[str, Any]],
    student_id: Any = None,
) -> List[Optional[uuid.UUID]]:
    """把 AI 生成的练习题按 §3.6 映射表落成归档题 + 标准答案。

    单题落库失败**不中断整批**：该位置返回 ``None``，出题功能本身照常可用
    （只是那一道题做错了进不了错题本），比整个 ``/exercise/gen`` 500 强。

    Args:
        db: 调用方传入的 SQLAlchemy 会话（本函数只 ``flush``，不 ``commit``）。
        session: 所属 ``AiSession``，用于取 ``student_id`` 与打日志。
        exercises: ``exercise.gen_exercises()`` 的输出，元素含
            ``q/a/type/knowledge/difficulty/role/tag``。
        student_id: 学生 ID；缺省时从 ``session.student_id`` 取。

    Returns:
        与 ``exercises`` **等长**的 ``question_id`` 列表，失败位置为 ``None``。
    """
    items = list(exercises or [])
    if not items:
        return []

    owner_id = student_id if student_id is not None else getattr(session, "student_id", None)
    grade = _student_grade(db, owner_id)

    # ⚠️ 整批一个事务：SQLite 上做「单题失败只回滚这一题」需要 SAVEPOINT，
    #    而 db.rollback() 会把**同批已 flush 的题**一起冲掉，比全批失败更糟。
    #    所以这里的降级口径是「要么全落、要么全不落」——出题结果照常返回前端，
    #    只是 question_id 全为 None（做错了进不了错题本，但功能不 500）。
    try:
        pending: List[Optional[Question]] = []
        for index, item in enumerate(items):
            # ★ 落库前轻量合法性检查：先算并写回事件 meta，保证每道题都有标记；
            #   只记 warning，不拦截——LLM 的合理出题绝不被校验层误杀。
            answer_text = _clean(item.get("a"))
            answer_check = _check_answer_legality(answer_text, _map_question_type(item.get("type")))
            item[ANSWER_CHECK_KEY] = answer_check
            if answer_check == ANSWER_CHECK_EMPTY:
                logger.warning(
                    "AI 练习题第 %d 道标准答案为空，判题将回落到模型 verdict session=%s",
                    index,
                    session.id,
                )
            elif answer_check == ANSWER_CHECK_UNPARSEABLE:
                logger.warning(
                    "AI 练习题第 %d 道标准答案无法被 SymPy 解析（%r），SymPy 仲裁层将降级 session=%s",
                    index,
                    answer_text[:120],
                    session.id,
                )

            content = _clean(item.get("q"))
            if not content:
                logger.warning("AI 练习题第 %d 道题干为空，跳过落库 session=%s", index, session.id)
                pending.append(None)
                continue

            kp_id = _resolve_kp_id(db, item.get("knowledge"))
            question = Question(
                content=content,
                subject=SUBJECT_DEFAULT,
                grade=grade,
                difficulty=_normalize_difficulty(item.get("difficulty")),
                kp_id=kp_id,
                type_=_map_question_type(item.get("type")),
                options=None,
                # ★ 隔离位一：归档 → 永不进入 practice_service 的抽题池
                status=QUESTION_STATUS_ARCHIVED,
                # ★ 隔离位二：非 NULL 来源 → 绕开 schema_guard 的 kp_verified 守卫
                source_doc=AI_SOURCE_DOC,
                edition=None,
                source_type=None,
                section=None,
                # 只有真挂上知识点才算「已复核」，否则画像会统计到一个空 kp
                kp_verified=kp_id is not None,
            )
            db.add(question)
            pending.append(question)

        db.flush()  # 一次性拿到全部 question.id，供 Answer 挂载

        for item, question in zip(items, pending):
            if question is None:
                continue
            db.add(
                Answer(
                    question_id=question.id,
                    correct_answer=_clean(item.get("a")) or None,
                    analysis=_clean(item.get("tag")) or None,
                    distractor_analysis={},
                )
            )
        db.flush()
    except Exception:  # noqa: BLE001 - 落库失败降级为「不落库」，出题本身不受影响
        logger.warning("AI 练习题落库失败 session=%s，本批降级为不落库", session.id, exc_info=True)
        db.rollback()
        return [None] * len(items)

    return [question.id if question is not None else None for question in pending]


# ============================================================ 归属校验
def _load_ai_session(db: Session, *, student_id: Any, session_id: Any) -> AiSession:
    """取本人的 AI 辅导会话；不存在 404，不是本人的 400。"""
    sid = to_uuid(student_id, "student_id")
    aid = to_uuid(session_id, "session_id")
    session = db.execute(select(AiSession).where(AiSession.id == aid)).scalar_one_or_none()
    if session is None:
        raise ApiError(CODE_NOT_FOUND, "辅导会话不存在")
    if session.student_id != sid:
        raise ApiError(CODE_BAD_REQUEST, "这个会话不是你的")
    return session


def _session_question_ids(db: Session, session_id: uuid.UUID) -> Set[uuid.UUID]:
    """从会话的 ``EXERCISE_PREFIX`` 消息里回捞本会话出过的所有 ``question_id``。

    这是 AI 练习**不依赖 ``practice_sessions``** 的归属校验依据：一道题只有既是
    ``source_doc='ai_gen'``、又出现在本会话的练习题清单里，才允许被引导/判定。
    """
    rows = db.execute(
        select(AiMessage.content)
        .where(AiMessage.session_id == session_id, AiMessage.role == "assistant")
        .order_by(AiMessage.created_at.asc())
    ).scalars()

    ids: Set[uuid.UUID] = set()
    for content in rows:
        text = content or ""
        if not text.startswith(EXERCISE_PREFIX):
            continue
        try:
            payload = json.loads(text[len(EXERCISE_PREFIX) :])
        except (TypeError, ValueError):
            logger.warning("会话 %s 的练习题元数据无法解析：%s", session_id, text[:120])
            continue
        if not isinstance(payload, list):
            continue
        for meta in payload:
            if not isinstance(meta, dict):
                continue
            try:
                ids.add(uuid.UUID(str(meta.get("question_id"))))
            except (TypeError, ValueError):
                continue
    return ids


def _latest_exercise_question_ids(messages: Sequence[AiMessage]) -> List[uuid.UUID]:
    """返回会话最近一次生成的练习题，供本轮完成状态和小结使用。"""
    for message in reversed(messages):
        text = message.content or ""
        if not text.startswith(EXERCISE_PREFIX):
            continue
        try:
            payload = json.loads(text[len(EXERCISE_PREFIX) :])
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, list):
            continue
        ids: List[uuid.UUID] = []
        for meta in payload:
            if not isinstance(meta, dict):
                continue
            try:
                ids.append(uuid.UUID(str(meta.get("question_id"))))
            except (TypeError, ValueError):
                continue
        if ids:
            return ids
    return []


def _file_url(image_key: Optional[str]) -> Optional[str]:
    """把可信上传 key 转成站内图片地址，拒绝路径片段。"""
    key = (image_key or "").strip()
    if not key or "/" in key or "\\" in key or key in {".", ".."}:
        return None
    return f"/api/file/{key}"


def _load_ai_question(
    db: Session,
    *,
    session: AiSession,
    question_id: Any,
) -> tuple[Question, Optional[Answer]]:
    """取一道**属于本会话**的 AI 练习题及其标准答案。

    Raises:
        ApiError: 404 题目不存在；400 题目不属于本会话 / 不是 AI 练习题。
    """
    qid = to_uuid(question_id, "question_id")
    question = db.execute(select(Question).where(Question.id == qid)).scalar_one_or_none()
    if question is None:
        raise ApiError(CODE_NOT_FOUND, "题目不存在")
    if question.source_doc != AI_SOURCE_DOC:
        raise ApiError(CODE_BAD_REQUEST, "这道题不是 AI 老师出的练习题")
    if qid not in _session_question_ids(db, session.id):
        raise ApiError(CODE_BAD_REQUEST, "这道题不在本次辅导的练习里")

    answer = db.execute(select(Answer).where(Answer.question_id == qid)).scalar_one_or_none()
    return question, answer


# ============================================================ 模型调用
def _ask_guide_model(
    *, stem: str, work: str, mode: str, context: str, image_b64: Optional[str] = None
) -> Dict[str, Any]:
    """用 ``practice_guide`` 提示词问一次方舟，返回解析后的四元组字段。

    Args:
        stem: 题面。
        work: 孩子的作答记录。
        mode: 本轮模式，仅用于把「讲解模式」这条指令追加到提示词末尾。
        context: 打日志用的上下文。

    Returns:
        ``{"reply", "ask_final_confirm", "verdict", "error_type", "raw"}``，
        其中 ``verdict`` ∈ ``correct/wrong/pending``，``error_type`` 为五类
        机器码之一或 ``None``。

    Raises:
        ApiError: 500 方舟调用失败。
    """
    prompt = practice_guide.PROMPT.replace("{{QUESTION}}", stem).replace(
        "{{WORK}}", work or EMPTY_WORK_PLACEHOLDER
    )
    suffix = _mode_instruction(mode)
    if suffix:
        prompt = f"{prompt}\n\n{suffix}"

    try:
        raw = ark_client.ark_respond(
            messages=[{"role": "user", "content": prompt}], image_b64=image_b64
        )
    except ark_client.ArkError as exc:
        logger.warning("AI 练习引导调用方舟失败 %s: %s", context, exc)
        raise ApiError(CODE_SERVER_ERROR, "老师这会儿有点忙，稍后再点一次试试") from exc

    return _parse_guide_outcome(raw, context=context)


async def _ask_guide_model_async(
    *, stem: str, work: str, mode: str, context: str, image_b64: Optional[str] = None
) -> Dict[str, Any]:
    """``_ask_guide_model`` 的异步版：await ``ark_client.ark_respond_async``。

    Args、Returns、Raises 与 ``_ask_guide_model`` 完全一致。
    """
    prompt = practice_guide.PROMPT.replace("{{QUESTION}}", stem).replace(
        "{{WORK}}", work or EMPTY_WORK_PLACEHOLDER
    )
    suffix = _mode_instruction(mode)
    if suffix:
        prompt = f"{prompt}\n\n{suffix}"

    try:
        raw = await ark_client.ark_respond_async(
            messages=[{"role": "user", "content": prompt}], image_b64=image_b64
        )
    except ark_client.ArkError as exc:
        logger.warning("AI 练习引导调用方舟失败 %s: %s", context, exc)
        raise ApiError(CODE_SERVER_ERROR, "老师这会儿有点忙，稍后再点一次试试") from exc

    return _parse_guide_outcome(raw, context=context)


def _parse_guide_outcome(raw: str, *, context: str) -> Dict[str, Any]:
    """``_ask_guide_model`` 的回复解析（同步 / 异步共用）。"""
    reply = _clean(raw)
    ask_final_confirm = False
    verdict = "pending"
    error_type: Optional[str] = None

    try:
        parsed = extract_json(raw)
    except Exception:  # noqa: BLE001 - 模型没给 JSON 时按纯文本处理
        parsed = None

    if isinstance(parsed, dict):
        reply = _clean(parsed.get("reply")) or reply
        ask_final_confirm = bool(
            parsed.get("askFinalConfirm") or parsed.get("ask_final_confirm")
        )
        verdict = _normalize_verdict(parsed.get("verdict"))
        raw_error = parsed.get("errorType") or parsed.get("error_type")
        if verdict == "wrong":
            error_type = _safe_error_code(raw_error)

    return {
        "reply": reply,
        "ask_final_confirm": ask_final_confirm,
        "verdict": verdict,
        "error_type": error_type,
        "raw": raw,
    }


def _mode_instruction(mode: str) -> str:
    """把 mode 翻成追加到 ``practice_guide`` 末尾的一句系统指令。

    提示词本体是核心教学资产（只做加法），模式差异一律用**后缀**表达。
    """
    if mode == MODE_DEMO:
        return "【本轮系统指令】现在切到讲解模式（DEMO）：孩子已经明确请你讲，请把这道题的解题过程从头完整讲清楚并说出结果，讲完再回扣一个小追问。"
    if mode == MODE_JUDGE:
        return "【本轮系统指令】现在切到评判模式（JUDGE）：孩子在等你判对错，请先明确说对还是不对、错在哪一步，再给一句下一步建议。"
    if mode == MODE_HINT:
        return "【本轮系统指令】现在切到提示模式（HINT）：只给一个方向性的小线索，不要给出完整步骤，也不要说出结果。"
    return ""


def _normalize_verdict(raw: Any) -> str:
    """把模型给的 ``verdict`` 收敛到 ``correct``/``wrong``/``pending``。"""
    value = _clean(raw).lower()
    if value in ("correct", "right", "true", "对"):
        return "correct"
    if value in ("wrong", "incorrect", "false", "错"):
        return "wrong"
    return "pending"


def _safe_error_code(raw: Any) -> str:
    """把模型给的错因收敛到五类机器码；实在归不了类的按「计算错」兜底。

    ``error_categories`` 是**唯一真相源**，这里不新增任何分类；
    ``UNCLASSIFIED`` 不在 ``ERROR_CATEGORIES`` 里，写进 ``mistakes`` 会在画像
    聚合时被丢弃，所以退化成出现频率最高的 ``CALC``，保证错题始终可归因。
    """
    code = normalize_error(raw)
    if code in ERROR_CATEGORIES:
        return code
    return ERROR_CALC


# ============================================================ T7 对话式引导
def guide_exercise(
    db: Session,
    *,
    student_id: Any,
    session_id: Any,
    question_id: Any,
    work: Optional[str] = None,
    work_image_b64: Optional[str] = None,
    work_image_key: Optional[str] = None,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """AI 练习题的对话式逐步引导（对齐 ``practice_service.guide_question``）。

    与题库引导的两点不同：

    1. **不依赖 ``practice_sessions``**：归属校验改为「``source_doc='ai_gen'``
       且 ``question_id`` 出现在本 ``ai_session`` 的练习题清单里」；
    2. **模式感知**：``mode`` 为 ``DEMO``/``JUDGE`` 时**跳过泄题闸门**——
       孩子明确请你讲/请你判，这时候把回复丢掉换兜底话术才是真正的「死板」。

    Args:
        db: 数据库会话；每轮学生过程和老师反馈都会写入本次会话。
        student_id: 学生 ID。
        session_id: AI 辅导会话 ID。
        question_id: 练习题 ID。
        work: 孩子当前的作答过程，可为空。
        mode: 前端显式指定的模式；``None`` 时按 ``work`` 文本自动识别。

    Returns:
        ``{"question_id", "reply", "ask_final_confirm", "verdict",
        "error_type", "mode", "prompt_version"}``。

    Raises:
        ApiError: 400 题目不属于本会话；404 会话/题目不存在；500 方舟调用失败。
    """
    session = _load_ai_session(db, student_id=student_id, session_id=session_id)
    question, answer = _load_ai_question(db, session=session, question_id=question_id)

    text = _clean(work)
    active_mode = detect_mode(text=text, state=session.session_state, override=mode)
    context = f"session={session.id} question={question.id}"
    logger.info("exercise-guide %s mode=%s", context, active_mode)

    outcome = _ask_guide_model(
        stem=question.content or "",
        work=text,
        mode=active_mode,
        context=context,
        image_b64=work_image_b64,
    )

    reply = outcome["reply"]
    if active_mode in UNGUARDED_MODES:
        # 讲解/评判模式：孩子要的就是结果，泄题闸门在这里必须让路
        reply = reply or GUIDE_FALLBACK_REPLY
    else:
        reply = sanitize_guide_reply(
            reply,
            q_type=question.type_,
            options=question.options,
            correct_answer=answer.correct_answer if answer else None,
            context=context,
        )

    guide_record = {
        "question_id": str(question.id),
        "work": text,
        "teacher_reply": reply,
        "verdict": outcome["verdict"],
        "error_type": outcome["error_type"],
        "work_image_url": _file_url(work_image_key),
    }
    db.add(
        AiMessage(
            session_id=session.id,
            role="system",
            content=f"{EXERCISE_GUIDE_PREFIX}{json.dumps(guide_record, ensure_ascii=False)}",
        )
    )
    db.commit()

    return {
        "question_id": str(question.id),
        "reply": reply,
        "ask_final_confirm": bool(outcome["ask_final_confirm"]),
        "verdict": outcome["verdict"],
        "error_type": outcome["error_type"],
        "mode": active_mode,
        "prompt_version": practice_guide.VERSION,
    }


async def guide_exercise_async(
    db: Session,
    *,
    student_id: Any,
    session_id: Any,
    question_id: Any,
    work: Optional[str] = None,
    work_image_b64: Optional[str] = None,
    work_image_key: Optional[str] = None,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """``guide_exercise`` 的异步版：await ``_ask_guide_model_async``。

    Args、Returns、Raises 与 ``guide_exercise`` 完全一致（DB 写操作仍是同步调用）。
    """
    session = _load_ai_session(db, student_id=student_id, session_id=session_id)
    question, answer = _load_ai_question(db, session=session, question_id=question_id)

    text = _clean(work)
    active_mode = detect_mode(text=text, state=session.session_state, override=mode)
    context = f"session={session.id} question={question.id}"
    logger.info("exercise-guide %s mode=%s", context, active_mode)

    outcome = await _ask_guide_model_async(
        stem=question.content or "",
        work=text,
        mode=active_mode,
        context=context,
        image_b64=work_image_b64,
    )

    reply = outcome["reply"]
    if active_mode in UNGUARDED_MODES:
        # 讲解/评判模式：孩子要的就是结果，泄题闸门在这里必须让路
        reply = reply or GUIDE_FALLBACK_REPLY
    else:
        reply = sanitize_guide_reply(
            reply,
            q_type=question.type_,
            options=question.options,
            correct_answer=answer.correct_answer if answer else None,
            context=context,
        )

    guide_record = {
        "question_id": str(question.id),
        "work": text,
        "teacher_reply": reply,
        "verdict": outcome["verdict"],
        "error_type": outcome["error_type"],
        "work_image_url": _file_url(work_image_key),
    }
    db.add(
        AiMessage(
            session_id=session.id,
            role="system",
            content=f"{EXERCISE_GUIDE_PREFIX}{json.dumps(guide_record, ensure_ascii=False)}",
        )
    )
    db.commit()

    return {
        "question_id": str(question.id),
        "reply": reply,
        "ask_final_confirm": bool(outcome["ask_final_confirm"]),
        "verdict": outcome["verdict"],
        "error_type": outcome["error_type"],
        "mode": active_mode,
        "prompt_version": practice_guide.VERSION,
    }


# ============================================================ T7 判错即时入本
#: 归一化比对时要抹掉的噪声字符。
#:
#: ⚠️ 刻意**不含** ``.`` ``/`` ``-`` ``%``——它们在数学答案里是有意义的
#: （小数点 / 分数线 / 负号 / 百分号），抹掉会把 ``12.0`` 变成 ``120``。
_NOISE_RE = re.compile(r"[\s,，。、；;：:！!？?（）()【】\[\]{}\"'“”‘’*_\\]+")

#: 「答案是 / 结果为 / 等于」这类前缀，比对前先剥掉
_ANSWER_LABEL_RE = re.compile(r"^(答案|答|结果|等于|得|是|为)\s*[是为:：=]?\s*")


def _normalize_answer(raw: Any) -> str:
    """答案文本归一化：全角转半角 + 小写 + 去噪声 + 去「答：」类前缀。"""
    text = unicodedata.normalize("NFKC", _clean(raw)).lower()
    if not text:
        return ""
    text = _ANSWER_LABEL_RE.sub("", text)
    text = _NOISE_RE.sub("", text)
    return text.rstrip(".")


def _sole_number(text: str) -> Optional[float]:
    """文本里**有且只有一个**数字时返回它，否则返回 ``None``。

    「有且只有一个」是关键：``12`` 与 ``12元`` 能对上，而 ``7`` 与 ``17``
    不会被误判成相等（后者抽出的是 17，与 7 不等）。

    TASK-002：实现提升为 ``app/ai/_num_utils.py::sole_number`` 共享
    （学生回答结构化 analyzer 同款语义，消双份；分层纪律允许 services 依赖 ai）。
    """
    return sole_number(text)


def _answers_match(student_answer: Any, correct_answer: Any) -> Optional[bool]:
    """文本 / 数值双口径比对学生答案与标准答案。

    比对顺序：归一化全等 → 整串数值相等 → 单一数字相等（容忍单位后缀）。

    Returns:
        ``True`` 一致、``False`` 不一致；标准答案或学生答案为空时返回 ``None``
        （表示「这条信号不可用」，交给模型 ``verdict`` 定夺）。
    """
    left = _normalize_answer(student_answer)
    right = _normalize_answer(correct_answer)
    if not left or not right:
        return None
    if left == right:
        return True

    # 数值题：``12`` / ``12.0`` / ``１２`` 视为同一个答案
    try:
        return abs(float(left) - float(right)) < 1e-6
    except (TypeError, ValueError):
        pass

    # 标准答案常写成「12元」「12 平方厘米」这类带单位形式：
    # 两边都只含一个数字时按数值比，避免被单位文案卡死
    left_num = _sole_number(left)
    right_num = _sole_number(right)
    if left_num is not None and right_num is not None:
        return abs(left_num - right_num) < 1e-6

    return False


def _sympy_arbitration(
    *,
    q_type: Optional[str],
    content: Optional[str],
    correct_answer: Optional[str],
    selected_answer: Optional[str],
) -> Optional[bool]:
    """判题前先过 SymPy 精确判（**仲裁层**，不是替代层）。

    定位与题库链路（``practice_service``）完全一致：纯本地符号计算，零 LLM
    调用、零幻觉。AI 生成题的标准答案由 LLM 自产、无第三方校验，模型自判又存在
    「幻觉判对」风险，所以这一层专门在最终判定前兜底。

    覆盖口径刻意收敛：

    * 题型必须在 ``sympy_judge.SYMPY_SUPPORTED_TYPES``（计算/化简/解方程等）内，
      应用题/图形题等主观题直接不可判（``judge`` 内置闸门），正则与黑名单
      **不放开**；
    * 标准答案与学生作答都必须能被 SymPy 解析，否则不可判。

    Args:
        q_type: 题目在库的题型（``Question.type_``，已按 DB 白名单映射）。
        content: 题干（供 SymPy 抽取表达式，可为空）。
        correct_answer: 标准答案文本。
        selected_answer: 学生最终作答文本。

    Returns:
        ``True`` / ``False``：SymPy 判定结论，调用方应**按它判**；
        ``None``：不可判（题型不符 / 任一侧答案不可解析 / 执行异常），
        调用方回落现有 ``_answers_match`` + 模型 verdict 逻辑，保留
        「冲突时以模型为准 + warning」的兜底行为。
    """
    if not correct_answer or not selected_answer:
        return None
    try:
        outcome = sympy_judge.judge(q_type, content, correct_answer, selected_answer)
    except Exception:  # noqa: BLE001 —— 判题主链路绝不能被校验层异常拖垮
        logger.warning("SymPy 仲裁层执行异常，回落现有判题逻辑", exc_info=True)
        return None
    if outcome is None:
        return None
    logger.info(
        "SymPy 仲裁层生效：type=%s correct=%s selected=%r standard=%r",
        q_type,
        outcome.is_correct,
        selected_answer,
        correct_answer,
    )
    return bool(outcome.is_correct)


def judge_and_record(
    db: Session,
    *,
    student_id: Any,
    session_id: Any,
    question_id: Any,
    answer: Optional[str] = None,
    work: Optional[str] = None,
    work_image_b64: Optional[str] = None,
    work_image_keys: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """学生提交最终答案：判对错 + **判错即时入本**（D3-b）。

    对错判定用**三信号**（仲裁层级联）：

    1. **SymPy 精确判**（仲裁层，``sympy_judge.judge``）：题型落在
       ``SYMPY_SUPPORTED_TYPES``（计算/化简/解方程等）且两侧答案均可解析时，
       按 SymPy 结果判——AI 题的标准答案由 LLM 自产、无第三方校验，这一层
       专防「模型幻觉判对」与文本比对误判；主观题 / 任一侧不可解析时 SymPy
       返回不可判，自动跳过，绝不抢判；
    2. ``answers.correct_answer`` 的归一化文本比对（信号①）；
    3. 模型 ``verdict``（信号②）。

    AI 生成题多为主观计算题，不走 ``judge_engine``（那是给题库选择/填空题
    设计的）；SymPy 不可判时沿用**裁决 A3：两者冲突以模型 ``verdict`` 为准**，
    因为它看得到孩子的整个解题过程，而文本比对只看最后一个数。

    判错后立刻 ``record_mistake()``——孩子此刻关掉页面也不丢数据，这正是
    「练完才结算」最容易漏掉的一段。

    Args:
        db: 数据库会话。**调用前不能有半截未提交的写**（``record_mistake``
            内部会 ``commit``）。
        student_id: 学生 ID。
        session_id: AI 辅导会话 ID。
        question_id: 练习题 ID。
        answer: 孩子提交的最终答案。
        work: 孩子的解题过程，可为空。

    Returns:
        ``{"question_id", "correct", "verdict_text", "error_type",
        "mistake_id", "mastery_refreshed"}``。

    Raises:
        ApiError: 400 题目不属于本会话；404 会话/题目不存在；500 方舟调用失败。
    """
    session = _load_ai_session(db, student_id=student_id, session_id=session_id)
    question, std_answer = _load_ai_question(db, session=session, question_id=question_id)

    sid = to_uuid(student_id, "student_id")
    submitted = _clean(answer)
    process = _clean(work)
    context = f"session={session.id} question={question.id}"

    # ---- 信号⓪：SymPy 精确判（仲裁层；不可判时返回 None 回落下方逻辑）--------
    sympy_correct = _sympy_arbitration(
        q_type=question.type_,
        content=question.content,
        correct_answer=std_answer.correct_answer if std_answer else None,
        selected_answer=submitted,
    )

    # ---- 信号①：文本 / 数值归一化比对 -------------------------------------
    text_match = _answers_match(submitted, std_answer.correct_answer if std_answer else None)

    # ---- 信号②：模型 verdict（JUDGE 模式，明确要它判对错）------------------
    combined_work = "\n".join(filter(None, [process, f"我的最终答案是：{submitted}" if submitted else ""]))
    outcome = _ask_guide_model(
        stem=question.content or "",
        work=combined_work,
        mode=MODE_JUDGE,
        context=context,
        image_b64=work_image_b64,
    )
    verdict = outcome["verdict"]

    # ---- 裁决：SymPy 仲裁优先；不可判时走原 A3（冲突时以模型为准）------------
    if sympy_correct is not None:
        # SymPy 能判定 → 按它的结果判（LLM 幻觉 / 文本比对误判的最终防线）
        correct = sympy_correct
        if verdict in ("correct", "wrong") and (verdict == "correct") != correct:
            logger.info(
                "AI 练习判定冲突（以 SymPy 为准）%s sympy=%s verdict=%s",
                context,
                sympy_correct,
                verdict,
            )
    elif verdict == "correct":
        correct = True
    elif verdict == "wrong":
        correct = False
    else:
        correct = bool(text_match) if text_match is not None else False

    if (
        sympy_correct is None
        and text_match is not None
        and verdict in ("correct", "wrong")
        and bool(text_match) != correct
    ):
        logger.info(
            "AI 练习判定冲突（以模型为准）%s text_match=%s verdict=%s",
            context,
            text_match,
            verdict,
        )

    verdict_text = outcome["reply"] or (DEFAULT_CORRECT_TEXT if correct else DEFAULT_WRONG_TEXT)

    judge_record = {
        "question_id": str(question.id),
        "work": process,
        "answer": submitted,
        "correct": correct,
        "error_type": None if correct else (outcome["error_type"] or _safe_error_code(verdict_text)),
    }
    db.add(
        AiMessage(
            session_id=session.id,
            role="system",
            content=f"{EXERCISE_JUDGE_PREFIX}{json.dumps(judge_record, ensure_ascii=False)}",
        )
    )
    db.commit()

    if correct:
        return {
            "question_id": str(question.id),
            "correct": True,
            "verdict_text": verdict_text,
            "error_type": None,
            "mistake_id": None,
            "mastery_refreshed": False,
        }

    # ---- 判错即时入本（幂等，record_mistake 内部 commit）--------------------
    error_type = str(judge_record["error_type"])
    detail_bits: List[str] = []
    if submitted:
        detail_bits.append(f"作答「{submitted}」")
    std_text = _clean(std_answer.correct_answer) if std_answer else ""
    if std_text:
        detail_bits.append(f"标准答案「{std_text}」")
    error_reason = MISTAKE_REASON_PREFIX
    if detail_bits:
        error_reason = f"{MISTAKE_REASON_PREFIX}：{'；'.join(detail_bits)}"

    mistake = learning_service.record_mistake(
        db,
        student_id=sid,
        question_id=question.id,
        error_type=error_type,
        error_reason=error_reason,
        work_image_urls=[
            f"/api/file/{key}"
            for key in (work_image_keys or [])
            if key and "/" not in key and "\\" not in key and key not in {".", ".."}
        ],
    )

    # ---- 掌握度缓存失效：只对可归因（kp_id 非空）的题做，失败不拖垮主流程 -----
    mastery_refreshed = False
    if question.kp_id is not None:
        try:
            profile_service.invalidate_cache_for_question(
                db,
                student_id=sid,
                question_id=question.id,
            )
            db.commit()
            mastery_refreshed = True
        except Exception:  # noqa: BLE001 - 对齐 practice_service:819
            logger.warning("AI 练习判错刷新画像缓存失败 %s", context, exc_info=True)
            db.rollback()

    logger.info(
        "AI 练习判错入本 %s error_type=%s mistake=%s", context, error_type, mistake.id
    )

    return {
        "question_id": str(question.id),
        "correct": False,
        "verdict_text": verdict_text,
        "error_type": error_type,
        "mistake_id": str(mistake.id),
        "mastery_refreshed": mastery_refreshed,
    }


async def judge_and_record_async(
    db: Session,
    *,
    student_id: Any,
    session_id: Any,
    question_id: Any,
    answer: Optional[str] = None,
    work: Optional[str] = None,
    work_image_b64: Optional[str] = None,
    work_image_keys: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """``judge_and_record`` 的异步版：await ``_ask_guide_model_async``。

    Args、Returns、Raises 与 ``judge_and_record`` 完全一致（DB 写操作仍是同步调用）。
    """
    session = _load_ai_session(db, student_id=student_id, session_id=session_id)
    question, std_answer = _load_ai_question(db, session=session, question_id=question_id)

    sid = to_uuid(student_id, "student_id")
    submitted = _clean(answer)
    process = _clean(work)
    context = f"session={session.id} question={question.id}"

    # ---- 信号⓪：SymPy 精确判（仲裁层；不可判时返回 None 回落下方逻辑）--------
    sympy_correct = _sympy_arbitration(
        q_type=question.type_,
        content=question.content,
        correct_answer=std_answer.correct_answer if std_answer else None,
        selected_answer=submitted,
    )

    # ---- 信号①：文本 / 数值归一化比对 -------------------------------------
    text_match = _answers_match(submitted, std_answer.correct_answer if std_answer else None)

    # ---- 信号②：模型 verdict（JUDGE 模式，明确要它判对错）------------------
    combined_work = "\n".join(filter(None, [process, f"我的最终答案是：{submitted}" if submitted else ""]))
    outcome = await _ask_guide_model_async(
        stem=question.content or "",
        work=combined_work,
        mode=MODE_JUDGE,
        context=context,
        image_b64=work_image_b64,
    )
    verdict = outcome["verdict"]

    # ---- 裁决：SymPy 仲裁优先；不可判时走原 A3（冲突时以模型为准）------------
    if sympy_correct is not None:
        # SymPy 能判定 → 按它的结果判（LLM 幻觉 / 文本比对误判的最终防线）
        correct = sympy_correct
        if verdict in ("correct", "wrong") and (verdict == "correct") != correct:
            logger.info(
                "AI 练习判定冲突（以 SymPy 为准）%s sympy=%s verdict=%s",
                context,
                sympy_correct,
                verdict,
            )
    elif verdict == "correct":
        correct = True
    elif verdict == "wrong":
        correct = False
    else:
        correct = bool(text_match) if text_match is not None else False

    if (
        sympy_correct is None
        and text_match is not None
        and verdict in ("correct", "wrong")
        and bool(text_match) != correct
    ):
        logger.info(
            "AI 练习判定冲突（以模型为准）%s text_match=%s verdict=%s",
            context,
            text_match,
            verdict,
        )

    verdict_text = outcome["reply"] or (DEFAULT_CORRECT_TEXT if correct else DEFAULT_WRONG_TEXT)

    judge_record = {
        "question_id": str(question.id),
        "work": process,
        "answer": submitted,
        "correct": correct,
        "error_type": None if correct else (outcome["error_type"] or _safe_error_code(verdict_text)),
    }
    db.add(
        AiMessage(
            session_id=session.id,
            role="system",
            content=f"{EXERCISE_JUDGE_PREFIX}{json.dumps(judge_record, ensure_ascii=False)}",
        )
    )
    db.commit()

    if correct:
        return {
            "question_id": str(question.id),
            "correct": True,
            "verdict_text": verdict_text,
            "error_type": None,
            "mistake_id": None,
            "mastery_refreshed": False,
        }

    # ---- 判错即时入本（幂等，record_mistake 内部 commit）--------------------
    error_type = str(judge_record["error_type"])
    detail_bits: List[str] = []
    if submitted:
        detail_bits.append(f"作答「{submitted}」")
    std_text = _clean(std_answer.correct_answer) if std_answer else ""
    if std_text:
        detail_bits.append(f"标准答案「{std_text}」")
    error_reason = MISTAKE_REASON_PREFIX
    if detail_bits:
        error_reason = f"{MISTAKE_REASON_PREFIX}：{'；'.join(detail_bits)}"

    mistake = learning_service.record_mistake(
        db,
        student_id=sid,
        question_id=question.id,
        error_type=error_type,
        error_reason=error_reason,
        work_image_urls=[
            f"/api/file/{key}"
            for key in (work_image_keys or [])
            if key and "/" not in key and "\\" not in key and key not in {".", ".."}
        ],
    )

    # ---- 掌握度缓存失效：只对可归因（kp_id 非空）的题做，失败不拖垮主流程 -----
    mastery_refreshed = False
    if question.kp_id is not None:
        try:
            profile_service.invalidate_cache_for_question(
                db,
                student_id=sid,
                question_id=question.id,
            )
            db.commit()
            mastery_refreshed = True
        except Exception:  # noqa: BLE001 - 对齐 practice_service:819
            logger.warning("AI 练习判错刷新画像缓存失败 %s", context, exc_info=True)
            db.rollback()

    logger.info(
        "AI 练习判错入本 %s error_type=%s mistake=%s", context, error_type, mistake.id
    )

    return {
        "question_id": str(question.id),
        "correct": False,
        "verdict_text": verdict_text,
        "error_type": error_type,
        "mistake_id": str(mistake.id),
        "mastery_refreshed": mastery_refreshed,
    }


def generate_summary(
    db: Session,
    *,
    student_id: Any,
    session_id: Any,
) -> Dict[str, Any]:
    """两道相似题都完成最终判定后，生成并缓存本次结构化学习小结。"""
    session = _load_ai_session(db, student_id=student_id, session_id=session_id)
    if session.summary_text:
        return {"summary": session.summary_text, "cached": True, "prompt_version": summary_prompt.VERSION}

    messages = list(
        db.execute(
            select(AiMessage)
            .where(AiMessage.session_id == session.id)
            .order_by(AiMessage.created_at.asc())
        ).scalars()
    )
    exercise_ids = _latest_exercise_question_ids(messages)
    judged: Dict[str, Dict[str, Any]] = {}
    guided: Dict[str, List[Dict[str, Any]]] = {}
    for message in messages:
        if message.content.startswith(EXERCISE_GUIDE_PREFIX):
            try:
                item = json.loads(message.content[len(EXERCISE_GUIDE_PREFIX):])
            except (TypeError, ValueError):
                continue
            qid = str(item.get("question_id") or "")
            if qid:
                guided.setdefault(qid, []).append(item)
            continue
        if not message.content.startswith(EXERCISE_JUDGE_PREFIX):
            continue
        try:
            item = json.loads(message.content[len(EXERCISE_JUDGE_PREFIX):])
        except (TypeError, ValueError):
            continue
        qid = str(item.get("question_id") or "")
        if qid:
            judged[qid] = item

    if len(exercise_ids) < 2 or not all(str(qid) in judged for qid in exercise_ids):
        raise ApiError(CODE_BAD_REQUEST, "先完成两道相似题，老师再帮你做小结")

    current_ids = {str(qid) for qid in exercise_ids}
    transcript = "\n".join(
        f"{message.role}：{message.content}"
        for message in messages
        if not message.content.startswith(
            (EXERCISE_PREFIX, EXERCISE_GUIDE_PREFIX, EXERCISE_JUDGE_PREFIX)
        )
    )
    exercise_notes = "\n".join(
        f"练习题 {qid}：逐步引导 "
        f"{json.dumps(guided.get(qid, []), ensure_ascii=False)}；"
        f"最终作答过程 {item.get('work') or '未写文字过程'}；"
        f"最终答案 {item.get('answer') or '未填写'}；"
        f"结果 {'正确' if item.get('correct') else '需要继续练习'}；"
        f"错因 {item.get('error_type') or '无'}"
        for qid, item in judged.items()
        if qid in current_ids
    )
    prompt = (
        "以下内容只供你内部判断，不要复述原始对话。\n"
        f"原题辅导记录：\n{transcript}\n\n两道相似题记录：\n{exercise_notes}"
    )
    try:
        summary_text = ark_client.ark_respond(
            messages=[{"role": "user", "content": prompt}],
            system=summary_prompt.PROMPT,
        ).strip()
    except ark_client.ArkError as exc:
        logger.warning("AI 学习小结生成失败 session=%s: %s", session.id, exc)
        raise ApiError(CODE_SERVER_ERROR, "老师的小结暂时没写好，稍后再试一次") from exc

    session.summary_text = summary_text
    session.session_state = "summarizing"
    session.state_updated_at = datetime.now(timezone.utc)
    db.add(AiMessage(session_id=session.id, role="assistant", content=summary_text))

    # ---- DNA-004 行为事件埋点：学习小结生成 = AI 练习会话收尾（SESSIONEND）----
    # 挂点理由：generate_summary 是 AI 练习链路的**唯一收尾路径**（两道相似题
    # 都判定后才允许生成小结，且 summary_text 有缓存——重复调用早返回，
    # 天然幂等，不会重复发事件）。归参取会话绑定题目；缺失标 unknown。
    end_unknown: Dict[str, Any] = {}
    if session.question_id is None:
        end_unknown["question_id_unknown"] = True
    elif session.question is None or session.question.kp_id is None:
        end_unknown["kp_id_unknown"] = True
    _emit_dna_event(
        db,
        student_id=session.student_id,
        event_type="SESSIONEND",
        question_id=session.question_id,
        knowledge_point_id=session.question.kp_id if session.question else None,
        meta={
            "channel": "ai_exercise",
            "session_id": str(session.id),
            "exercise_count": len(exercise_ids),
            **end_unknown,
        },
    )
    db.commit()
    return {"summary": summary_text, "cached": False, "prompt_version": summary_prompt.VERSION}


async def generate_summary_async(
    db: Session,
    *,
    student_id: Any,
    session_id: Any,
) -> Dict[str, Any]:
    """``generate_summary`` 的异步版：await ``ark_client.ark_respond_async``。

    Args、Returns、Raises 与 ``generate_summary`` 完全一致（DB 写操作仍是同步调用）。
    """
    session = _load_ai_session(db, student_id=student_id, session_id=session_id)
    if session.summary_text:
        return {"summary": session.summary_text, "cached": True, "prompt_version": summary_prompt.VERSION}

    messages = list(
        db.execute(
            select(AiMessage)
            .where(AiMessage.session_id == session.id)
            .order_by(AiMessage.created_at.asc())
        ).scalars()
    )
    exercise_ids = _latest_exercise_question_ids(messages)
    judged: Dict[str, Dict[str, Any]] = {}
    guided: Dict[str, List[Dict[str, Any]]] = {}
    for message in messages:
        if message.content.startswith(EXERCISE_GUIDE_PREFIX):
            try:
                item = json.loads(message.content[len(EXERCISE_GUIDE_PREFIX):])
            except (TypeError, ValueError):
                continue
            qid = str(item.get("question_id") or "")
            if qid:
                guided.setdefault(qid, []).append(item)
            continue
        if not message.content.startswith(EXERCISE_JUDGE_PREFIX):
            continue
        try:
            item = json.loads(message.content[len(EXERCISE_JUDGE_PREFIX):])
        except (TypeError, ValueError):
            continue
        qid = str(item.get("question_id") or "")
        if qid:
            judged[qid] = item

    if len(exercise_ids) < 2 or not all(str(qid) in judged for qid in exercise_ids):
        raise ApiError(CODE_BAD_REQUEST, "先完成两道相似题，老师再帮你做小结")

    current_ids = {str(qid) for qid in exercise_ids}
    transcript = "\n".join(
        f"{message.role}：{message.content}"
        for message in messages
        if not message.content.startswith(
            (EXERCISE_PREFIX, EXERCISE_GUIDE_PREFIX, EXERCISE_JUDGE_PREFIX)
        )
    )
    exercise_notes = "\n".join(
        f"练习题 {qid}：逐步引导 "
        f"{json.dumps(guided.get(qid, []), ensure_ascii=False)}；"
        f"最终作答过程 {item.get('work') or '未写文字过程'}；"
        f"最终答案 {item.get('answer') or '未填写'}；"
        f"结果 {'正确' if item.get('correct') else '需要继续练习'}；"
        f"错因 {item.get('error_type') or '无'}"
        for qid, item in judged.items()
        if qid in current_ids
    )
    prompt = (
        "以下内容只供你内部判断，不要复述原始对话。\n"
        f"原题辅导记录：\n{transcript}\n\n两道相似题记录：\n{exercise_notes}"
    )
    try:
        summary_text = (
            await ark_client.ark_respond_async(
                messages=[{"role": "user", "content": prompt}],
                system=summary_prompt.PROMPT,
            )
        ).strip()
    except ark_client.ArkError as exc:
        logger.warning("AI 学习小结生成失败 session=%s: %s", session.id, exc)
        raise ApiError(CODE_SERVER_ERROR, "老师的小结暂时没写好，稍后再试一次") from exc

    session.summary_text = summary_text
    session.session_state = "summarizing"
    session.state_updated_at = datetime.now(timezone.utc)
    db.add(AiMessage(session_id=session.id, role="assistant", content=summary_text))

    # ---- DNA-004 行为事件埋点：学习小结生成 = AI 练习会话收尾（SESSIONEND）----
    # 挂点理由：generate_summary 是 AI 练习链路的**唯一收尾路径**（两道相似题
    # 都判定后才允许生成小结，且 summary_text 有缓存——重复调用早返回，
    # 天然幂等，不会重复发事件）。归参取会话绑定题目；缺失标 unknown。
    end_unknown: Dict[str, Any] = {}
    if session.question_id is None:
        end_unknown["question_id_unknown"] = True
    elif session.question is None or session.question.kp_id is None:
        end_unknown["kp_id_unknown"] = True
    _emit_dna_event(
        db,
        student_id=session.student_id,
        event_type="SESSIONEND",
        question_id=session.question_id,
        knowledge_point_id=session.question.kp_id if session.question else None,
        meta={
            "channel": "ai_exercise",
            "session_id": str(session.id),
            "exercise_count": len(exercise_ids),
            **end_unknown,
        },
    )
    db.commit()
    return {"summary": summary_text, "cached": False, "prompt_version": summary_prompt.VERSION}


# ============================================================ T1 薄弱画像
def get_weak_profile(
    db: Session,
    *,
    student_id: Any,
    top_n: int = 2,
) -> Dict[str, Any]:
    """按 student_id 聚合错题库，取 Top-N 主导弱项错因。

    纯本地 SQL 聚合（零 LLM 调用），只认 ``error_categories`` 五类机器码，
    天然过滤掉 ``NULL`` 与 TD-3 的脏值 ``"练习错题"``。``label`` 一律走
    ``error_label(code)``，不硬编码中文。

    Args:
        db: 调用方传入的 SQLAlchemy 会话（**本函数只读，不 commit**）。
        student_id: 学生 ID（``uuid.UUID`` 或可转换字符串）。
        top_n: 取计数最高的前 N 个弱项（默认 2）。

    Returns:
        ``{"weak_tags": [{"code","label"}], "source": "profile"|"fallback"}``：
        - ``source="profile"``：聚合到合规错题，按计数降序回带 Top-N 标签；
        - ``source="fallback"``：新学生 / 无合规错题，``weak_tags`` 为空。
    """
    sid = to_uuid(student_id, "student_id")
    rows = db.execute(
        select(Mistake.error_type, func.count().label("cnt"))
        .where(
            Mistake.student_id == sid,
            Mistake.error_type.in_(ERROR_CATEGORIES),  # 五类机器码过滤；NULL/"练习错题" 自动排除
        )
        .group_by(Mistake.error_type)
        .order_by(func.count().desc(), Mistake.error_type)  # 计数降序取主导；同计数稳定 tie-break
    ).all()

    top_codes = [row.error_type for row in rows[:top_n]]
    if not top_codes:
        # 兜底：新学生 / 无合规错题
        return {"weak_tags": [], "source": "fallback"}

    weak_tags = [{"code": code, "label": error_label(code)} for code in top_codes]
    return {"weak_tags": weak_tags, "source": "profile"}


__all__ = [
    "AI_SOURCE_DOC",
    "MISTAKE_REASON_PREFIX",
    "ANSWER_CHECK_KEY",
    "ANSWER_CHECK_OK",
    "ANSWER_CHECK_EMPTY",
    "ANSWER_CHECK_UNPARSEABLE",
    "ANSWER_CHECK_SKIPPED",
    "persist_exercises",
    "guide_exercise",
    "guide_exercise_async",
    "judge_and_record",
    "judge_and_record_async",
    "generate_summary",
    "generate_summary_async",
    "get_weak_profile",
]
