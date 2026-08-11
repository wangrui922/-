"""docx 题库新题上架 CLI（T05 · T4 · U2）。

新导入的题一律 ``status='archived'``，不进推荐池。人工抽检通过后再批量上架。

用法::

    python backend/scripts/promote_bank.py --sample 30
        → 导出 30 道 archived 新题为 JSON 供人工抽检（import_reports/sample_30.json）

    python backend/scripts/promote_bank.py --promote --source-doc "<文件名>"
        → 把该来源文件下的 archived 题批量转 active

    python backend/scripts/promote_bank.py --promote --all
        → 把所有 archived 新题（source_doc IS NOT NULL）批量上架

等价 SQL 会随命令打印，便于在数据库客户端复核。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Answer, Question  # noqa: E402
from app.models.content import (  # noqa: E402
    QUESTION_STATUS_ACTIVE,
    QUESTION_STATUS_ARCHIVED,
)
from app.services.importers.quality_gate import (  # noqa: E402
    VERDICT_CLEAN,
    classify_question,
)


def cmd_sample(n: int) -> int:
    session: Session = SessionLocal()
    try:
        rows = (
            session.execute(
                select(Question)
                .where(Question.source_doc.is_not(None))
                .where(Question.status == QUESTION_STATUS_ARCHIVED)
                .limit(n)
            )
            .scalars()
            .all()
        )
        items = []
        for q in rows:
            ans = session.get(Answer, q.id)
            items.append(
                {
                    "id": str(q.id),
                    "grade": q.grade,
                    "edition": q.edition,
                    "source_doc": q.source_doc,
                    "section": q.section,
                    "type": q.type_,
                    "content": q.content,
                    "answer": ans.correct_answer if ans else None,
                }
            )
        out = BACKEND_DIR / "import_reports" / f"sample_{n}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[promote] 已导出 {len(items)} 道待抽检题 → {out}")
        print(
            f"[promote] 等价 SQL：SELECT * FROM questions "
            f"WHERE source_doc IS NOT NULL AND status='archived' LIMIT {n};"
        )
        return 0
    finally:
        session.close()


def cmd_promote(source_doc: str | None, promote_all: bool) -> int:
    session: Session = SessionLocal()
    try:
        stmt = select(Question).where(Question.status == QUESTION_STATUS_ARCHIVED)
        if promote_all:
            stmt = stmt.where(Question.source_doc.is_not(None))
        else:
            stmt = stmt.where(Question.source_doc == source_doc)
        rows = session.execute(stmt).scalars().all()

        # 双保险（P1）：只对质量闸门判定为 ``clean`` 的 archived 题执行上架。
        # ``review`` 题（结构性污染，待人工复核）即使卡在 archived 也绝不上架，
        # 避免上一轮「--promote --all 全量上架」把脏题放给学生刷的事故重演。
        promoted = 0
        skipped = 0
        for q in rows:
            verdict, _reasons = classify_question(q.content, None)
            if verdict == VERDICT_CLEAN:
                q.status = QUESTION_STATUS_ACTIVE
                promoted += 1
            else:
                skipped += 1
        session.commit()
        scope = f"（来源：{source_doc}）" if source_doc else "（全部新题）"
        print(f"[promote] 已上架 {promoted} 道题{scope}，跳过污染复核 {skipped} 道")
        if source_doc:
            print(
                f"[promote] 等价 SQL：UPDATE questions SET status='active' "
                f"WHERE source_doc='{source_doc}' AND status='archived'"
                f" AND <质量闸门判定=clean>;"
            )
        else:
            print(
                f"[promote] 等价 SQL：UPDATE questions SET status='active' "
                f"WHERE source_doc IS NOT NULL AND status='archived'"
                f" AND <质量闸门判定=clean>;"
            )
        return 0
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="docx 题库新题抽检/上架")
    parser.add_argument("--sample", type=int, default=None, help="导出 N 道待抽检题")
    parser.add_argument("--promote", action="store_true", help="上架模式")
    parser.add_argument("--source-doc", default=None, help="上架指定来源文件名的题")
    parser.add_argument(
        "--all", dest="promote_all", action="store_true", help="上架全部 archived 新题"
    )
    args = parser.parse_args()

    if args.sample:
        return cmd_sample(args.sample)
    if args.promote:
        if not args.source_doc and not args.promote_all:
            print("[promote] 上架需指定 --source-doc 或 --all", file=sys.stderr)
            return 1
        return cmd_promote(args.source_doc, args.promote_all)
    print(
        "[promote] 未指定动作，使用 --sample N 或 --promote --source-doc/--all",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
