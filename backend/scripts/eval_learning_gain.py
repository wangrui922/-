# -*- coding: utf-8 -*-
"""Learning Gain 离线评测（Benchmark 维度二：学习效果）。

对「错题教练」的学习效果做离线复盘：输入学生 / 知识点范围，从
``attempts`` / ``review_cards`` / ``review_logs`` / ``student_learning_events``
/ ``ai_messages``（**全部只读**）聚合下面五个指标，输出 JSON 报告。

指标口径定义（每个指标一句话定义 + 计算式，报告与看板必须与此一致）：

1. **首次正确率**（first_attempt_accuracy）
   定义：每个 (学生, 题目) 组合的**第一次**作答中答对的比例——学生的前置
   水平（前测）。
   计算式：首答正确次数 ÷ 首答总次数（首答 = 同 (学生, 题目) 下
   ``created_at`` 最早的 attempt）。

2. **复测正确率**（retest_accuracy）
   定义：每个 (学生, 题目) 组合**首次之后**的作答中答对的比例——练习/纠错
   后的水平（后测）。
   计算式：非首答正确次数 ÷ 非首答总次数。

3. **Learning Gain**（learning_gain，Hake 标准化增益）
   定义：同一批题上「后测相对前测」的标准化提升，用于跨学生/跨知识点比较。
   计算式：``g = (post - pre) / (1 - pre)``；``pre = 首次正确率``、
   ``post = 复测正确率``。前测已满分（pre=1）时 g 数学上未定义，记
   ``None`` 并给 note；任一侧样本为 0 同样记 ``None``——**不编造数字**。

4. **纠错后正确率**（corrected_accuracy）
   定义：同一知识点下「先答错、随后答对」的 (学生, 知识点) 组合，占至少答
   错过一次的组合的比例——错题有没有被真正纠正。
   计算式：先错后对的组合数 ÷ 至少错答过一次的组合数（按
   ``(student_id, kp_id)`` 分组，attempt 按时间排序判定「随后」）。

5. **迁移题正确率**（transfer_accuracy）
   定义：被认定为「迁移题」的作答中答对的比例——同知识点换情境后是否仍会。
   迁移题认定口径（满足其一）：① AI 练习 ``role='transfer'`` 的题
   （``student_learning_events`` 的 QUESTIONVIEW meta 或 ``ai_messages``
   的「练习题：」消息回捞）；② 挂在复习卡片上的题（``review_cards``
   记录，视为保持性测验）。
   计算式：迁移题答对次数 ÷ 迁移题作答总次数。

6. **7 天复习正确率**（review_7d_accuracy）
   定义：距上次复习已过去至少 ``--review-gap-days`` 天（默认 7）的复习
   记录中「记得」的比例——间隔 7 天后的保持率。
   计算式：``review_logs`` 中 ``elapsed_days >= 7`` 且 ``rating >= 3``
   （FSRS：1=忘了 2=模糊 3=记得 4=太简单）的记录数 ÷ 同期记录数。
   ``review_logs`` 表不存在（未执行 013 迁移）时记 ``None`` + note。

数据不足时**明确报告样本量**（correct/total 原样给出，rate 为 None），
绝不输出假数字。报告含 ``notes`` 列表说明每个数据缺口。

用法::

    # 全量 + 默认 30 天窗（报告落 backend/.qa_cache/）
    python backend/scripts/eval_learning_gain.py --all

    # 只看某知识点（名称或 ID 均可，可重复）
    python backend/scripts/eval_learning_gain.py --kp "一元一次方程·移项" --days 30

    # 指定学生 + 输出路径
    python backend/scripts/eval_learning_gain.py --student <student-uuid> --out /tmp/lg.json

    # 收紧/放宽 7 天复习认定间隔
    python backend/scripts/eval_learning_gain.py --all --review-gap-days 7

连接：走 ``app.db``（尊重 ``DATABASE_URL``，开发库默认
``sqlite:///./dev.db``）；本脚本**只读**，不写任何业务表。
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select, text  # noqa: E402

from app.db import SessionLocal, is_sqlite  # noqa: E402
from app.models.ai import AiMessage  # noqa: E402
from app.models.content import KnowledgePoint, Question  # noqa: E402
from app.models.dna import StudentLearningEvent  # noqa: E402
from app.models.learning import Attempt  # noqa: E402

#: 报告默认输出目录（与任务书约定一致）
DEFAULT_REPORT_DIR = BACKEND_DIR / ".qa_cache"

#: 默认时间窗（天）
DEFAULT_WINDOW_DAYS: int = 30

#: FSRS 评分「记得」下限（1=Again 2=Hard 3=Good 4=Easy）
REVIEW_RATING_PASS: int = 3

#: 「练习题：」消息前缀（与 orchestrator.EXERCISE_PREFIX 一致，勿漂移）
EXERCISE_PREFIX: str = "练习题："

#: 复习事件 QUESTIONVIEW 的 meta 里标记迁移题的角色值
TRANSFER_ROLE: str = "transfer"


# ================================================================== 工具
def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    """把输入规整为 ``uuid.UUID``；非法返回 ``None``。"""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _parse_iso(raw: Optional[str]) -> Optional[datetime]:
    """解析 ISO8601 时间；失败抛 ``ValueError``（由 main 转成退出码 2）。"""
    if not raw:
        return None
    dt = datetime.fromisoformat(str(raw).strip())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _rate(correct: int, total: int) -> Optional[float]:
    """比率；分母 0 返回 ``None``（不编造 0%）。"""
    if total <= 0:
        return None
    return correct / total


def _metric(correct: int, total: int) -> Dict[str, Any]:
    """标准指标块：样本量永远给出，rate 允许为 None。"""
    return {
        "correct": int(correct),
        "total": int(total),
        "rate": _rate(correct, total),
    }


# ================================================================== 取数（只读）
def _load_attempts(
    db: Any,
    *,
    kp_ids: Optional[Sequence[uuid.UUID]] = None,
    student_ids: Optional[Sequence[uuid.UUID]] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> List[Attempt]:
    """按范围取 attempt 流水（按 (created_at, id) 升序，保证「首次/随后」可判）。"""
    stmt = select(Attempt)
    if since is not None:
        stmt = stmt.where(Attempt.created_at >= since)
    if until is not None:
        stmt = stmt.where(Attempt.created_at < until)
    if student_ids:
        stmt = stmt.where(Attempt.student_id.in_(list(student_ids)))
    if kp_ids:
        stmt = stmt.join(Question, Attempt.question_id == Question.id).where(
            Question.kp_id.in_(list(kp_ids))
        )
    rows = list(db.execute(stmt).scalars().all())
    rows.sort(key=lambda a: (a.created_at, str(a.id)))
    return rows


def _load_question_kp(db: Any, question_ids: Set[uuid.UUID]) -> Dict[uuid.UUID, Optional[uuid.UUID]]:
    """题目 → 知识点 映射（取不到的题不出现在字典里，按未知处理）。"""
    if not question_ids:
        return {}
    rows = db.execute(
        select(Question.id, Question.kp_id).where(Question.id.in_(list(question_ids)))
    ).all()
    return {row[0]: row[1] for row in rows}


def _table_exists(db: Any, name: str) -> bool:
    """表存在判断（与调用方**同一会话/事务**内查询）。

    ⚠️ 为什么不用 ``sqlalchemy.inspect(engine).has_table``：Inspector 会从
    连接池另取连接，连接归还时的 reset 在 StaticPool（测试内存库的单连接
    共享）下会把**调用方未提交的事务回滚**——实测插入后调一次 has_table，
    同会话随后就查不到刚 flush 的行。本函数改用同一连接上的轻量查询，
    SQLite 走 ``sqlite_master``、PG 走 ``to_regclass``（方言切换只经
    ``app.db.is_sqlite`` 这一个 sanctioned 入口）。
    """
    try:
        if is_sqlite():
            row = db.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
                {"n": name},
            ).first()
            return row is not None
        row = db.execute(text("SELECT to_regclass(:n) AS t"), {"n": name}).first()
        return bool(row is not None and row[0] is not None)
    except Exception:  # noqa: BLE001 - 探测失败按不存在处理，由 note 说明
        return False


def _load_transfer_question_ids(db: Any, notes: List[str]) -> Set[uuid.UUID]:
    """迁移题认定（口径见模块 docstring）：role=transfer 的 AI 练习题 ∪ 复习卡题目。"""
    ids: Set[uuid.UUID] = set()

    # ① QUESTIONVIEW 事件的 meta.role == 'transfer'（AI 练习展示埋点）
    try:
        events = db.execute(
            select(StudentLearningEvent).where(
                StudentLearningEvent.event_type == "QUESTIONVIEW"
            )
        ).scalars().all()
        for event in events:
            meta = event.meta if isinstance(event.meta, dict) else {}
            if meta.get("role") == TRANSFER_ROLE and event.question_id is not None:
                ids.add(event.question_id)
    except Exception as exc:  # noqa: BLE001 - 单源失败不拖垮整体
        notes.append(f"QUESTIONVIEW 事件读取失败，迁移题认定少一路来源：{exc}")

    # ② ai_messages 的「练习题：[{question_id, role, ...}]」消息回捞
    try:
        messages = db.execute(
            select(AiMessage).where(
                AiMessage.role == "assistant",
                AiMessage.content.like(f"{EXERCISE_PREFIX}%"),
            )
        ).scalars().all()
        for message in messages:
            raw = message.content[len(EXERCISE_PREFIX):]
            try:
                items = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict) or item.get("role") != TRANSFER_ROLE:
                    continue
                qid = _as_uuid(item.get("question_id"))
                if qid is not None:
                    ids.add(qid)
    except Exception as exc:  # noqa: BLE001
        notes.append(f"ai_messages 练习题消息读取失败，迁移题认定少一路来源：{exc}")

    # ③ review_cards 上挂的题（review 记录，视为保持性测验）
    if _table_exists(db, "review_cards"):
        try:
            rows = db.execute(
                text("SELECT question_id FROM review_cards WHERE question_id IS NOT NULL")
            ).all()
            for row in rows:
                qid = _as_uuid(row[0])
                if qid is not None:
                    ids.add(qid)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"review_cards 读取失败，迁移题认定少一路来源：{exc}")
    else:
        notes.append("review_cards 表不存在（未执行 013 迁移）：迁移题认定缺少 review 一路来源")

    return ids


def _load_review_logs(
    db: Any,
    *,
    student_ids: Optional[Sequence[uuid.UUID]] = None,
    kp_ids: Optional[Sequence[uuid.UUID]] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    min_gap_days: int = 7,
    notes: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """读 review_logs（raw SQL 表，只读）；表缺失/读失败返回空列表并记 note。

    Returns:
       元素形如 ``{"student_id": uuid, "kp_id": uuid|None, "rating": int,
       "elapsed_days": int, "review_at": datetime|None}``。
    """
    notes = notes if notes is not None else []
    if not _table_exists(db, "review_logs"):
        notes.append("review_logs 表不存在（未执行 013 迁移）：7 天复习正确率无数据")
        return []

    has_cards = _table_exists(db, "review_cards")
    join_sql = "LEFT JOIN review_cards rc ON rc.id = rl.card_id" if has_cards else ""
    kp_select = "rc.kp_id AS kp_id" if has_cards else "NULL AS kp_id"
    sql = text(
        f"SELECT rl.student_id AS student_id, rl.rating AS rating, "
        f"rl.elapsed_days AS elapsed_days, rl.review_at AS review_at, {kp_select} "
        f"FROM review_logs rl {join_sql}"
    )
    try:
        rows = db.execute(sql).all()
    except Exception as exc:  # noqa: BLE001
        notes.append(f"review_logs 读取失败：{exc}")
        return []

    sid_set = {str(s) for s in (student_ids or [])}
    kp_set = {str(k) for k in (kp_ids or [])}
    result: List[Dict[str, Any]] = []
    for row in rows:
        student_id = _as_uuid(row[0])
        if sid_set and (student_id is None or str(student_id) not in sid_set):
            continue
        kp_id = _as_uuid(row[4])
        if kp_set and (kp_id is None or str(kp_id) not in kp_set):
            continue
        review_at: Optional[datetime] = None
        if row[3]:
            try:
                review_at = datetime.fromisoformat(str(row[3]))
                if review_at.tzinfo is None:
                    review_at = review_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                review_at = None
        if since is not None and (review_at is None or review_at < since):
            continue
        if until is not None and (review_at is not None and review_at >= until):
            continue
        result.append(
            {
                "student_id": student_id,
                "kp_id": kp_id,
                "rating": int(row[1] or 0),
                "elapsed_days": int(row[2] or 0),
                "review_at": review_at,
            }
        )
    return result


# ================================================================== 指标计算
def _first_and_retest(
    attempts: List[Attempt],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """首答/复测两组统计（输入必须已按时间升序）。"""
    first: Dict[Tuple[uuid.UUID, uuid.UUID], Attempt] = {}
    seen: Set[Tuple[uuid.UUID, uuid.UUID]] = set()
    retest_total = 0
    retest_correct = 0
    for attempt in attempts:
        if attempt.question_id is None:
            continue  # 无题目归属（自由对话）无法判首/复测，剔除不编造
        key = (attempt.student_id, attempt.question_id)
        if key not in seen:
            seen.add(key)
            first[key] = attempt
        else:
            retest_total += 1
            if attempt.is_correct:
                retest_correct += 1
    first_total = len(first)
    first_correct = sum(1 for a in first.values() if a.is_correct)
    return _metric(first_correct, first_total), _metric(retest_correct, retest_total)


def _corrected(
    attempts: List[Attempt], question_kp: Dict[uuid.UUID, Optional[uuid.UUID]]
) -> Dict[str, Any]:
    """纠错后正确率：同 (学生, 知识点) 先错后对 的占比（输入已按时间升序）。"""
    grouped: Dict[Tuple[uuid.UUID, uuid.UUID], List[Attempt]] = {}
    for attempt in attempts:
        kp_id = question_kp.get(attempt.question_id) if attempt.question_id else None
        if kp_id is None:
            continue  # 无知识点归属的 attempt 不进 kp 级指标
        grouped.setdefault((attempt.student_id, kp_id), []).append(attempt)

    kp_with_wrong = 0
    corrected = 0
    for items in grouped.values():
        wrong_seen = False
        has_later_correct = False
        for attempt in items:  # items 已按 (created_at, id) 升序
            if not attempt.is_correct:
                wrong_seen = True
            elif wrong_seen:
                has_later_correct = True
        if wrong_seen:
            kp_with_wrong += 1
            if has_later_correct:
                corrected += 1
    return _metric(corrected, kp_with_wrong)


def compute_learning_gain(
    db: Any,
    *,
    kp_ids: Optional[Sequence[uuid.UUID]] = None,
    student_ids: Optional[Sequence[uuid.UUID]] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    review_gap_days: int = 7,
) -> Dict[str, Any]:
    """Learning Gain 离线聚合（只读；口径定义见模块 docstring）。

    Args:
        db: SQLAlchemy 会话（只读使用，不 commit）。
        kp_ids: 知识点范围；``None``/空 = 不限。
        student_ids: 学生范围；``None``/空 = 不限。
        since: 窗内起点（含）；``None`` = 不限起点。
        until: 窗内终点（不含）；``None`` = 不限终点。
        review_gap_days: 7 天复习认定的最小间隔天数（默认 7）。

    Returns:
        报告 dict（``metrics`` / ``samples`` / ``notes``），由调用方补
        ``generated_at`` 与 ``scope`` 后序列化。
    """
    notes: List[str] = []
    kp_list = [k for k in (kp_ids or []) if k is not None]
    sid_list = [s for s in (student_ids or []) if s is not None]

    attempts = _load_attempts(
        db, kp_ids=kp_list, student_ids=sid_list, since=since, until=until
    )
    attributed = [a for a in attempts if a.question_id is not None]
    if not attributed:
        notes.append("时间窗内没有带题目归属的 attempt：attempts 类指标样本为 0")

    question_kp = _load_question_kp(db, {a.question_id for a in attributed})

    # ---- 1/2/3. 首答 / 复测 / Learning Gain --------------------------------
    first_metric, retest_metric = _first_and_retest(attempts)
    pre = first_metric["rate"]
    post = retest_metric["rate"]
    gain: Optional[float] = None
    gain_note: Optional[str] = None
    if pre is None or post is None:
        gain_note = "前测或后测样本为 0，标准化增益无法计算"
    elif pre >= 1.0:
        gain_note = "前测已满分（pre=1），标准化增益数学上未定义"
    else:
        gain = (post - pre) / (1.0 - pre)
    learning_gain: Dict[str, Any] = {
        "pre": pre,
        "post": post,
        "gain": round(gain, 4) if gain is not None else None,
        "note": gain_note,
    }

    # ---- 4. 纠错后正确率 ----------------------------------------------------
    corrected_metric = _corrected(attempts, question_kp)
    if corrected_metric["total"] == 0:
        notes.append("没有「至少答错过一次」的 (学生, 知识点) 组合：纠错后正确率样本为 0")

    # ---- 5. 迁移题正确率 ----------------------------------------------------
    transfer_ids = _load_transfer_question_ids(db, notes)
    transfer_attempts = [a for a in attributed if a.question_id in transfer_ids]
    transfer_total = len(transfer_attempts)
    transfer_correct = sum(1 for a in transfer_attempts if a.is_correct)
    transfer_metric = _metric(transfer_correct, transfer_total)
    if not transfer_ids:
        notes.append("未识别到任何迁移题（role=transfer / review 记录）：迁移题正确率样本为 0")
    elif transfer_total == 0:
        notes.append(
            f"识别到 {len(transfer_ids)} 道迁移题，但时间窗内没有对应作答："
            "迁移题正确率样本为 0"
        )

    # ---- 6. 7 天复习正确率 --------------------------------------------------
    review_rows = _load_review_logs(
        db,
        student_ids=sid_list,
        kp_ids=kp_list,
        since=since,
        until=until,
        min_gap_days=review_gap_days,
        notes=notes,
    )
    gap_rows = [r for r in review_rows if r["elapsed_days"] >= max(0, int(review_gap_days))]
    review_total = len(gap_rows)
    review_correct = sum(1 for r in gap_rows if r["rating"] >= REVIEW_RATING_PASS)
    review_metric = _metric(review_correct, review_total)
    if _table_exists(db, "review_logs") and review_total == 0:
        notes.append(
            f"review_logs 中没有 elapsed_days >= {review_gap_days} 的记录："
            "7 天复习正确率样本为 0"
        )

    return {
        "metrics": {
            "first_attempt_accuracy": first_metric,
            "retest_accuracy": retest_metric,
            "learning_gain": learning_gain,
            "corrected_accuracy": corrected_metric,
            "transfer_accuracy": transfer_metric,
            "review_7d_accuracy": review_metric,
        },
        "samples": {
            "attempts_total": len(attempts),
            "attempts_with_question": len(attributed),
            "questions": len({a.question_id for a in attributed}),
            "students": len({a.student_id for a in attempts}),
            "transfer_questions_identified": len(transfer_ids),
            "review_logs_total": len(review_rows),
            "review_logs_gap": review_total,
        },
        "notes": notes,
    }


# ================================================================== 报告
def _fmt_rate(rate: Optional[float]) -> str:
    return "样本不足" if rate is None else f"{rate * 100:.1f}%"


def render_summary(report: Dict[str, Any]) -> str:
    """人类可读的报告摘要（写 stdout 用）。"""
    scope = report.get("scope", {})
    metrics = report.get("metrics", {})
    samples = report.get("samples", {})
    lines: List[str] = []
    lines.append("== Learning Gain 评测（Benchmark 维度二） ==")
    kp_names = scope.get("kp_names") or []
    kp_ids = scope.get("kp_ids") or []
    kp_desc = ", ".join(kp_names) if kp_names else (", ".join(kp_ids) if kp_ids else "全部")
    student_desc = ", ".join(scope.get("student_ids") or []) or "全部"
    lines.append(f"范围: 知识点[{kp_desc}] 学生[{student_desc}]")
    lines.append(
        f"窗口: {scope.get('since') or '-inf'} ~ {scope.get('until') or 'now'}"
        f"（review 间隔 >= {scope.get('review_gap_days')} 天）"
    )
    lines.append(f"样本: attempt {samples.get('attempts_with_question', 0)} 条"
                 f"（总 {samples.get('attempts_total', 0)}），"
                 f"题 {samples.get('questions', 0)} 道，"
                 f"学生 {samples.get('students', 0)} 人，"
                 f"迁移题 {samples.get('transfer_questions_identified', 0)} 道，"
                 f"复习记录 {samples.get('review_logs_gap', 0)} 条")
    for key, label in (
        ("first_attempt_accuracy", "首次正确率"),
        ("retest_accuracy", "复测正确率"),
        ("corrected_accuracy", "纠错后正确率"),
        ("transfer_accuracy", "迁移题正确率"),
        ("review_7d_accuracy", "7天复习正确率"),
    ):
        block = metrics.get(key, {})
        lines.append(
            f"  {label:<8} {block.get('correct', 0)}/{block.get('total', 0)}"
            f" = {_fmt_rate(block.get('rate'))}"
        )
    gain_block = metrics.get("learning_gain", {})
    lines.append(
        f"  Learning Gain  pre={_fmt_rate(gain_block.get('pre'))}"
        f" post={_fmt_rate(gain_block.get('post'))}"
        f" g={gain_block.get('gain') if gain_block.get('gain') is not None else '样本不足'}"
        + (f"（{gain_block['note']}）" if gain_block.get("note") else "")
    )
    for note in report.get("notes", []):
        lines.append(f"  [NOTE] {note}")
    return "\n".join(lines)


def _resolve_kp_ids(db: Any, names: Sequence[str], raw_ids: Sequence[str]) -> Tuple[List[uuid.UUID], List[str], List[str]]:
    """把 ``--kp``（名称或 ID）与 ``--kp-id`` 解析成知识点 ID 列表。

    Returns:
        ``(kp_ids, kp_names, unresolved)``；无法解析的名称原样进
        ``unresolved``（由调用方警告，不静默丢弃）。
    """
    kp_ids: List[uuid.UUID] = []
    kp_names: List[str] = []
    unresolved: List[str] = []
    seen: Set[uuid.UUID] = set()

    for raw in list(raw_ids or []):
        qid = _as_uuid(raw)
        if qid is None:
            unresolved.append(str(raw))
        elif qid not in seen:
            seen.add(qid)
            kp_ids.append(qid)

    for name in list(names or []):
        qid = _as_uuid(name)
        if qid is not None:
            if qid not in seen:
                seen.add(qid)
                kp_ids.append(qid)
            continue
        rows = db.execute(
            select(KnowledgePoint.id, KnowledgePoint.name).where(
                KnowledgePoint.name == str(name)
            )
        ).all()
        if not rows:
            unresolved.append(str(name))
            continue
        for row in rows:
            if row[0] not in seen:
                seen.add(row[0])
                kp_ids.append(row[0])
                kp_names.append(str(row[1]))
    return kp_ids, kp_names, unresolved


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 入口：解析参数 → 只读聚合 → 写 JSON 报告 + stdout 摘要。"""
    parser = argparse.ArgumentParser(
        description="Learning Gain 离线评测（Benchmark 维度二：学习效果，只读）"
    )
    parser.add_argument("--kp", action="append", default=[], help="知识点范围：名称或 ID，可重复")
    parser.add_argument("--kp-id", action="append", default=[], help="知识点 ID（UUID），可重复")
    parser.add_argument("--student", action="append", default=[], help="学生 ID（UUID），可重复")
    parser.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS, help=f"时间窗（天，默认 {DEFAULT_WINDOW_DAYS}）")
    parser.add_argument("--since", default=None, help="窗内起点 ISO8601（覆盖 --days）")
    parser.add_argument("--until", default=None, help="窗内终点 ISO8601")
    parser.add_argument(
        "--review-gap-days", type=int, default=7, help="7 天复习认定的最小间隔天数（默认 7）"
    )
    parser.add_argument("--all", action="store_true", help="不限知识点（默认即全部，显式声明更清楚）")
    parser.add_argument("--out", default=None, help="报告输出路径（默认 backend/.qa_cache/eval_learning_gain_<ts>.json）")
    args = parser.parse_args(argv)

    try:
        since = _parse_iso(args.since)
        until = _parse_iso(args.until)
    except ValueError as exc:
        print(f"[ERROR] 时间参数解析失败：{exc}", file=sys.stderr)
        return 2

    student_ids: List[uuid.UUID] = []
    for raw in args.student:
        sid = _as_uuid(raw)
        if sid is None:
            print(f"[ERROR] --student 不是合法 UUID：{raw}", file=sys.stderr)
            return 2
        student_ids.append(sid)

    if since is None and until is None and args.days > 0:
        since = datetime.now(timezone.utc) - timedelta(days=args.days)

    db = SessionLocal()
    try:
        kp_ids, kp_names, unresolved = _resolve_kp_ids(db, args.kp, args.kp_id)
        for name in unresolved:
            print(f"[WARN] 知识点无法解析，已忽略：{name}", file=sys.stderr)

        report = compute_learning_gain(
            db,
            kp_ids=kp_ids,
            student_ids=student_ids,
            since=since,
            until=until,
            review_gap_days=args.review_gap_days,
        )
    finally:
        db.close()

    report["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["scope"] = {
        "kp_ids": [str(k) for k in kp_ids],
        "kp_names": kp_names,
        "student_ids": [str(s) for s in student_ids],
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "review_gap_days": int(args.review_gap_days),
        "all_knowledge_points": bool(args.all) or not kp_ids,
    }
    if unresolved:
        report.setdefault("notes", []).append(f"未解析的知识点参数：{unresolved}")

    out_path = Path(args.out) if args.out else (
        DEFAULT_REPORT_DIR / f"eval_learning_gain_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    print(render_summary(report))
    print(f"== 报告 ==")
    print(f"  已写入 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
