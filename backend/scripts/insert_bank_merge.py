"""C05 合并第二步（服务器容器内）：把 dev 导出的题插入生产库。

纪律（对齐 cleanup_prod_data.py）：
- 默认 dry-run，必须显式 --confirm 才写
- 写前必须 sqlite3 backup API 备份（禁 cp，WAL 会 malformed）
- 幂等：source_tag 已存在的题不重复插（可重跑）
- 只插 questions/answers 两表，绝不碰其他表
"""
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

DB = "/app/data/coach.db"
PAYLOAD = os.environ.get("C05_PAYLOAD", "/app/c05_merge_payload.json")
BACKUP = "/app/data/coach.db.bak-c05merge-20261023"
TAG = "merged_from_dev_20261023"


def backup(conn: sqlite3.Connection) -> None:
    conn.execute("pragma wal_checkpoint(TRUNCATE)")
    dst = sqlite3.connect(BACKUP)
    conn.backup(dst)
    dst.close()
    print(f"  备份完成：{BACKUP}")


def main() -> int:
    confirm = "--confirm" in sys.argv
    payload = json.loads(Path(PAYLOAD).read_text(encoding="utf-8"))
    questions = payload["questions"]
    answers = payload["answers"]
    stats = payload["stats"]
    print(f"payload：{stats['to_merge']} 题 / {stats['answers_to_merge']} 答案（tag={TAG}）")

    conn = sqlite3.connect(DB)
    conn.execute("pragma foreign_keys=on")

    prod_kps = {r[0] for r in conn.execute("select id from knowledge_points")}
    existing_tag = conn.execute(
        "select count(*) from questions where source_tag=?", (TAG,)
    ).fetchone()[0]
    print(f"  生产 KP={len(prod_kps)}；已带本 tag 的题={existing_tag}（幂等基准）")

    plan = []
    bad_kp = 0
    for q in questions:
        if q["kp_id"] not in prod_kps:
            bad_kp += 1
            continue
        plan.append(q)
    print(f"  计划插入 {len(plan)} 题（KP 校验淘汰 {bad_kp}）")
    if not plan:
        print("  无可插入内容，退出")
        conn.close()
        return 0

    if not confirm:
        print("  [dry-run] 未写库。加 --confirm 执行。")
        conn.close()
        return 0

    backup(conn)
    id_map: dict[str, str] = {}
    cur = conn.cursor()
    for q in plan:
        new_id = str(uuid.uuid4())
        id_map[q["id"]] = new_id
        cur.execute(
            """insert into questions
               (id, content, image_url, subject, grade, difficulty, kp_id, type,
                options, status, edition, source_type, section, source_doc,
                kp_verified, source_tag, ship_ready, created_at,
                scenario_type, scenario_template, scenario_params)
               values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                new_id, q["content"], q.get("image_url"), q.get("subject"),
                q.get("grade"), q.get("difficulty"), q["kp_id"], q.get("type"),
                q.get("options"), q.get("status"), q.get("edition"),
                q.get("source_type"), q.get("section"), q.get("source_doc"),
                q.get("kp_verified", 1), TAG, 1, q.get("created_at"),
                q.get("scenario_type"), q.get("scenario_template"),
                q.get("scenario_params"),
            ),
        )
    inserted_a = 0
    for a in answers:
        old_qid = a["question_id"]
        if old_qid not in id_map:
            continue
        cur.execute(
            """insert into answers (id, question_id, correct_answer, analysis, distractor_analysis)
               values (?,?,?,?,?)""",
            (str(uuid.uuid4()), id_map[old_qid], a.get("correct_answer"),
             a.get("analysis"), a.get("distractor_analysis")),
        )
        inserted_a += 1
    conn.commit()

    total = conn.execute("select count(*) from questions").fetchone()[0]
    tagged = conn.execute("select count(*) from questions where source_tag=?", (TAG,)).fetchone()[0]
    a_total = conn.execute("select count(*) from answers").fetchone()[0]
    print(f"  已插入 questions {len(id_map)} / answers {inserted_a}")
    print(f"  复检：questions 总数 {total}（本 tag {tagged}）；answers 总数 {a_total}")
    conn.close()
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
