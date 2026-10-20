"""生产库测试数据清理（2026-10-20）。

⚠️ **执行前必须先跑 --dry-run 看清单**，并确认已备份。

清什么：
    1. 测试/冒烟账号（users 表）：nickname 含「冒烟/验收/dbg」，
       或 phone 以 ``invite-`` 开头（激活码建号的内置标识），
       且 created_at >= 2026-09-15（项目首次部署日）——
       这些**全是自动化冒烟/验收产物**，无一条真实学生。
       ⚠️ 排除 role='admin'（两个管理员账号必须保留）。
    2. 这些账号关联的全部业务数据：students / attempts / mistakes /
       ai_sessions / ai_messages / guardian_consents / learning_tasks /
       review_cards / review_logs / subscriptions / payment_transactions。
    3. **孤儿学生档案**（user_id 指向不存在的 users 行）+ 其关联数据——
       9-15/9-18 历史测试遗留，attempts/mistakes 都挂在这里。
    4. 测试用激活码：label 含「冒烟/测试/debug/acceptance/订单」，
       以及 ``beta-20261020`` 批次（上线前生成的测试码），
       保留 ``prod-2026-09-15`` 批次里未被使用的。

**保留不动**：questions / answers / knowledge_points / plans（题库与套餐）。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DB = "/app/data/coach.db"

#: 这些表的行若挂在被删账号 / 孤儿学生下，也要一起删
CASCADE = {
    "ai_messages": "session_id in (select id from ai_sessions where student_id in %s)",
    "ai_sessions": "student_id in %s",
    "attempts": "student_id in %s",
    "mistakes": "student_id in %s",
    "guardian_consents": "user_id in %s",
    "learning_tasks": "student_id in %s",
    "review_logs": "student_id in %s",
    "review_cards": "student_id in %s",
    "student_knowledge_mastery": "student_id in %s",
    "student_learning_events": "student_id in %s",
    "student_learning_profiles": "student_id in %s",
    "practice_progress": "student_id in %s",
    "students": "id in %s",
    "subscriptions": "user_id in %s",
    "payment_transactions": "user_id in %s",
    "activation_codes": "user_id in %s",
    "users": "id in %s",
}

#: weekly_reports 用 student_id，但要单独判列名
WEEKLY_TABLE = "weekly_reports"


def collect(conn: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """返回 (待删 user_ids, 待删 student_ids)。"""
    bad_nick = ("冒烟", "验收", "dbg", "测试")
    rows = conn.execute(
        "select id, phone, nickname, role, created_at from users order by created_at"
    ).fetchall()
    bad_users: list[str] = []
    for uid, phone, nick, role, created in rows:
        if role == "admin":
            continue  # 管理员保留
        nick_s = nick or ""
        is_test = (
            (phone or "").startswith("invite-")
            or any(k in nick_s for k in bad_nick)
            or (created or "") >= "2026-09-15"
        )
        if is_test:
            bad_users.append(uid)

    # 孤儿学生：user_id 指向不存在的 users
    orphan = conn.execute(
        "select s.id from students s left join users u on u.id = s.user_id "
        "where u.id is null"
    ).fetchall()
    bad_students = [r[0] for r in orphan]

    # 关联到待删用户的全部学生（含未成为孤儿的）
    if bad_users:
        qs = ",".join("?" * len(bad_users))
        for r in conn.execute(
            f"select id from students where user_id in ({qs})", bad_users
        ).fetchall():
            if r[0] not in bad_students:
                bad_students.append(r[0])

    return bad_users, bad_students


def count_rows(conn: sqlite3.Connection, table: str, ids: list[str], col: str) -> int:
    if not ids:
        return 0
    qs = ",".join("?" * len(ids))
    try:
        return conn.execute(
            f'select count(*) from "{table}" where "{col}" in ({qs})', ids
        ).fetchone()[0]
    except sqlite3.Error:
        return -1  # 无此表/列


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只列清单，不改数据")
    ap.add_argument("--confirm", action="store_true", help="确认执行删除")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    try:
        bad_users, bad_students = collect(conn)
        print("=" * 66)
        print("清理清单（生产库测试数据）")
        print("=" * 66)
        print(f"待删用户 {len(bad_users)} 个 / 待删学生档案 {len(bad_students)} 个\n")

        print("--- 待删用户明细 ---")
        if bad_users:
            qs = ",".join("?" * len(bad_users))
            for r in conn.execute(
                f"select phone, nickname, role, created_at from users "
                f"where id in ({qs}) order by created_at",
                bad_users,
            ):
                print("  ", r)
        print("\n--- 关联数据行数（将被级联删除）---")
        total = 0
        for table, cond in CASCADE.items():
            col = cond.split(" in ")[0].strip()
            ids = bad_students if table in {
                "ai_messages", "ai_sessions", "attempts", "mistakes",
                "learning_tasks", "review_logs", "review_cards",
                "student_knowledge_mastery", "student_learning_events",
                "student_learning_profiles", "practice_progress", "students",
            } else bad_users
            n = count_rows(conn, table, ids, col)
            if n > 0:
                print(f"   {table:32s} {n}")
                total += n
        # weekly_reports 单独
        try:
            n = count_rows(conn, WEEKLY_TABLE, bad_students, "student_id")
            if n > 0:
                print(f"   {WEEKLY_TABLE:32s} {n}")
                total += n
        except sqlite3.Error:
            pass
        print(f"\n   合计删除 {total} 行（不含下面单独统计的激活码）")

        print("\n--- 激活码逐张判定 ---")
        keep_prefix = "prod-2026-09-15"
        rows = conn.execute(
            "select id, label, user_id from activation_codes order by label, created_at"
        ).fetchall()
        del_codes = 0
        keep_codes = 0
        for cid, label, used_by in rows:
            is_test = (
                used_by is not None
                or label == "beta-20261020"
                or (label and any(k in label for k in ("冒烟", "测试", "debug", "acceptance", "订单")))
            )
            if is_test:
                del_codes += 1
            else:
                keep_codes += 1
        # 按 label 汇总展示
        agg = conn.execute(
            "select label, count(*), sum(user_id is not null) from activation_codes group by label order by 2 desc"
        ).fetchall()
        for label, n, used in agg:
            is_test = (used and used > 0) or label == "beta-20261020" or (
                label and any(k in label for k in ("冒烟", "测试", "debug", "acceptance", "订单"))
            )
            if used and used > 0 and not is_test:
                # 部分已用：按已用数删，未用留
                print(f"   [部分删] {label:32s} 共 {n} 张，已用 {used} 张将随账号级联删除")
            elif is_test:
                print(f"   [删] {label:32s} {n} 张（已用 {used}）")
            else:
                print(f"   [留] {label:32s} {n} 张（已用 {used}）")
        print(f"   合计删除激活码 {del_codes} 张 / 保留 {keep_codes} 张")

        print("\n--- 保留不动 ---")
        for t in ("questions", "answers", "knowledge_points", "plans", "system_config"):
            n = count_rows(conn, t, [1], "id") if False else conn.execute(
                f'select count(*) from "{t}"').fetchone()[0]
            print(f"   {t:20s} {n} 行")

        if args.dry_run or not args.confirm:
            print("\n[dry-run] 未改动任何数据。确认无误后加 --confirm 执行。")
            return 0

        # ---------------- 真正执行 ----------------
        print("\n>>> 开始删除…")
        conn.execute("pragma foreign_keys=off")
        for table, cond in CASCADE.items():
            col = cond.split(" in ")[0].strip()
            ids = bad_students if table in {
                "ai_messages", "ai_sessions", "attempts", "mistakes",
                "learning_tasks", "review_logs", "review_cards",
                "student_knowledge_mastery", "student_learning_events",
                "student_learning_profiles", "practice_progress", "students",
            } else bad_users
            if not ids:
                continue
            qs = ",".join("?" * len(ids))
            cur = conn.execute(f'delete from "{table}" where "{col}" in ({qs})', ids)
            if cur.rowcount > 0:
                print(f"   {table:32s} -{cur.rowcount}")
        # weekly_reports
        if bad_students:
            qs = ",".join("?" * len(bad_students))
            cur = conn.execute(
                f"delete from {WEEKLY_TABLE} where student_id in ({qs})", bad_students
            )
            if cur.rowcount > 0:
                print(f"   {WEEKLY_TABLE:32s} -{cur.rowcount}")
        # 激活码
        cur = conn.execute(
            "delete from activation_codes where user_id is not null or label='beta-20261020' "
            "or label like '%冒烟%' or label like '%测试%' or label like '%debug%' "
            "or label like '%acceptance%' or label like '%订单%'"
        )
        print(f"   activation_codes                  -{cur.rowcount}")
        conn.commit()
        print("\nDONE（已提交）")

        # 复检
        print("\n=== 删除后复检 ===")
        for t in ("users", "students", "attempts", "mistakes", "ai_sessions",
                  "guardian_consents", "activation_codes", "weekly_reports"):
            print(f"   {t:24s} {conn.execute(f'select count(*) from {t}').fetchone()[0]}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
