# -*- coding: utf-8 -*-
"""把 generate_math_bank.py 产出的 JSON 批量写入生产库 questions/answers 表（方向七）。

幂等：以 (content) 判重（questions.content 无唯一约束，用 SELECT 预检跳过已存在题面），
可安全重跑。只新增不修改存量题。
用法（容器内）：
    python3 /tmp/apply_mathgen_bank.py /tmp/mathgen_bank_v1.json /app/data/coach.db
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

VERBOSE = True


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: apply_mathgen_bank.py <bank.json> <db>")
        return 2
    bank_path, db_path = sys.argv[1], sys.argv[2]

    with open(bank_path, encoding="utf-8") as f:
        bank = json.load(f)
    items = bank["questions"]
    print(f"[bank] total={len(items)}")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    # 已有题面集合（防重）
    existing = {r[0] for r in cur.execute("SELECT content FROM questions").fetchall()}
    print(f"[db] existing questions={len(existing)}")

    inserted_q = 0
    inserted_a = 0
    skipped = 0
    now = utcnow()

    for it in items:
        q = it["question"]
        a = it["answer"]
        if q["content"] in existing:
            skipped += 1
            continue
        # content 含非法序列化 NULL 的 LaTeX 命令需保持原样；JSON 内已保证非空
        cur.execute(
            """INSERT OR IGNORE INTO questions
               (id, content, image_url, subject, grade, difficulty, kp_id, type,
                options, status, edition, source_type, section, source_doc,
                kp_verified, source_tag, ship_ready, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                q["id"], q["content"], None, q["subject"], q["grade"], q["difficulty"],
                q["kpId"], q["type"], None, q["status"], q.get("edition"),
                q.get("sourceType"), None, q.get("sourceDoc"),
                1 if q.get("kpVerified", True) else 0,
                q.get("sourceTag", "yellow"), 1 if q.get("shipReady", True) else 0,
                now,
            ),
        )
        if cur.rowcount == 0:
            skipped += 1
            continue
        cur.execute(
            """INSERT OR REPLACE INTO answers
               (id, question_id, correct_answer, analysis, distractor_analysis)
               VALUES (?,?,?,?,?)""",
            (str(uuid.uuid4()), q["id"], a["correctAnswer"], a.get("analysis"), ""),
        )
        inserted_q += 1
        inserted_a += 1
        existing.add(q["content"])

    conn.commit()
    print(f"[done] inserted questions={inserted_q} answers={inserted_a} skipped={skipped}")
    # 校验
    total = cur.execute("SELECT COUNT(*) FROM questions WHERE source_doc='mathgenerator'").fetchone()[0]
    noans = cur.execute(
        "SELECT COUNT(*) FROM questions q LEFT JOIN answers a ON a.question_id=q.id "
        "WHERE q.source_doc='mathgenerator' AND a.id IS NULL"
    ).fetchone()[0]
    print(f"[audit] mathgenerator questions total={total} missing_answer={noans}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())