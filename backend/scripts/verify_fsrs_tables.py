# -*- coding: utf-8 -*-
"""FSRS 表结构验证脚本：用纯 SQL 模拟 review_service 核心操作，不依赖 pydantic/fastapi。

用法：
    python backend/scripts/verify_fsrs_tables.py backend/dev.db
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_fsrs_tables.py <db>")
        return 2
    db_path = sys.argv[1]
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 0. 表存在性
    tables = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('review_cards','review_logs')"
    ).fetchall()}
    assert tables >= {"review_cards", "review_logs"}, f"missing tables: {tables}"
    print("[check] tables exist OK")

    # 1. 取一个学生
    student = cur.execute("SELECT id FROM students LIMIT 1").fetchone()
    if not student:
        print("[SKIP] no student found")
        return 0
    sid = student["id"]
    print(f"[student] {sid}")

    # 2. 取一道题
    q = cur.execute("SELECT id FROM questions WHERE status='active' LIMIT 1").fetchone()
    if not q:
        print("[SKIP] no active question found")
        return 0
    qid = q["id"]

    # 3. 创建卡片（模拟 create_card_for_question）
    card_id = str(uuid.uuid4())
    cur.execute(
        """INSERT INTO review_cards
           (id, student_id, question_id, kp_id, due, stability, difficulty,
            elapsed_days, scheduled_days, reps, lapses, state, last_review, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (card_id, sid, qid, None, now_iso(), 0.0, 0.0, 0, 0, 0, 0, 0, None, now_iso(), now_iso()),
    )
    conn.commit()
    print(f"[insert] review_cards id={card_id}")

    # 4. 提交复习（模拟 submit_review，rating=3 Good）
    log_id = str(uuid.uuid4())
    review_at = now_iso()
    cur.execute(
        """UPDATE review_cards SET
           due=?, stability=?, difficulty=?, elapsed_days=?, scheduled_days=?,
           reps=?, lapses=?, state=?, last_review=?, updated_at=?
           WHERE id=?""",
        (now_iso(), 3.5, 5.0, 0, 1, 1, 0, 1, review_at, review_at, card_id),
    )
    cur.execute(
        """INSERT INTO review_logs
           (id, card_id, student_id, rating, state, elapsed_days, scheduled_days,
            stability_before, stability_after, difficulty_before, difficulty_after, review_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (log_id, card_id, sid, 3, 1, 0, 1, 0.0, 3.5, 0.0, 5.0, review_at),
    )
    conn.commit()
    print(f"[review] review_logs id={log_id}")

    # 5. 查询到期卡片（模拟 get_due_cards）
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    rows = cur.execute(
        """SELECT rc.id, rc.question_id, rc.kp_id, rc.due, rc.stability,
                  rc.difficulty, rc.state, rc.scheduled_days, rc.reps, rc.lapses,
                  q.content as question_content, q.type as question_type
           FROM review_cards rc
           LEFT JOIN questions q ON rc.question_id = q.id
           WHERE rc.student_id = ? AND rc.due <= ?
           ORDER BY rc.due ASC, rc.difficulty DESC
           LIMIT ?""",
        (sid, tomorrow, 20),
    ).fetchall()
    print(f"[due] {len(rows)} cards due <= tomorrow")

    # 6. 统计（模拟 get_review_stats + admin review_stats）
    total = cur.execute("SELECT COUNT(*) FROM review_cards").fetchone()[0]
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    due_today = cur.execute(
        "SELECT COUNT(*) FROM review_cards WHERE student_id=? AND due <= ?", (sid, tomorrow)
    ).fetchone()[0]
    recent = cur.execute(
        "SELECT COUNT(*) FROM review_logs WHERE review_at >= ?",
        ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),),
    ).fetchone()[0]
    avg_rating = cur.execute("SELECT AVG(rating) FROM review_logs").fetchone()[0]
    state_dist = cur.execute("SELECT state, COUNT(*) FROM review_cards GROUP BY state").fetchall()
    print(f"[stats] total={total} due_today={due_today} recent_30d={recent} avg_rating={avg_rating}")
    print(f"[state] {state_dist}")

    # 7. 清理测试数据
    cur.execute("DELETE FROM review_logs WHERE card_id=?", (card_id,))
    cur.execute("DELETE FROM review_cards WHERE id=?", (card_id,))
    conn.commit()
    print("[clean] test data removed")

    conn.close()
    print("[PASS] all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
