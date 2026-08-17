"""把学习任务里"含 archived 题的 doing session"全部归档为 done。

背景（2026-08-18 题库清洗后续）：
- 2026-08-17 清洗把 6 条真残缺题（status=archived）+ 74 条格式污染题修了
- 数据库主表已经干净（active 库里不再含 archived 题的 UUID）
- 但 history.learning_tasks.items（JSON 快照）里有 29 个 status=doing 的老 session
  仍引用这些 archived 的 UUID，导致前端 localStorage 恢复时还是会显示
- 这些老 session 现在"卡死"：session 不会被新抽题覆盖（用户主动取消/退出才会清理），
  前端通过 localStorage 持久化恢复时显示这道题，对学生体验差。

策略：
- 只动 status 字段，**不动 attempts 数据**（用户已作答的 attempts 永远保留）
- done 状态让下次进练习页 force 到 filter phase 重新抽题
- 导出 CSV 留痕（红线）

可逆性：
- 这只是 UPDATE status，备份表 questions_incomplete_recover_20260817 已存原始题内容
- session 反向操作 = UPDATE status='doing'

调用方式：python -m scripts.data_cleanup.archive_stale_sessions_20260818
"""
from __future__ import annotations

import csv
import datetime
import json
import os
import sqlite3
import uuid
from pathlib import Path

DB_PATH = "dev.db"
OUT_DIR = Path("scripts/data_cleanup")
TS = datetime.datetime.now().strftime("%Y%m%d_%H%M")

# 只挑"含 archived 题 + status=doing"的 session
# 用 LIKE 在 SQL 层粗筛（archived UUID 数量有限，性能可接受）
QUERY_STALE_SESSIONS = """
SELECT t.id, t.student_id, t.status, t.generated_at, t.finished_at, t.items
FROM learning_tasks t
WHERE t.status = 'doing'
  AND EXISTS (
    SELECT 1 FROM questions q
    WHERE q.status = 'archived'
      AND t.items LIKE ('%' || q.id || '%')
  )
ORDER BY t.generated_at DESC
"""


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    rows = cur.execute(QUERY_STALE_SESSIONS).fetchall()
    print(f"[1/4] 命中 stale doing session（doing + 含 archived 题）: {len(rows)} 个")
    if not rows:
        print("  无需处理，退出")
        return

    # 列出每个 session 里含几条 archived 题
    archived_ids = [
        r[0] for r in cur.execute(
            "SELECT id FROM questions WHERE status='archived'"
        )
    ]
    print(f"  当前 archived 题 UUID 数: {len(archived_ids)}")

    # 准备 CSV 输出
    out_csv = OUT_DIR / f"20260818_stale_doing_sessions_{TS}.csv"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows_for_csv = []
    for r in rows:
        items = json.loads(r["items"]) if r["items"] else []
        archived_in_items = [it.get("question_id", "?")[:8] for it in items
                              if isinstance(it, dict)
                              and it.get("question_id") in archived_ids]
        rows_for_csv.append({
            "session_id": r["id"],
            "student_id": r["student_id"][:8] if r["student_id"] else "",
            "status": r["status"],
            "generated_at": r["generated_at"],
            "finished_at": r["finished_at"] or "",
            "items_count": len(items),
            "archived_q_count": len(archived_in_items),
            "archived_q_ids": ",".join(archived_in_items[:5]),
        })

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_for_csv[0].keys()))
        w.writeheader()
        for r in rows_for_csv:
            w.writerow(r)
    print(f"  CSV: {out_csv}")

    # 第一遍警告：让用户审阅
    print()
    print(f"[2/4] 准备 UPDATE {len(rows)} 个 session 的 status='done'")
    print("  （不动 attempts 表，不动 items JSON，不入 mistakes 表）")
    print("  （用户已作答的 attempts 永久保留；下次进练习页自动到 filter 重抽）")

    # 第二遍执行
    session_ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(session_ids))
    cur.execute(
        f"""
        UPDATE learning_tasks
        SET status='done', finished_at=CURRENT_TIMESTAMP
        WHERE id IN ({placeholders})
        """,
        session_ids,
    )
    con.commit()
    n_updated = cur.execute("SELECT changes()").fetchone()[0]
    print(f"[3/4] 影响行数: {n_updated}")

    # 校验
    remain = cur.execute(
        """
        SELECT COUNT(*) FROM learning_tasks
        WHERE status='doing'
          AND EXISTS (
            SELECT 1 FROM questions q
            WHERE q.status='archived'
              AND learning_tasks.items LIKE ('%' || q.id || '%')
          )
        """
    ).fetchone()[0]
    print(f"[4/4] doing session 含 archived 题: {remain}（应为 0）")

    # 全库 health check
    active_cnt = cur.execute(
        "SELECT COUNT(*) FROM questions WHERE status='active'"
    ).fetchone()[0]
    doing_cnt = cur.execute(
        "SELECT COUNT(*) FROM learning_tasks WHERE status='doing'"
    ).fetchone()[0]
    print()
    print("=== 全库状态 ===")
    print(f"  questions active: {active_cnt}")
    print(f"  learning_tasks doing: {doing_cnt}")


if __name__ == "__main__":
    main()
