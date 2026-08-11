"""docx 题库离线导入 CLI（T05 · T4）。

把登记在册的 15 份 docx（青岛版衔接 5 + 苏教版期末 10）解析为结构化题目，
落到 ``questions`` + ``answers`` 两表，并输出导入汇总报告（JSON + Markdown）。

用法::

    python backend/scripts/import_docx_bank.py
    python backend/scripts/import_docx_bank.py --source-dir "D:/我的文档/Documents/数学题"
    python backend/scripts/import_docx_bank.py --only qd-g2          # 只导某个 key
    python backend/scripts/import_docx_bank.py --dry-run             # 只解析不落库
    python backend/scripts/import_docx_bank.py --output-dir ./reports

红线：
    * 严禁 ``git checkout`` / ``git merge`` / ``git rebase``（沙箱回收站保护会吞 .git）。
    * 落库走 ``loader``（uuid5 幂等 upsert，R-22/R-23 已锁死：不删行、不回退运营标记）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Tuple

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.services.importers import ensure_bank_columns  # noqa: E402
from app.services.importers import (  # noqa: E402
    loader,
    manifest,
    qingdao,
    report,
    sujiao,
)
from app.services.importers.schema import (  # noqa: E402
    LoadStats,
    PairingError,
    ParseOutcome,
    SchemaGuardError,
)


def _parse_sources(
    source_dir: Path, only_keys: set | None
) -> Tuple[List[ParseOutcome], List[Dict[str, str]]]:
    """解析全部登记源，返回 ``(解析结果列表, 整单元失败清单)``。

    苏教 A 组的「正文/答案分离」配对由 :func:`sujiao.parse_pair` 负责：

    * 闸门①（配对文件数/存在性）、闸门②（正文题数为 0）是**结构性错误**，
      仍抛 :class:`PairingError` —— 这里捕获后整卷跳过并记失败清单。
    * 闸门③（题号 overlap）不再整卷跳过：**保留全部题目**，只按精确题号挂
      匹配上的答案；overlap 偏低记 warning，overlap 极低（疑似答案文件错配）
      则整卷答案置空。

    R-02 红线未变：挂答案永远只走 ``(major_no, minor_no)`` 精确键，
    绝不降级成顺序对齐、文件名模糊匹配等 best-effort 猜配。
    """
    outcomes: List[ParseOutcome] = []
    failures: List[Dict[str, str]] = []

    # ---------------------------------------------------- 青岛版：单文件正文+答案
    for src in manifest.QINGDAO_SOURCES:
        if only_keys and src.key not in only_keys:
            continue
        path = src.path(source_dir)
        if not path.exists():
            failures.append({"source": src.label, "reason": "文件缺失"})
            continue
        outcomes.append(qingdao.parse(src, path))

    # ---------------------------------------------------- 苏教 A 组：正文/答案配对
    for (grade, paper_no), group in manifest.sujiao_a_pairs().items():
        if only_keys:
            keys = {s.key for s in group}
            if not keys & only_keys:
                continue
        bodies = [s for s in group if s.role == "body"]
        answers = [s for s in group if s.role == "answer"]
        try:
            outcomes.append(sujiao.parse_pair(bodies, answers, source_dir))
        except PairingError as exc:
            label = bodies[0].label if bodies else f"苏教A组(grade={grade},paper={paper_no})"
            failures.append({"source": label, "reason": f"配对闸门未通过：{exc}"})

    # ---------------------------------------------------- 苏教 B 组：单文件多套
    for src in manifest.SUJIAO_B_SOURCES:
        if only_keys and src.key not in only_keys:
            continue
        path = src.path(source_dir)
        if not path.exists():
            failures.append({"source": src.label, "reason": "文件缺失"})
            continue
        outcomes.append(sujiao.parse_b(src, path))

    return outcomes, failures


def _empty_stats() -> LoadStats:
    return LoadStats()


def main() -> int:
    parser = argparse.ArgumentParser(description="把登记在册的教辅 docx 解析入库")
    parser.add_argument("--source-dir", default=str(manifest.DEFAULT_SOURCE_DIR), help="docx 源目录")
    parser.add_argument("--only", action="append", default=None, help="只导指定 key（可重复）")
    parser.add_argument("--dry-run", action="store_true", help="只解析不落库")
    parser.add_argument(
        "--output-dir", default=str(BACKEND_DIR / "import_reports"), help="报告输出目录"
    )
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    only_keys = set(args.only) if args.only else None

    # 缺文件提示（不阻断，配对闸门会处理）
    miss = manifest.missing_files(source_dir)
    if miss:
        print(f"[import] ⚠️ 登记在册但磁盘缺失 {len(miss)} 个文件：")
        for f in miss:
            print(f"    - {f}")

    # V7 保险②：缺列自动补 + 存量未复核拦截
    try:
        info = ensure_bank_columns(engine)
        if info.get("added") or info.get("backfilled"):
            print(f"[import] schema_guard：补列 {info.get('added')}，回填 {info.get('backfilled')} 题")
    except SchemaGuardError as exc:
        print(f"[import] ❌ {exc}", file=sys.stderr)
        print("[import] 拒绝导入（exit 2）。请先执行迁移或手工回填存量题 kp_verified。", file=sys.stderr)
        return 2

    outcomes, failures = _parse_sources(source_dir, only_keys)

    if args.dry_run:
        rep = report.build_report(outcomes, _empty_stats(), failures=failures, dry_run=True)
        written = report.write_report(rep, Path(args.output_dir))
        print(f"[import] DRY-RUN 完成，报告：{written['markdown']}")
        print(f"[import] 解析源 {len(outcomes)} 个，整单元失败 {len(failures)} 个（未落库）")
        return 0

    session: Session = SessionLocal()
    try:
        stats = loader.load_all(session, outcomes)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    total = int(
        SessionLocal().execute(text("SELECT COUNT(*) FROM questions")).scalar() or 0
    )
    rep = report.build_report(
        outcomes, stats, failures=failures, db_counts={"totalQuestions": total}
    )
    written = report.write_report(rep, Path(args.output_dir))

    print(
        f"[import] 完成：新增 {stats.inserted} / 更新 {stats.updated} / "
        f"答案 {stats.answers} / 闸门 clean {stats.gated_clean} / review {stats.gated_review} "
        f"/ 失败 {len(failures)}"
    )
    print(f"[import] 报告：{written['markdown']}")
    print(f"[import] 下一步：promote_bank.py --sample 30 抽检，--promote --source-doc <文件名> 上架")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
