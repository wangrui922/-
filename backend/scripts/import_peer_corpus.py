#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量导入 A-2 同伴视角种子语料（CSV → peer_view_corpus）。

设计对齐「工程铁律 14：写库脚本默认 dry-run」与 peer_corpus_service 的
「生成 → 规则过滤 → archived → 人工放行 → released」流程。

行为：
  * 逐行调用 ``peer_corpus_service.ingest_sentence``：
      干净（无禁止项）→ status='archived'（待人工抽检）
      命中红线        → status='draft'（拦截留痕，release 前必须先修正重提）
  * **默认 dry-run**：只校验 + 扫描 + 计数，不落库、不提交；加 ``--confirm`` 才写。
  * 导入后由运营人工放行（draft 永远进不了检索池，0 例外）：
        python scripts/import_peer_corpus.py --list                 # 看待审
        python scripts/import_peer_corpus.py --release-all --reviewer 玥玥
        python scripts/import_peer_corpus.py --coverage             # 验收覆盖

CLI：
  --csv PATH             CSV 路径（默认脚本同目录的 peer_corpus_seed_template.csv）
  --confirm              ★ 真正落库（默认只 dry-run 打印预览）
  --batch TEXT           批次号（默认取文件名或 YYYYMMDD）；写入 seed_batch 列
  --db URL|PATH          覆盖数据库连接（sqlite 文件或 sqlite://... URL）；
                         不传则用 app.db 的默认库（容器内 /app/data/coach.db）
  --source TEXT          来源（seed/template/llm_rewrite，默认 seed）
  --list                 列出待人工抽检的语料（默认 archived）
  --release-all          放行全部 archived（含 --reviewer，必填）
  --reject-all           退回全部 draft（含 --reviewer，必填）
  --coverage             打印放行语料的覆盖验收（released≥200 / KP≥60 / 错因各≥25 / 年级各≥20）
  --reviewer TEXT        审核人（release/reject 必填，留痕）

CSV 列：kp_code,grade,scene,error_type,sentence[,source][,seed_batch]
  kp_code    必填，真实知识点 code（见 knowledge_points.code）
  grade      必填，1-6 整数
  scene      可选，取值见 peer_corpus_service.SCENES
  error_type 可选，取值见 peer_corpus_service.ERROR_TYPES
  sentence   必填，≤40 字口语，无姓名/班级/校名/LaTeX/贬义词/『你错了』/连用感叹号
  source     可选，默认 seed
  seed_batch 可选，缺则取 --batch
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent


def _resolve_db_arg(db_arg: str | None) -> None:
    """把 --db 写进 DATABASE_URL（必须在 import app.db 之前）。"""
    if not db_arg:
        return
    if db_arg.startswith("sqlite://"):
        os.environ["DATABASE_URL"] = db_arg
    else:
        os.environ["DATABASE_URL"] = "sqlite:///" + os.path.abspath(db_arg)


def _build_arg_parser() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="批量导入 A-2 同伴视角种子语料（默认 dry-run）")
    ap.add_argument("--csv", default=str(SCRIPT_DIR / "peer_corpus_seed_template.csv"))
    ap.add_argument("--confirm", action="store_true", help="真正落库（默认仅 dry-run 预览）")
    ap.add_argument("--batch", default=None, help="批次号（默认文件名或 YYYYMMDD）")
    ap.add_argument("--db", default=None, help="覆盖数据库（sqlite 文件或 URL）")
    ap.add_argument("--source", default="seed", help="来源 seed/template/llm_rewrite")
    ap.add_argument("--list", action="store_true", help="列出待审 archived 语料")
    ap.add_argument("--release-all", action="store_true", help="放行全部 archived")
    ap.add_argument("--reject-all", action="store_true", help="退回全部 draft")
    ap.add_argument("--coverage", action="store_true", help="打印放行覆盖验收")
    ap.add_argument("--reviewer", default=None, help="审核人（release/reject 必填）")
    ap.add_argument("--no-dedup", action="store_true",
                    help="关闭去重（默认按 kp_code+grade+sentence 跳过已存在行，防重复批次）")
    return ap.parse_args()


def _default_batch(csv_path: str) -> str:
    stem = Path(csv_path).stem
    if stem and stem != "peer_corpus_seed_template":
        return stem
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def main() -> int:
    args = _build_arg_parser()
    _resolve_db_arg(args.db)

    # app.db / service 依赖数据库连接与项目包路径，放到参数解析后、env 就绪再 import
    sys.path.insert(0, str(BACKEND_ROOT))
    from sqlalchemy import text  # noqa: E402
    from app.db import SessionLocal  # noqa: E402
    import app.services.peer_corpus_service as svc  # noqa: E402

    # ---- 只读子命令：不需要 CSV ----
    if args.list or args.release_all or args.reject_all or args.coverage:
        with SessionLocal() as db:
            if args.list:
                rows = svc.list_for_review(db)
                print(f"=== 待人工抽检（archived）共 {len(rows)} 条 ===")
                for r in rows:
                    print(f"  {r['id'][:8]} | {r['kp_code']} | g{r['grade']} | "
                          f"{r['scene'] or '-'} | {r['error_type'] or '-'} | {r['sentence']}")
                return 0
            if args.coverage:
                cov = svc.coverage_stats(db)
                print("=== 放行语料覆盖验收 ===")
                print(f"  released 总数       : {cov['released']}  (目标 ≥200)")
                print(f"  覆盖 KP 数          : {cov['kp_count']}  (目标 ≥60)")
                print(f"  错因分布            : {cov['error_type']}  (目标每类 ≥25)")
                print(f"  年级分布            : {cov['grade']}  (目标每年级 ≥20)")
                return 0
            if args.release_all or args.reject_all:
                if not args.reviewer:
                    print("[fatal] --release-all / --reject-all 必须带 --reviewer")
                    return 2
                statuses = ("archived",) if args.release_all else ("draft",)
                rows = svc.list_for_review(db, statuses=statuses)
                if not rows:
                    print(f"没有 status={statuses[0]} 的语料可处理")
                    return 0
                n = 0
                for r in rows:
                    ok = svc.release(db, r["id"], args.reviewer) if args.release_all \
                        else svc.reject(db, r["id"], args.reviewer)
                    n += 1 if ok else 0
                db.commit()
                verb = "放行" if args.release_all else "退回"
                print(f"[done] 已{verb} {n}/{len(rows)} 条（reviewer={args.reviewer}）")
                return 0

    # ---- 导入子命令 ----
    csv_path = args.csv
    if not Path(csv_path).exists():
        print(f"[fatal] CSV 不存在：{csv_path}")
        return 2
    batch = args.batch or _default_batch(csv_path)

    imported = archived = drafted = skipped = duplicates = 0
    skipped_rows: list[tuple[int, str]] = []
    draft_rows: list[tuple[int, str, list[str]]] = []
    dup_rows: list[tuple[int, str, int, str]] = []  # (行, kp_code, grade, sentence) 去重跳过

    with SessionLocal() as db:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            required = {"kp_code", "grade", "sentence"}
            if not required.issubset(reader.fieldnames or []):
                print(f"[fatal] CSV 缺少必要列，需要 {sorted(required)}，实际 {reader.fieldnames}")
                return 2
            for i, row in enumerate(reader, start=2):  # 第 1 行是表头
                kp_code = (row.get("kp_code") or "").strip()
                grade_raw = (row.get("grade") or "").strip()
                sentence = (row.get("sentence") or "").strip()
                scene = (row.get("scene") or "").strip() or None
                error_type = (row.get("error_type") or "").strip() or None
                source = (row.get("source") or "").strip() or args.source
                seed_batch = (row.get("seed_batch") or "").strip() or batch

                # 基础字段校验
                if not kp_code or not sentence:
                    skipped += 1
                    skipped_rows.append((i, "kp_code 或 sentence 为空"))
                    continue
                try:
                    grade = int(grade_raw)
                except ValueError:
                    skipped += 1
                    skipped_rows.append((i, f"grade 非整数：{grade_raw!r}"))
                    continue

                # 去重（默认开）：同 (kp_code, grade, sentence) 已存在则跳过，
                # 防止同一份 CSV 重复批次灌入导致语料翻倍。
                if not args.no_dedup:
                    exists = db.execute(
                        text(
                            "SELECT 1 FROM peer_view_corpus "
                            "WHERE kp_code=:kp AND grade=:g AND sentence=:s LIMIT 1"
                        ),
                        {"kp": kp_code, "g": grade, "s": sentence},
                    ).fetchone()
                    if exists:
                        duplicates += 1
                        dup_rows.append((i, kp_code, grade, sentence))
                        continue

                try:
                    res = svc.ingest_sentence(
                        db,
                        kp_code=kp_code,
                        grade=grade,
                        sentence=sentence,
                        scene=scene,
                        error_type=error_type,
                        source=source,
                        seed_batch=seed_batch,
                    )
                except ValueError as exc:  # 参数非法（scene/error_type/source 取值错）
                    skipped += 1
                    skipped_rows.append((i, str(exc)))
                    continue

                imported += 1
                if res["status"] == "archived":
                    archived += 1
                else:  # draft：命中禁止项，拦截
                    drafted += 1
                    draft_rows.append((i, sentence, list(res["violations"])))

        # ---- 提交裁决 ----
        if args.confirm:
            db.commit()
            print(f"[confirm] 已落库：archived={archived} draft(拦截)={drafted} "
                  f"skipped={skipped} batch={batch}")
        else:
            db.rollback()
            print("[dry-run] 未落库（预览如下）；加 --confirm 才真正写入")

    # ---- 汇总（无论 dry-run 都打印，便于人工核对）----
    print(f"\n=== 导入预览（共解析 {imported + skipped + duplicates} 行）===")
    print(f"  将入 archived（待人工放行）：{archived}")
    print(f"  将入 draft（命中红线拦截）  ：{drafted}")
    print(f"  去重跳过（已存在）          ：{duplicates}")
    print(f"  跳过（字段非法）            ：{skipped}")
    if draft_rows:
        print("\n--- 命中红线被拦截（draft，须修正后重提，不进检索池）---")
        for ln, sent, viol in draft_rows:
            print(f"  行{ln} [{','.join(viol)}] {sent}")
    if dup_rows:
        print("\n--- 去重跳过（已存在相同 kp_code+grade+sentence，将不写入）---")
        for ln, kp, g, sent in dup_rows:
            print(f"  行{ln} [{kp} g{g}] {sent}")
    if skipped_rows:
        print("\n--- 字段非法跳过 ---")
        for ln, reason in skipped_rows:
            print(f"  行{ln} {reason}")
    print("\n下一步：确认无误后加 --confirm 落库；再用 --list / --release-all --reviewer X 放行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
