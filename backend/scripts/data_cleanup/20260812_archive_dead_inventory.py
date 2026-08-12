"""P0 · 死题归档（2026-08-12 优化轮）。

按用户「有问题的题都剔除」原则，把全部 ``active`` 且**真破损**的题做可逆归档
（``status='archived'``，备份表 + CSV 留痕，随时可恢复）。判定**严格复用**
``app.services.judge_engine`` 的解析函数（单点真相，不自己写 SQL 口径）：

``死题 ⟺ active 且满足任一：``
  1. ``type_ IS NULL``                 —— 丢了题型标签，完全抽不到、无法归类
  2. ``答案缺失``（answers 无行或 correct_answer 空）  —— 永远判不了分
  3. ``选择题 correct_answer 抽不出合法键位``（extract_choice_key 返回 None）
  4. ``判断题 correct_answer 无法归一成布尔``（normalize_judge_value 返回 None）

**不误杀**：有题型 + 有答案 的 填空/计算/应用/图形 走 AI 判分路径，不算破损，
一律保留（本脚本不碰它们）。

处置流程：
1. ORM 加载全部 active 题（options 经 JSONType 自动反序列化，避免裸 SQL 拿字符串误判）；
2. 逐题用 judge_engine 判定，归类到上述 4 类之一（每题只取首个命中原因）；
3. ``--expect N`` 量级熔断：实际命中与期望偏差 > 5% 即 abort（exit 2）；
4. 先拷入备份表 ``questions_removed_backup_20260812_opt``（NOT EXISTS 去重），
   再 ``UPDATE questions SET status='archived'``；
5. 导出 ``docs/dead-inventory-removed-20260812.csv``。

⚠️ 零迁移红线：备份表用 ``CREATE TABLE IF NOT EXISTS``，不改 questions 结构、不进 migrations。

用法::

    cd backend && .venv/Scripts/python.exe scripts/data_cleanup/20260812_archive_dead_inventory.py --dry-run --expect 362
    cd backend && .venv/Scripts/python.exe scripts/data_cleanup/20260812_archive_dead_inventory.py --expect 362
    cd backend && .venv/Scripts/python.exe scripts/data_cleanup/20260812_archive_dead_inventory.py --expect 362 --limit 50

退出码：成功 0；量级熔断 2；错误 1。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session as _ORM_Session  # noqa: E402
from sqlalchemy.orm import joinedload  # noqa: E402

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models.content import (  # noqa: E402
    QUESTION_STATUS_ACTIVE,
    QUESTION_STATUS_ARCHIVED,
    Question,
)
from app.services import judge_engine  # noqa: E402
from app.services.importers.quality_gate import _coerce_options  # noqa: E402
from scripts.data_cleanup._common import (  # noqa: E402
    append_log,
    build_log_block,
    get_engine,
)
from scripts.data_cleanup._residual_common import (  # noqa: E402
    utc_isoformat,
)

#: 本期备份表（与 8-12 早些时候的 residual 备份表区分，互不污染）。
REMOVED_BACKUP_TABLE: str = "questions_removed_backup_20260812_opt"
#: 本期 CSV（落 docs/）。
_PROJECT_ROOT = _BACKEND_ROOT.parent
_REMOVED_CSV = _PROJECT_ROOT / "docs" / "dead-inventory-removed-20260812.csv"

#: 量级熔断阈值：实际命中与 --expect 偏差 > 5% 即 abort。
EXPECT_TOLERANCE: float = 0.05

#: 死题原因常量（写入备份表 reason 列）。
REASON_NO_TYPE: str = "dead_no_type"
REASON_NO_ANSWER: str = "dead_no_answer"
REASON_BROKEN_ANSWER: str = "dead_broken_answer"


def classify_dead(q: Question) -> Optional[str]:
    """判定单道 active 题是否为死题，返回原因常量；非死题返回 None。

    每题只取**首个**命中原因（优先级：题型缺失 > 答案缺失 > 答案无法解析），
    保证归档留痕语义清晰、不与其它清理任务重复计数。

    Args:
        q: ORM ``Question``（options 已反序列化）。

    Returns:
        原因常量（REASON_*）或 None（健康，保留）。
    """
    if q.type_ is None or not str(q.type_).strip():
        return REASON_NO_TYPE

    ans = q.answer
    ca = ans.correct_answer if ans else None
    if ca is None or not str(ca).strip():
        return REASON_NO_ANSWER

    t = str(q.type_).strip()
    if t == "选择":
        keys = judge_engine.option_keys(_coerce_options(q.options))
        if judge_engine.extract_choice_key(str(ca), allowed=tuple(keys) if keys else None) is None:
            return REASON_BROKEN_ANSWER
    elif t == "判断":
        if judge_engine.normalize_judge_value(str(ca)) is None:
            return REASON_BROKEN_ANSWER
    return None


def create_backup_table(conn) -> None:
    conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {REMOVED_BACKUP_TABLE} (
                id          TEXT NOT NULL,
                old_status  TEXT NOT NULL,
                old_kp_id   TEXT,
                removed_at  TEXT NOT NULL,
                reason      TEXT NOT NULL
            )
            """
        )
    )


def ensure_removed_backup(conn, rows: List[Dict[str, Any]]) -> int:
    inserted = 0
    for row in rows:
        qid = row["id"]
        exists = conn.execute(
            text(
                f"SELECT 1 FROM {REMOVED_BACKUP_TABLE} "
                f"WHERE id = :id AND reason = :reason"
            ),
            {"id": qid, "reason": row["reason"]},
        ).first()
        if exists is not None:
            continue
        conn.execute(
            text(
                f"INSERT INTO {REMOVED_BACKUP_TABLE} "
                f"(id, old_status, old_kp_id, removed_at, reason) "
                f"VALUES (:id, :old_status, :old_kp_id, :removed_at, :reason)"
            ),
            {
                "id": qid,
                "old_status": row.get("old_status"),
                "old_kp_id": row.get("old_kp_id"),
                "removed_at": row.get("removed_at"),
                "reason": row.get("reason"),
            },
        )
        inserted += 1
    return inserted


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="死题批量归档（有问题的题都剔除）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    parser.add_argument(
        "--expect", type=int, default=None,
        help="期望命中数；实际命中与期望偏差 > 5%% 即 abort（exit 2）",
    )
    parser.add_argument("--limit", type=int, default=None, help="限制实际归档数量（小批量试跑）")
    parser.add_argument("--out-dir", default=None, help="CSV 输出目录（默认 docs/，测试可指向临时目录）")
    args = parser.parse_args(argv)

    engine = get_engine()
    stamp = utc_isoformat(datetime.now(timezone.utc))

    removed_csv = (
        Path(args.out_dir) / "dead-inventory-removed-20260812.csv"
        if args.out_dir
        else _REMOVED_CSV
    )

    # 1. ORM 加载全部 active 题（options 已反序列化；预加载 answer 避免 detached）
    db = _ORM_Session(engine)
    try:
        active = (
            db.query(Question)
            .options(joinedload(Question.answer))
            .filter(Question.status == QUESTION_STATUS_ACTIVE)
            .all()
        )
    finally:
        db.close()

    # 2. 逐题判定
    to_archive: List[Dict[str, Any]] = []
    by_reason: Dict[str, int] = {}
    for q in active:
        reason = classify_dead(q)
        if reason is None:
            continue
        by_reason[reason] = by_reason.get(reason, 0) + 1
        to_archive.append(
            {
                "id": str(q.id),
                "old_status": QUESTION_STATUS_ACTIVE,
                "old_kp_id": str(q.kp_id) if q.kp_id else None,
                "removed_at": stamp,
                "reason": reason,
            }
        )

    scanned = len(active)
    matched = len(to_archive)

    # 3. 量级熔断
    if args.expect is not None:
        expected = args.expect
        if expected <= 0:
            print(f"[熔断] --expect 必须为正数，收到 {expected}")
            return 2
        deviation = abs(matched - expected) / expected
        if deviation > EXPECT_TOLERANCE:
            print(
                f"[熔断] 实际命中 {matched} 与期望 {expected} 偏差 "
                f"{deviation:.1%} > {EXPECT_TOLERANCE:.0%}，中止执行（exit 2）"
            )
            print(f"  命中原因分布：{by_reason}")
            print(f"  命中明细（前 20）：{[r['id'] for r in to_archive[:20]]}")
            return 2

    # 4. 限制数量
    if args.limit is not None and args.limit >= 0:
        to_archive = to_archive[: args.limit]
        matched = len(to_archive)

    # 5. 执行：先备份，再归档
    if to_archive and not args.dry_run:
        with engine.begin() as conn:
            create_backup_table(conn)
            ensure_removed_backup(conn, to_archive)
            placeholders = ", ".join(f":id{i}" for i in range(len(to_archive)))
            params = {f"id{i}": r["id"] for i, r in enumerate(to_archive)}
            params["archived"] = QUESTION_STATUS_ARCHIVED
            conn.execute(
                text(
                    f"UPDATE questions SET status = :archived "
                    f"WHERE id IN ({placeholders})"
                ),
                params,
            )

    # 6. 出口 CSV
    if to_archive and not args.dry_run:
        removed_csv.parent.mkdir(parents=True, exist_ok=True)
        import csv

        with removed_csv.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["question_id", "old_status", "old_kp_id", "reason", "removed_at"])
            for row in to_archive:
                writer.writerow(
                    [row["id"], row["old_status"], row.get("old_kp_id") or "", row["reason"], row["removed_at"]]
                )

    # 7. 日志
    log_block = build_log_block(
        "死题批量归档（有问题的题都剔除）",
        [
            ("脚本", "scripts/data_cleanup/20260812_archive_dead_inventory.py"),
            ("时间", stamp),
            ("模式", "dry-run" if args.dry_run else "实际执行"),
            ("扫描", f"active 题 {scanned} 道"),
            ("命中归档", f"{matched} 道"),
            ("原因分布", str(by_reason)),
            ("期望核对", f"--expect {args.expect}" if args.expect is not None else "未设（跳过熔断）"),
            ("备份表", REMOVED_BACKUP_TABLE),
            ("CSV", "（未写出）" if args.dry_run else str(removed_csv)),
        ],
    )
    append_log(log_block)

    print(
        f"[{'dry-run' if args.dry_run else '归档'}] 死题清理完成："
        f"扫描 {scanned} 道 active，命中归档 {matched} 道，原因 {by_reason}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
