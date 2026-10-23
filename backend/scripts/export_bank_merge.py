"""C05 合并第一步（本地）：从 dev.db 导出「KP 属生产体系 + 内容不在生产」的题。

连同 answers 一起导出（question_id 重映射成新 UUID 在插入端做）。
导出的 JSON 是唯一合并数据源——可审计、可重放、回滚只需按 source_tag 删。
"""
import json
import sqlite3
import sys
import uuid
from pathlib import Path

import os

DEV = Path(os.environ.get("C05_DEV_DB", "F:/错题教练/_wt/integrate/backend/dev.db"))
PROD_FP = Path(os.environ.get("C05_PROD_FP", "F:/错题教练/.qa_cache/prod_fp.json"))
OUT = Path(os.environ.get("C05_PAYLOAD_OUT", "F:/错题教练/.qa_cache/c05_merge_payload.json"))

MERGE_TAG = "merged_from_dev_20261023"


def main() -> int:
    prod = json.loads(PROD_FP.read_text(encoding="utf-8"))
    prod_kps = set(prod["kps"])
    prod_prints = set(prod["prints"])

    dev = sqlite3.connect(f"file:{DEV}?mode=ro", uri=True)
    qcols = [r[1] for r in dev.execute("pragma table_info(questions)")]
    rows = dev.execute(
        "select %s from questions where kp_id is not null" % ",".join(qcols)
    ).fetchall()
    questions = [dict(zip(qcols, r)) for r in rows]

    merge, skipped_kp, skipped_dup = [], 0, 0
    for q in questions:
        if q["kp_id"] not in prod_kps:
            skipped_kp += 1
            continue
        if (q["content"] or "")[:100] in prod_prints:
            skipped_dup += 1
            continue
        merge.append(q)

    # answers 跟随
    merge_ids = {q["id"] for q in merge}
    acols = [r[1] for r in dev.execute("pragma table_info(answers)")]
    arows = dev.execute("select %s from answers" % ",".join(acols)).fetchall()
    answers = [dict(zip(acols, r)) for r in arows if r[acols.index("question_id")] in merge_ids]

    payload = {
        "tag": MERGE_TAG,
        "questions": merge,
        "answers": answers,
        "stats": {
            "dev_total": len(questions),
            "skipped_kp_not_in_prod": skipped_kp,
            "skipped_dup": skipped_dup,
            "to_merge": len(merge),
            "answers_to_merge": len(answers),
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload["stats"], ensure_ascii=False, indent=2))
    print(f"written: {OUT} ({OUT.stat().st_size/1024:.0f} KB)")
    dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
