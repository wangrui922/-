# -*- coding: utf-8 -*-
"""Audit 质检：对 source_tag='yellow' 的 mathgen_bank 题做自动质检并转绿。

质检口径：
  1. 选择题：options JSON 必须 4 项、值不重复、correct_answer 能匹配 A/B/C/D 之一
  2. 判断题：correct_answer 必须为 "正确" 或 "错误"
  3. 填空题：correct_answer 非空（暂不入池，不转绿）
  4. 所有题：kp_id 必须在 knowledge_points 中存在
  5. content 非空、type 非空

通过的题 source_tag -> 'green'；
失败的题保留 'yellow' 并记录到问题台账 JSON。

用法：
    python backend/scripts/audit_yellow_green.py backend/dev.db
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Optional

def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def audit(db_path: str) -> int:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    kp_ids = {r[0] for r in cur.execute("SELECT id FROM knowledge_points").fetchall()}

    rows = cur.execute("""
        SELECT q.id, q.content, q.type, q.options, q.kp_id,
               a.correct_answer, a.analysis
        FROM questions q
        JOIN answers a ON a.question_id = q.id
        WHERE q.source_doc = 'mathgen_bank'
          AND q.status = 'active'
          AND q.source_tag = 'yellow'
    """).fetchall()

    passed: list[str] = []
    failed: list[dict] = []

    for r in rows:
        qid = r["id"]
        qtype = r["type"]
        content = (r["content"] or "").strip()
        opts_json = r["options"]
        kp_id = r["kp_id"]
        ca = (r["correct_answer"] or "").strip()

        issues: list[str] = []

        if not content:
            issues.append("content_empty")
        if not qtype:
            issues.append("type_empty")
        if not ca:
            issues.append("correct_answer_empty")
        if kp_id not in kp_ids:
            issues.append("kp_id_missing")

        if qtype == "选择":
            try:
                opts = json.loads(opts_json) if opts_json else {}
            except Exception:
                opts = {}
                issues.append("options_json_invalid")
            if len(opts) != 4:
                issues.append(f"options_count_{len(opts)}")
            if len(set(opts.values())) != len(opts):
                issues.append("options_dup")
            keys = set(opts.keys())
            if keys != {"A", "B", "C", "D"}:
                issues.append(f"options_keys_{keys}")
            # correct_answer 提取键位
            key = ca.replace("选", "").strip().upper()
            if key not in opts:
                issues.append(f"ca_key_mismatch_{ca}")

        elif qtype == "判断":
            if ca not in ("正确", "错误"):
                issues.append(f"judge_ca_invalid_{ca}")

        elif qtype == "填空":
            # 填空题暂不入池，不转绿（但保留 yellow 以便未来支持）
            issues.append("fill_not_auto_judgeable")

        if issues:
            failed.append({"id": qid, "content": content[:60], "type": qtype, "issues": issues})
        else:
            passed.append(qid)

    now = utcnow()

    # 转绿
    if passed:
        cur.executemany(
            "UPDATE questions SET source_tag = 'green' WHERE id = ?",
            [(qid,) for qid in passed],
        )

    # 保存问题台账
    log_path = db_path + ".audit_yellow_fail.json"
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump({
            "audit_at": now,
            "total_yellow": len(rows),
            "passed": len(passed),
            "failed": len(failed),
            "failures": failed,
        }, f, ensure_ascii=False, indent=2)

    conn.commit()
    conn.close()

    print(f"[audit] total_yellow={len(rows)} passed={len(passed)} failed={len(failed)}")
    if failed:
        print(f"[audit] failure log: {log_path}")
        # 打印 issue 统计
        from collections import Counter
        c = Counter()
        for f in failed:
            for iss in f["issues"]:
                c[iss.split("_")[0]] += 1
        print("[audit] issue types:", dict(c))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: audit_yellow_green.py <db>")
        raise SystemExit(2)
    raise SystemExit(audit(sys.argv[1]))
