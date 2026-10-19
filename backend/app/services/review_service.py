"""
复习服务层：桥接 FSRS 调度器与现有业务系统

职责：
  1. 卡片生命周期管理（创建、查询、更新、删除）
  2. 与 mastery 系统联动（掌握度变化 → 卡片参数调整）
  3. 每日复习列表生成
  4. 复习结果提交与持久化

集成点：
  - practice_sessions: 练习完成时自动创建卡片
  - mastery / student_mastery: 掌握度变化时调整难度
  - questions / knowledge_points: 卡片内容来源
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("app.services.review_service")
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.services.fsrs_scheduler import Card, FSRSScheduler, ReviewLog
from app.db import SessionLocal
from app.models.learning import ReviewSchedule
from app.services.review_scheduler import (
    INITIAL_EASE_FACTOR,
    MAX_EASE_FACTOR,
    MIN_EASE_FACTOR,
    ReviewState,
    initial_state,
    is_due,
    quality_from_correct,
    review as sm2_review,
)


class ReviewService:
    """复习业务服务"""

    def __init__(self, db: Optional[Session] = None):
        self.db = db
        self.scheduler = FSRSScheduler()

    def _scheduler_for(self, student_id: Optional[str]) -> FSRSScheduler:
        """按学生取调度器：把 student_id 透传给内核裁决（py-fsrs 灰度放量用）。

        灰度是按学生稳定哈希分批的，只有拿到 student_id 才能裁决该学生走
        py-fsrs 还是 SM-2；拿不到时退回无学生上下文的全局裁决（等价于默认 sm2）。
        """
        if student_id is None:
            return self.scheduler
        return FSRSScheduler(student_id=str(student_id))

    def _get_db(self) -> Session:
        if self.db:
            return self.db
        return SessionLocal()

    @staticmethod
    def _to_dt(v):
        """把 raw SQL 读出的日期字段（str/None/datetime）统一为 datetime。"""
        if v is None or isinstance(v, datetime):
            return v
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v)
            except ValueError:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v

    def create_card_for_question(
        self,
        student_id: str,
        question_id: str,
        initial_rating: int = 3,
        now: Optional[datetime] = None,
    ) -> dict:
        """
        为一道题目创建复习卡片（错题入本即建档 / 练习完成时调用）

        幂等与复活口径：
            * 已有**活跃**卡片（``due`` 非空）→ 直接复用，不重复建卡；
            * 已有**暂停**卡片（``due`` 为空，错题攻克联动置空）→ 按
              ``initial_rating`` **复活**重新排程。学生再次做错同一题时
              ``record_mistake`` 会新建错题记录，卡片若不复活将永远沉底、
              再也不进到期队列；
            * 没有任何卡片 → 新建。

        调度一律走 ``_scheduler_for(student_id)``（sm2/fsrs_py 按学生
        稳定哈希灰度裁决），不绕过统一入口——灰度可回滚的前提就在这里。

        Args:
            student_id: 学生 ID
            question_id: 题目 ID
            initial_rating: 初始评分（默认 Good=3；错题入本传 Again=1）
            now: 当前时间

        Returns:
            卡片字典（``created`` / ``revived`` / ``reason`` 等键）
        """
        student_id = str(student_id)
        question_id = str(question_id)
        if now is None:
            now = datetime.now()

        db = self._get_db()

        # 检查是否已存在（连排程状态一起读，供「暂停态复活」判定）
        existing = db.execute(
            text("""SELECT id, due, stability, difficulty, elapsed_days,
                           scheduled_days, reps, lapses, state, last_review
                   FROM review_cards 
                   WHERE student_id = :p1 AND question_id = :p2"""),
            {"p1": student_id, "p2": question_id},
        ).fetchone()

        if existing is not None and self._to_dt(existing[1]) is not None:
            # 活跃卡片：幂等复用，不重复建卡
            return {"id": existing[0], "created": False, "reason": "already_exists"}

        if existing is not None:
            # 暂停态卡片（攻克联动把 due 置空）：从库里重建状态，按
            # initial_rating 复活（错题→Again(1) 近期重现）。
            card = Card(
                id=existing[0],
                due=self._to_dt(existing[1]),
                stability=existing[2],
                difficulty=existing[3],
                elapsed_days=existing[4],
                scheduled_days=existing[5],
                reps=existing[6],
                lapses=existing[7],
                state=existing[8],
                last_review=self._to_dt(existing[9]),
            )
            card, _ = self._scheduler_for(student_id).review_card(
                card, initial_rating, now
            )

            db.execute(
                text("""UPDATE review_cards SET
                    due = :p1, stability = :p2, difficulty = :p3, elapsed_days = :p4,
                    scheduled_days = :p5, reps = :p6, lapses = :p7, state = :p8,
                    last_review = :p9, updated_at = :p10
                   WHERE id = :p11"""),
                {
                    "p1": card.due,
                    "p2": card.stability,
                    "p3": card.difficulty,
                    "p4": card.elapsed_days,
                    "p5": card.scheduled_days,
                    "p6": card.reps,
                    "p7": card.lapses,
                    "p8": card.state,
                    "p9": card.last_review,
                    "p10": now,
                    "p11": card.id,
                },
            )
            db.commit()

            return {
                "id": card.id,
                "created": True,
                "revived": True,
                "due": card.due.isoformat(),
                "stability": card.stability,
                "difficulty": card.difficulty,
                "state": card.state,
            }

        # 创建 FSRS 卡片（调度器按学生取，保证灰度裁决与后续复习一致）
        card = Card()
        card.id = str(uuid.uuid4())
        card, _ = self._scheduler_for(student_id).review_card(
            card, initial_rating, now
        )

        # 持久化到数据库
        db.execute(
            text("""INSERT INTO review_cards 
                (id, student_id, question_id, kp_id, due, stability, difficulty,
                 elapsed_days, scheduled_days, reps, lapses, state, last_review, created_at, updated_at)
               VALUES (:p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, :p9, :p10, :p11, :p12, :p13, :p14, :p15)"""),
            {
                "p1": card.id,
                "p2": student_id,
                "p3": question_id,
                "p4": None,
                "p5": card.due,
                "p6": card.stability,
                "p7": card.difficulty,
                "p8": card.elapsed_days,
                "p9": card.scheduled_days,
                "p10": card.reps,
                "p11": card.lapses,
                "p12": card.state,
                "p13": card.last_review,
                "p14": now,
                "p15": now,
            },
        )
        db.commit()

        return {
            "id": card.id,
            "created": True,
            "revived": False,
            "due": card.due.isoformat(),
            "stability": card.stability,
            "difficulty": card.difficulty,
            "state": card.state,
        }

    def pause_cards_for_question(
        self,
        student_id: str,
        question_id: str,
        now: Optional[datetime] = None,
    ) -> dict:
        """把 (student, question) 的题目粒度复习卡暂停调度（错题攻克联动）。

        暂停语义（**无 schema 变更**，复用既有列）：``review_cards.due`` 置空。
            * ``get_due_cards`` / 复习页自然不再出现该卡（``due <= now``
              不匹配 NULL）；
            * 错题本列表出参的 ``due`` 同步变为 None（学生端不展示
              「何时该再练」）；
            * 卡片行与 ``review_logs`` 历史**全部保留**，不删行；
            * 学生再次做错同一题时 ``create_card_for_question`` 会把卡片
              按 Again(1) 复活（见该方法的「暂停态复活」口径）。

        Args:
            student_id: 学生 ID。
            question_id: 题目 ID。
            now: 当前时间（写 ``updated_at`` 用）。

        Returns:
            ``{"paused": 受影响行数}``；0 = 没有可暂停的卡片（建卡失败 /
            历史数据无卡），属正常情况，调用方按成功处理。
        """
        student_id = str(student_id)
        question_id = str(question_id)
        if now is None:
            now = datetime.now()

        db = self._get_db()
        result = db.execute(
            text("""UPDATE review_cards SET due = NULL, updated_at = :p1
                   WHERE student_id = :p2 AND question_id = :p3"""),
            {"p1": now, "p2": student_id, "p3": question_id},
        )
        db.commit()

        return {"paused": int(result.rowcount or 0)}

    def has_question_card_for_kp(self, student_id: str, kp_id: str) -> bool:
        """该学生是否已有**题目粒度**复习卡覆盖这个知识点（KP 卡去重判据）。

        去重口径（产品拍板）：题目粒度卡优先——同一学生已有题目卡的知识点，
        练习结算不再建 KP 卡（KP 卡仅兜底），避免同一知识点双卡排程、
        到期队列里同一内容重复出现。

        暂停态卡片（攻克后 ``due`` 置空）同样算「已存在」：题目卡是该题
        唯一的调度记录，学生再次做错会按 Again(1) 复活，不需要 KP 卡补位。

        Args:
            student_id: 学生 ID。
            kp_id: 知识点 ID。

        Returns:
            存在至少一张题目卡返回 ``True``，否则 ``False``。
        """
        row = self._get_db().execute(
            text("""SELECT rc.id FROM review_cards rc
                   JOIN questions q ON q.id = rc.question_id
                   WHERE rc.student_id = :p1 AND q.kp_id = :p2
                   LIMIT 1"""),
            {"p1": str(student_id), "p2": str(kp_id)},
        ).fetchone()
        return row is not None

    def create_card_for_knowledge_point(
        self,
        student_id: str,
        kp_id: str,
        initial_rating: int = 3,
        now: Optional[datetime] = None,
    ) -> dict:
        """为一个知识点创建复习卡片"""
        student_id = str(student_id)
        kp_id = str(kp_id)
        if now is None:
            now = datetime.now()

        db = self._get_db()

        existing = db.execute(
            text("""SELECT id FROM review_cards 
               WHERE student_id = :p1 AND kp_id = :p2"""),
            {"p1": student_id, "p2": kp_id},
        ).fetchone()

        if existing:
            return {"id": existing[0], "created": False, "reason": "already_exists"}

        card = Card()
        card.id = str(uuid.uuid4())
        card, _ = self._scheduler_for(student_id).review_card(
            card, initial_rating, now
        )

        db.execute(
            text("""INSERT INTO review_cards 
                (id, student_id, question_id, kp_id, due, stability, difficulty,
                 elapsed_days, scheduled_days, reps, lapses, state, last_review, created_at, updated_at)
               VALUES (:p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, :p9, :p10, :p11, :p12, :p13, :p14, :p15)"""),
            {
                "p1": card.id,
                "p2": student_id,
                "p3": None,
                "p4": kp_id,
                "p5": card.due,
                "p6": card.stability,
                "p7": card.difficulty,
                "p8": card.elapsed_days,
                "p9": card.scheduled_days,
                "p10": card.reps,
                "p11": card.lapses,
                "p12": card.state,
                "p13": card.last_review,
                "p14": now,
                "p15": now,
            },
        )
        db.commit()

        return {
            "id": card.id,
            "created": True,
            "due": card.due.isoformat(),
            "stability": card.stability,
            "difficulty": card.difficulty,
            "state": card.state,
        }

    def submit_review(
        self,
        card_id: str,
        rating: int,
        now: Optional[datetime] = None,
    ) -> dict:
        """
        提交复习结果

        Args:
            card_id: 卡片 ID
            rating: 1=Again, 2=Hard, 3=Good, 4=Easy
            now: 当前时间

        Returns:
            更新后的卡片信息
        """
        card_id = str(card_id)
        if now is None:
            now = datetime.now()

        db = self._get_db()

        # 读取当前卡片状态
        row = db.execute(
            text("""SELECT id, student_id, question_id, kp_id, due, stability, difficulty,
                      elapsed_days, scheduled_days, reps, lapses, state, last_review
               FROM review_cards WHERE id = :p1"""),
            {"p1": card_id},
        ).fetchone()

        if not row:
            raise ValueError(f"Card not found: {card_id}")

        # 重建 Card 对象
        card = Card(
            id=row[0],
            due=self._to_dt(row[4]),
            stability=row[5],
            difficulty=row[6],
            elapsed_days=row[7],
            scheduled_days=row[8],
            reps=row[9],
            lapses=row[10],
            state=row[11],
            last_review=self._to_dt(row[12]),
        )

        # 执行 FSRS 调度（用卡片所属学生做灰度裁决，同一学生始终同一内核）
        card, log = self._scheduler_for(row[1]).review_card(card, rating, now)

        # 更新卡片
        db.execute(
            text("""UPDATE review_cards SET
                due = :p1, stability = :p2, difficulty = :p3, elapsed_days = :p4,
                scheduled_days = :p5, reps = :p6, lapses = :p7, state = :p8,
                last_review = :p9, updated_at = :p10
               WHERE id = :p11"""),
            {
                "p1": card.due,
                "p2": card.stability,
                "p3": card.difficulty,
                "p4": card.elapsed_days,
                "p5": card.scheduled_days,
                "p6": card.reps,
                "p7": card.lapses,
                "p8": card.state,
                "p9": card.last_review,
                "p10": now,
                "p11": card.id,
            },
        )

        # 插入复习记录
        log_id = str(uuid.uuid4())
        db.execute(
            text("""INSERT INTO review_logs
                (id, card_id, student_id, rating, state, elapsed_days, scheduled_days,
                 stability_before, stability_after, difficulty_before, difficulty_after, review_at)
               VALUES (:p1, :p2, :p3, :p4, :p5, :p6, :p7, :p8, :p9, :p10, :p11, :p12)"""),
            {
                "p1": log_id,
                "p2": card.id,
                "p3": row[1],
                "p4": log.rating,
                "p5": log.state,
                "p6": log.elapsed_days,
                "p7": log.scheduled_days,
                "p8": row[5],
                "p9": card.stability,
                "p10": row[6],
                "p11": card.difficulty,
                "p12": now,
            },
        )

        db.commit()

        return {
            "card_id": card.id,
            "due": card.due.isoformat(),
            "stability": card.stability,
            "difficulty": card.difficulty,
            "state": card.state,
            "scheduled_days": card.scheduled_days,
            "reps": card.reps,
            "lapses": card.lapses,
        }

    def get_due_cards(
        self,
        student_id: str,
        limit: int = 20,
        now: Optional[datetime] = None,
    ) -> list[dict]:
        """
        获取今日到期复习卡片

        Args:
            student_id: 学生 ID
            limit: 最大数量
            now: 当前时间

        Returns:
            卡片列表（含题目/知识点信息）
        """
        student_id = str(student_id)
        if now is None:
            now = datetime.now()

        db = self._get_db()

        rows = db.execute(
            text("""SELECT rc.id, rc.question_id, rc.kp_id, rc.due, rc.stability,
                      rc.difficulty, rc.state, rc.scheduled_days, rc.reps, rc.lapses,
                      q.content as question_content, q.type as question_type,
                      kp.name as kp_name, kp.code as kp_code
               FROM review_cards rc
               LEFT JOIN questions q ON rc.question_id = q.id
               LEFT JOIN knowledge_points kp ON rc.kp_id = kp.id
               WHERE rc.student_id = :p1 AND rc.due <= :p2
               ORDER BY rc.due ASC, rc.difficulty DESC
               LIMIT :p3"""),
            {"p1": student_id, "p2": now, "p3": limit},
        ).fetchall()

        cards = []
        for row in rows:
            due_dt = self._to_dt(row[3])
            card = {
                "id": row[0],
                "question_id": row[1],
                "kp_id": row[2],
                "due": due_dt.isoformat() if due_dt else None,
                "stability": row[4],
                "difficulty": row[5],
                "state": row[6],
                "scheduled_days": row[7],
                "reps": row[8],
                "lapses": row[9],
                "retrievability": self._scheduler_for(student_id).get_retrievability(
                    Card(
                        stability=row[4],
                        last_review=due_dt - timedelta(days=row[7])
                        if due_dt and row[7]
                        else None,
                    ),
                    now,
                )
                if row[4] > 0
                else 0.0,
            }

            if row[1]:  # 题目卡片
                card["content"] = {
                    "type": "question",
                    "question_content": row[10],
                    "question_type": row[11],
                }
            elif row[2]:  # 知识点卡片
                card["content"] = {
                    "type": "knowledge_point",
                    "kp_name": row[12],
                    "kp_code": row[13],
                }

            cards.append(card)

        return cards

    def get_review_stats(
        self,
        student_id: str,
        days: int = 30,
    ) -> dict:
        """
        获取复习统计

        Args:
            student_id: 学生 ID
            days: 统计天数

        Returns:
            统计字典
        """
        student_id = str(student_id)
        db = self._get_db()

        since = datetime.now() - timedelta(days=days)

        # 总卡片数
        total = db.execute(
            text("SELECT COUNT(*) FROM review_cards WHERE student_id = :p1"),
            {"p1": student_id},
        ).fetchone()[0]

        # 今日到期
        today_due = db.execute(
            text("""SELECT COUNT(*) FROM review_cards 
               WHERE student_id = :p1 AND due <= :p2"""),
            {"p1": student_id, "p2": datetime.now()},
        ).fetchone()[0]

        # 近期复习次数
        recent_reviews = db.execute(
            text("""SELECT COUNT(*) FROM review_logs 
               WHERE student_id = :p1 AND review_at >= :p2"""),
            {"p1": student_id, "p2": since},
        ).fetchone()[0]

        # 平均评分
        avg_rating = db.execute(
            text("""SELECT AVG(rating) FROM review_logs 
               WHERE student_id = :p1 AND review_at >= :p2"""),
            {"p1": student_id, "p2": since},
        ).fetchone()[0]

        # 各状态卡片数
        state_counts = db.execute(
            text("""SELECT state, COUNT(*) FROM review_cards 
               WHERE student_id = :p1 GROUP BY state"""),
            {"p1": student_id},
        ).fetchall()

        return {
            "total_cards": total,
            "today_due": today_due,
            "recent_reviews": recent_reviews,
            "average_rating": round(avg_rating, 2) if avg_rating else 0,
            "state_distribution": {row[0]: row[1] for row in state_counts},
            "period_days": days,
        }

    def adjust_difficulty_by_mastery(
        self,
        student_id: str,
        kp_id: str,
        mastery_score: float,
    ) -> dict:
        """
        根据掌握度调整卡片难度（mastery 系统联动）

        difficulty 列语义 = SM-2 难度系数 EF（见 fsrs_scheduler：EF 高 =
        更容易记，间隔增长更快），取值范围 `[MIN_EASE_FACTOR, MAX_EASE_FACTOR]`。

        Args:
            student_id: 学生 ID
            kp_id: 知识点 ID
            mastery_score: 掌握度分数 [0, 1]

        Returns:
            调整结果
        """
        student_id = str(student_id)
        kp_id = str(kp_id)
        db = self._get_db()

        # 查找对应卡片
        row = db.execute(
            text("""SELECT id, difficulty FROM review_cards 
               WHERE student_id = :p1 AND kp_id = :p2"""),
            {"p1": student_id, "p2": kp_id},
        ).fetchone()

        if not row:
            return {"adjusted": False, "reason": "card_not_found"}

        card_id, current_difficulty = row

        # 掌握度越高，卡片越「容易记住」→ EF（difficulty）应上调，间隔增长加快；
        # 掌握度越低 → EF 下调，复习更密。
        # mastery_score 0.0 -> EF -1.0   mastery_score 1.0 -> EF +1.0
        adjustment = (mastery_score - 0.5) * 2.0
        new_difficulty = min(
            max(float(current_difficulty) + adjustment, MIN_EASE_FACTOR),
            MAX_EASE_FACTOR,
        )

        db.execute(
            text(
                "UPDATE review_cards SET difficulty = :p1, updated_at = :p2 WHERE id = :p3"
            ),
            {"p1": new_difficulty, "p2": datetime.now(), "p3": card_id},
        )
        db.commit()

        return {
            "adjusted": True,
            "card_id": card_id,
            "old_difficulty": current_difficulty,
            "new_difficulty": new_difficulty,
            "adjustment": adjustment,
        }

    def sync_cards_from_mastery(
        self,
        student_id: str,
        now: Optional[datetime] = None,
    ) -> dict:
        """
                从 mastery 表同步创建/更新复习卡片

                遍历 student_mastery 表，为每个有掌握度记录的知识点创建卡片

                Args:
                    student_id: 学生 ID
                    now: 当前时间

                Returns:
        同步结果统计
        """
        student_id = str(student_id)
        if now is None:
            now = datetime.now()

        db = self._get_db()

        # 获取学生的 mastery 记录
        rows = db.execute(
            text("""SELECT knowledge_point_id, mastery_score FROM student_mastery 
               WHERE student_id = :p1"""),
            {"p1": student_id},
        ).fetchall()

        created = 0
        updated = 0
        skipped = 0

        for kp_id, mastery_score in rows:
            # 检查是否已有卡片
            existing = db.execute(
                text("""SELECT id FROM review_cards 
                   WHERE student_id = :p1 AND kp_id = :p2"""),
                {"p1": student_id, "p2": kp_id},
            ).fetchone()

            if existing:
                # 更新难度
                self.adjust_difficulty_by_mastery(student_id, kp_id, mastery_score)
                updated += 1
            else:
                # 创建新卡片，根据掌握度设置初始评分
                if mastery_score >= 0.8:
                    initial_rating = 4  # Easy
                elif mastery_score >= 0.6:
                    initial_rating = 3  # Good
                elif mastery_score >= 0.4:
                    initial_rating = 2  # Hard
                else:
                    initial_rating = 1  # Again

                self.create_card_for_knowledge_point(
                    student_id, kp_id, initial_rating, now
                )
                created += 1

        return {
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "total": len(rows),
        }


# ==================================================================== #65 模块级薄包装
# 组 A（FT-001~006）契约：测试与 API 以模块级函数调用本服务，薄封装
# ``review_scheduler`` 纯算法层与 ``review_schedule`` ORM 行之间的转换与 upsert，
# 不触碰 fsrs_scheduler（FSRS 算法主体留待 Sprint 7 落地）。


def _coerce_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """DB 读回的 datetime 统一补 UTC（SQLite 常返回 naive）。"""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


def _row_to_state(row) -> ReviewState:
    """ORM 行 -> ReviewState（key 借 kp_id，本层不关心 kp_code 语义）。"""
    return ReviewState(
        key=str(row.knowledge_point_id),
        ease_factor=row.ease_factor,
        interval_days=row.interval_days,
        repetitions=row.repetitions,
        last_reviewed_at=_coerce_utc(row.last_reviewed_at),
        due_at=_coerce_utc(row.due_at),
    )


def _find_row(session: Session, student_id, kp_id) -> Optional[ReviewSchedule]:
    return session.execute(
        select(ReviewSchedule).where(
            ReviewSchedule.student_id == student_id,
            ReviewSchedule.knowledge_point_id == kp_id,
        )
    ).scalar_one_or_none()


def _ensure_row(
    session: Session, student_id: uuid.UUID, kp_id: uuid.UUID
) -> ReviewSchedule:
    """按 (student_id, kp_id) 查或建初始排程行（due_at=None -> 立即到期）。"""
    row = _find_row(session, student_id, kp_id)
    if row is not None:
        return row
    row = ReviewSchedule(
        id=uuid.uuid4(),
        student_id=student_id,
        knowledge_point_id=kp_id,
        ease_factor=INITIAL_EASE_FACTOR,
        interval_days=0,
        repetitions=0,
        last_reviewed_at=None,
        due_at=None,
    )
    session.add(row)
    session.flush()
    return row


def seed_initial(session: Session, student_id: uuid.UUID, kp_ids) -> int:
    """为多个知识点建立「从未复习」的初始排程行；幂等，返回新建行数。"""
    created = 0
    for kp_id in kp_ids:
        if _find_row(session, student_id, kp_id) is not None:
            continue
        _ensure_row(session, student_id, kp_id)
        created += 1
    session.flush()
    return created


def record_review(
    session: Session,
    student_id: uuid.UUID,
    kp_id: uuid.UUID,
    quality: int,
    now: Optional[datetime] = None,
) -> ReviewState:
    """记录一次复习并推进 SM-2 排程（upsert 一行），返回新状态。"""
    if now is None:
        now = datetime.now(timezone.utc)
    row = _ensure_row(session, student_id, kp_id)
    state = _row_to_state(row)
    plan = sm2_review(state, quality, now)
    row.ease_factor = plan.ease_factor
    row.interval_days = plan.interval_days
    row.repetitions = plan.repetitions
    row.last_reviewed_at = plan.last_reviewed_at
    row.due_at = plan.due_at
    session.flush()
    return _row_to_state(row)


def record_review_from_result(
    session: Session,
    student_id: uuid.UUID,
    kp_id: uuid.UUID,
    correct: bool,
    verdict: str = "pending",
    now: Optional[datetime] = None,
) -> ReviewState:
    """用「对/错 + verdict」收敛 quality 后推进排程（API 入口）。"""
    return record_review(
        session, student_id, kp_id, quality_from_correct(correct, verdict), now=now
    )


def list_due_reviews(
    session: Session,
    student_id: uuid.UUID,
    now: Optional[datetime] = None,
) -> List[Tuple[uuid.UUID, ReviewState]]:
    """当前该复习的知识点列表：[(kp_id, state), ...]，未复习(due_at=None)的排最前。"""
    if now is None:
        now = datetime.now(timezone.utc)
    rows = (
        session.execute(
            select(ReviewSchedule).where(ReviewSchedule.student_id == student_id)
        )
        .scalars()
        .all()
    )
    due: List[Tuple[uuid.UUID, ReviewState]] = []
    for row in rows:
        state = _row_to_state(row)
        if is_due(state, now):
            due.append((row.knowledge_point_id, state))
    due.sort(
        key=lambda t: (
            t[1].due_at is None,
            t[1].due_at or datetime.min.replace(tzinfo=timezone.utc),
        )
    )
    return due


def get_state(
    session: Session, student_id: uuid.UUID, kp_id: uuid.UUID
) -> Optional[ReviewState]:
    """读取某学生某知识点的当前排程状态；无记录返回 None。"""
    row = _find_row(session, student_id, kp_id)
    return _row_to_state(row) if row is not None else None


def upsert_cards_from_practice(
    student_id: uuid.UUID,
    kp_results: dict,
    now: Optional[datetime] = None,
) -> dict:
    """练习结算后为本次练到的知识点自动建档复习卡片（FSRS 卡流联动）。

    与 ``seed_initial``（SM-2 排程行）互补：本函数写 **review_cards** 表，
    供 Review.vue 的卡片流复习页使用。练习答对的知识点按 Good(3) 建档
    （隔日到期），答错/未攻克的知识点按 Again(1) 建档（近期再次出现）。

    去重口径（产品拍板）：**题目粒度卡优先**。建 KP 卡前先查该学生是否
    已有题目粒度卡片覆盖这个知识点（``has_question_card_for_kp``），
    存在则跳过——KP 卡仅兜底，避免同一内容双卡排程。

    Args:
        student_id: 学生 ID。
        kp_results: ``{kp_id(str): {"correct": bool, "attempts": int}}``，
            本次练习各知识点的答题情况（可归因的题才有 kp）。
        now: 当前时间（UTC）。

    Returns:
        ``{"created", "skipped", "total"}``：新建 / 已存在或被题目卡去重
        跳过 / 参与评估总数。
    """
    svc = ReviewService()
    created = skipped = 0
    for kp_id, res in (kp_results or {}).items():
        try:
            correct = bool(res.get("correct", False))
            initial_rating = 3 if correct else 1
            # 去重口径：题目粒度卡优先——已有题目卡覆盖该知识点时跳过 KP 卡
            # （KP 卡仅兜底）。见 ReviewService.has_question_card_for_kp。
            if svc.has_question_card_for_kp(str(student_id), str(kp_id)):
                skipped += 1
                continue
            out = svc.create_card_for_knowledge_point(
                str(student_id), str(kp_id), initial_rating, now or datetime.now()
            )
            if out.get("created"):
                created += 1
            else:
                skipped += 1
        except Exception:  # noqa: BLE001 - 建卡失败不能拖垮练习结算
            logger.exception("练习结算建档复习卡失败 kp=%s", kp_id)
            skipped += 1
    return {"created": created, "skipped": skipped, "total": len(kp_results or {})}


def get_question_due_map(
    db: Session,
    student_id: Any,
) -> Dict[str, Optional[str]]:
    """查询学生的题目粒度复习卡到期时间：``{question_id: due ISO | None}``。

    用途：错题本列表出参补 ``due`` 字段（学生端「何时该再练」）。口径：
        * 有活跃卡片 → 下次到期时间（ISO 8601 字符串）；
        * 卡片已暂停（攻克联动置空 ``due``）→ ``None``；
        * 从未建卡 / 建卡失败的历史错题 → 不进 map，调用方 ``.get``
          得到 ``None``（前端据此不展示该信息）。

    同一题存在多张卡的异常场景（历史数据）取**最早的非空到期时间**。

    容错口径：``due`` 是错题本列表的附属信息，查询失败（如 review_cards
    表尚未建好的历史库）只 warning 并返回空 dict——错题本主链路不因
    复习卡读取故障而 500（与建卡兜底同款思路）。

    Args:
        db: 数据库会话（调用方请求级会话，只读查询）。
        student_id: 学生 ID。

    Returns:
        ``{question_id: due ISO 字符串或 None}`` 的字典；没有任何题目卡
        （或查询失败）时返回空字典。
    """
    try:
        rows = db.execute(
            text("""SELECT question_id, due FROM review_cards
                    WHERE student_id = :p1 AND question_id IS NOT NULL
                    ORDER BY (due IS NULL), due ASC"""),
            {"p1": str(student_id)},
        ).fetchall()
    except Exception:  # noqa: BLE001 - 附属信息读取失败不拖垮错题本列表
        logger.warning(
            "读取题目复习卡到期时间失败 student=%s", student_id, exc_info=True
        )
        return {}

    due_map: Dict[str, Optional[str]] = {}
    for question_id, due in rows:
        key = str(question_id)
        if key in due_map and due_map[key] is not None:
            continue  # 已记录更早的非空到期（ORDER BY 保证非空且小的先到）
        due_dt = ReviewService._to_dt(due)
        due_map[key] = due_dt.isoformat() if due_dt is not None else None
    return due_map
