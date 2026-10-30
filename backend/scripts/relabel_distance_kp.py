# -*- coding: utf-8 -*-
"""distance 族 69 道非行程题「按算式二次拆分」回填脚本（B 下期，dev-only）。

背景
----
``app/engines/scenario_kp_mapping.py`` 的 ``ANNOTATION_ISSUES`` 已登记：
distance 全族 216 题由 ``t_distance`` / ``t_distance2`` 双模板按 cell 奇偶交替生成，
同一 ``scenario_template`` 下混入多种算式；其中 69 题的算式并非行程三量
（47 道长度单位换算 + 22 道两段路程相加），却被 B-0 一律挂到了 ``KP_WORD_TRAVEL``。

本脚本用 ``classify_distance_form`` 按题干算式二次归类，把这 69 道题的 ``kp_id``
从 ``KP_WORD_TRAVEL`` 重贴到正确的知识点上（长度换算 → ``KP_UNIT_LENGTH_BASIC``，
两段相加 → ``KP_CALC_ADD``；行程三量仍留 ``KP_WORD_TRAVEL``）。

安全设计（沿用 ``backfill_scenario_kp.py`` 范式）
------------------------------------------------
* **默认 dry-run**：不带 ``--confirm`` 时只打印意图，**零写入**；
* **备份**：真写前用 **sqlite3 backup API** 复制一份（禁止 cp，WAL 下直接复制会得到
  残缺库）；
* **幂等**：只处理「当前 ``kp_id`` 解析为 ``KP_WORD_TRAVEL``」的题；其余（已重贴过、
  或其它归属）一律跳过不覆盖；目标仍是 ``KP_WORD_TRAVEL`` 的题也跳过；
* **外键校验前置**：``DISTANCE_FORM_KP_MAP`` 涉及的每个 ``kp_code`` 必须在库中解析到
  一条 ``status='active'`` 的知识点，否则整批中止并以非零退出码返回；
* **只写一列**：唯一被修改的列是 ``questions.kp_id``，其余列字节级一致；
* **生产护栏**：生产库无 ``scenario_bank`` 数据；指向生产副本时必须设
  ``ALLOW_PROD_BACKFILL=1`` 才允许 ``--confirm``。

⚠️ **范围**：本脚本**只改 ``kp_id`` 一列**，不修正任何官方答案。长度换算答案 bug
（``generate_scenario_bank.py::t_distance2`` kind==3）由生成器修复保证未来正确性，
既有 dev 库里那 5 道历史错误答案需另走内容级重生成，不在本脚本范围内。

用法
----
    # 1) 预演（零写入）
    python backend/scripts/relabel_distance_kp.py backend/dev.db

    # 2) 真写（先自动备份，再改写 69 题的 kp_id）
    python backend/scripts/relabel_distance_kp.py backend/dev.db --confirm

    # 3) 产出审计报告
    python backend/scripts/relabel_distance_kp.py backend/dev.db --report out.json

退出码
------
    0  正常完成（dry-run 预演 或 写入成功）
    2  命令行用法错误
    3  外键校验失败——目标 kp_code 缺失或非 active，整批中止、零写入
    4  数据库 / IO 异常
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.engines.scenario_kp_mapping import (  # noqa: E402  (需先补 sys.path)
    DISTANCE_FORM_KP_MAP,
    classify_distance_form,
)

# ============================================================ 常量
EXIT_OK = 0
EXIT_FK_VALIDATION_FAILED = 3
EXIT_DB_ERROR = 4

#: 目标场景题来源（与 ``generate_scenario_bank.py`` 写入的一致）。
TARGET_SOURCE_DOC = "scenario_bank"

#: 仅处理 distance 场景的子模板（``scenario_template LIKE 'distance/%'``）。
DISTANCE_LIKE = "distance/%"

#: 只有「当前归属为行程问题」的 distance 题才参与二次拆分重贴。
RELABEL_SOURCE_KP_CODE = "KP_WORD_TRAVEL"

#: 本脚本涉及的全部目标知识点 code（外键校验对象）。
RELEVANT_KP_CODES: Tuple[str, ...] = tuple(
    sorted(set(DISTANCE_FORM_KP_MAP.values()))
)

DEFAULT_BATCH_SIZE = 500


# ============================================================ 数据结构
@dataclass(frozen=True)
class KnowledgePointRow:
    """知识点最小只读视图。"""

    id: str
    code: str
    name: str
    status: str


@dataclass
class TargetRow:
    """一条待重贴的题目行。"""

    id: str
    content: str
    scenario_template: str
    current_kp_code: Optional[str]
    current_kp_id: Optional[str]
    form: str
    target_kp_code: str
    target_kp_id: str
    action: str = "pending"  # write | skip_same | skip_other_kp
    reason: str = ""


@dataclass
class RelabelOutcome:
    """一次重贴（或预演）的执行结论。"""

    batch_id: str
    mode: str
    db_path: str
    backup_path: Optional[str]
    report_path: Optional[str]
    exit_code: int
    targets: List[TargetRow] = field(default_factory=list)
    planned: int = 0
    written: int = 0
    skipped_same: int = 0
    skipped_other_kp: int = 0
    errors: List[str] = field(default_factory=list)
    validation_errors: List[str] = field(default_factory=list)
    # 重贴分布（仅 write 的）：form -> 题数
    relabel_distribution: Dict[str, int] = field(default_factory=dict)


# ============================================================ 工具函数
def utcnow_iso() -> str:
    """当前 UTC 时间的 ISO8601 字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_batch_id() -> str:
    """批次号：``rel-{UTC时间}-{6位随机}``。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"rel-{stamp}-{uuid.uuid4().hex[:6]}"


def backup_database(source_path: Path, destination_path: Path) -> Path:
    """用 **sqlite3 backup API** 复制数据库（禁止 cp，WAL 下直接复制会残缺）。"""
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(source_path))
    try:
        destination = sqlite3.connect(str(destination_path))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return destination_path


# ============================================================ 数据库读取
def fetch_knowledge_points(
    connection: sqlite3.Connection,
) -> Dict[str, KnowledgePointRow]:
    """按 ``code`` 索引知识点（只读）。"""
    rows = connection.execute(
        "SELECT id, code, name, status FROM knowledge_points"
    ).fetchall()
    index: Dict[str, KnowledgePointRow] = {}
    for row in rows:
        code = (row[1] or "").strip()
        if not code:
            continue
        index[code] = KnowledgePointRow(
            id=row[0], code=code, name=row[2], status=row[3]
        )
    return index


def validate_kp_codes(
    kp_codes: Sequence[str],
    kp_index: Dict[str, KnowledgePointRow],
) -> List[str]:
    """外键校验：每个 code 必须解析到一条 **active** 的知识点。"""
    errors: List[str] = []
    for code in sorted(set(kp_codes)):
        row = kp_index.get(code)
        if row is None:
            errors.append(f"知识点不存在: code={code}")
            continue
        if row.status != "active":
            errors.append(f"知识点非 active: code={code} status={row.status}")
    return errors


def fetch_targets(connection: sqlite3.Connection) -> List[TargetRow]:
    """读取 distance 族待处理题目行（**只读**）。

    取数范围：``source_doc='scenario_bank'`` 且 ``scenario_template LIKE 'distance/%'``。
    通过 LEFT JOIN 把当前 ``kp_id`` 解析成 ``code``，便于区分「当前是否为
    KP_WORD_TRAVEL」与其它归属。
    """
    rows = connection.execute(
        """
        SELECT q.id, q.content, q.scenario_template, q.kp_id, kp.code
          FROM questions q
          LEFT JOIN knowledge_points kp ON kp.id = q.kp_id
         WHERE q.source_doc = ?
           AND q.scenario_template LIKE ?
         ORDER BY q.scenario_template, q.id
        """,
        (TARGET_SOURCE_DOC, DISTANCE_LIKE),
    ).fetchall()
    targets: List[TargetRow] = []
    for question_id, content, template, kp_id, current_code in rows:
        targets.append(
            TargetRow(
                id=question_id,
                content=content or "",
                scenario_template=(template or "").strip(),
                current_kp_code=(current_code or "").strip() or None,
                current_kp_id=kp_id,
                form="",
                target_kp_code="",
                target_kp_id="",
            )
        )
    return targets


# ============================================================ 计划与写入
def plan_targets(
    targets: Sequence[TargetRow],
    kp_index: Dict[str, KnowledgePointRow],
) -> Tuple[List[TargetRow], int, int, int]:
    """给每条目标行标出动作（不写库）。

    动作口径：

        * ``skip_other_kp`` —— 当前 ``kp_id`` 解析出的 code 不是 ``KP_WORD_TRAVEL``
          （已重贴过 / 其它归属），**不覆盖**；
        * ``skip_same``     —— 二次归类结果仍是 ``KP_WORD_TRAVEL``，本就正确，幂等跳过；
        * ``write``         —— 当前为 ``KP_WORD_TRAVEL`` 且二次归类命中长度换算 / 两段相加，
          需改写为目标 code 对应的知识点 id。

    Returns:
        ``(targets, write_count, skip_same, skip_other_kp)``。
    """
    write_count = 0
    skip_same = 0
    skip_other_kp = 0
    for target in targets:
        # 只处理「当前归属为行程问题」的题；其余不动
        if target.current_kp_code != RELABEL_SOURCE_KP_CODE:
            target.action = "skip_other_kp"
            target.reason = (
                f"当前归属为 {target.current_kp_code or 'None'}，非"
                f"{RELABEL_SOURCE_KP_CODE}，不动"
            )
            skip_other_kp += 1
            continue

        form = classify_distance_form(target.content)
        target.form = form
        target_kp_code = DISTANCE_FORM_KP_MAP[form]
        target.target_kp_code = target_kp_code
        kp_row = kp_index.get(target_kp_code)
        if kp_row is None:
            # 外键校验阶段已拦截，这里理论上不会命中；保险起见标成不写
            target.action = "skip_other_kp"
            target.reason = f"目标知识点缺失: {target_kp_code}"
            skip_other_kp += 1
            continue
        target.target_kp_id = kp_row.id

        if target_kp_code == RELABEL_SOURCE_KP_CODE:
            target.action = "skip_same"
            target.reason = "二次归类仍为行程三量，无需改动"
            skip_same += 1
        else:
            target.action = "write"
            target.reason = (
                f"当前 {RELABEL_SOURCE_KP_CODE} → 二次归类命中"
                f"{form}，重贴到 {target_kp_code}"
            )
            write_count += 1
    return list(targets), write_count, skip_same, skip_other_kp


def apply_writes(
    connection: sqlite3.Connection,
    targets: Sequence[TargetRow],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """执行重贴写入（**只写 ``questions.kp_id`` 一列**）。

    ``UPDATE ... WHERE id = ? AND kp_id = ?`` 用当前 id 作守卫，
    避免并发改动后被静默覆盖。
    """
    pending = [target for target in targets if target.action == "write"]
    written = 0
    step = max(1, int(batch_size))
    for start in range(0, len(pending), step):
        chunk = pending[start : start + step]
        with connection:  # 每批一个事务
            for target in chunk:
                cursor = connection.execute(
                    "UPDATE questions SET kp_id = ? WHERE id = ? AND kp_id = ?",
                    (target.target_kp_id, target.id, target.current_kp_id),
                )
                written += int(cursor.rowcount or 0)
    return written


# ============================================================ 报告
def build_report(outcome: RelabelOutcome) -> Dict[str, Any]:
    """组装 JSON 报告。"""
    return {
        "batch_id": outcome.batch_id,
        "generated_at": utcnow_iso(),
        "mode": outcome.mode,
        "db_path": outcome.db_path,
        "backup_path": outcome.backup_path,
        "report_path": outcome.report_path,
        "exit_code": outcome.exit_code,
        "source_doc": TARGET_SOURCE_DOC,
        "scenario_like": DISTANCE_LIKE,
        "relabel_source_kp_code": RELABEL_SOURCE_KP_CODE,
        "distance_form_kp_map": dict(DISTANCE_FORM_KP_MAP),
        "stats": {
            "target_total": len(outcome.targets),
            "to_relabel": outcome.planned,
            "written": outcome.written,
            "skipped_same": outcome.skipped_same,
            "skipped_other_kp": outcome.skipped_other_kp,
            "errors": len(outcome.errors),
            "validation_errors": len(outcome.validation_errors),
        },
        "relabel_distribution": outcome.relabel_distribution,
        "questions": [
            {
                "id": target.id,
                "scenario_template": target.scenario_template,
                "form": target.form,
                "current_kp_code": target.current_kp_code,
                "target_kp_code": target.target_kp_code,
                "action": target.action,
                "reason": target.reason,
            }
            for target in outcome.targets
        ],
        "errors": list(outcome.errors),
        "validation_errors": list(outcome.validation_errors),
    }


# ============================================================ 主流程
def run_relabel(
    db_path: Path,
    confirm: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    batch_id: Optional[str] = None,
    backup_dir: Optional[Path] = None,
) -> RelabelOutcome:
    """执行一次 distance 二次拆分重贴（或 dry-run 预演）。

    Args:
        db_path: 目标 SQLite 文件路径。
        confirm: ``True`` 才真正写库；``False`` 为 dry-run（零写入）。
        batch_size: 每批事务条数。
        batch_id: 批次号（不传则自动生成）。
        backup_dir: 备份落盘目录（默认与源库同目录）。

    Returns:
        ``RelabelOutcome``。
    """
    resolved_batch_id = batch_id or new_batch_id()
    outcome = RelabelOutcome(
        batch_id=resolved_batch_id,
        mode="confirm" if confirm else "dry-run",
        db_path=str(db_path),
        backup_path=None,
        report_path=None,
        exit_code=EXIT_OK,
    )

    connection = sqlite3.connect(str(db_path))
    try:
        connection.row_factory = sqlite3.Row
        kp_index = fetch_knowledge_points(connection)

        validation_errors = validate_kp_codes(RELEVANT_KP_CODES, kp_index)
        if validation_errors:
            outcome.validation_errors.extend(validation_errors)
            outcome.exit_code = EXIT_FK_VALIDATION_FAILED
            return outcome

        targets = fetch_targets(connection)
        targets, write_count, skip_same, skip_other_kp = plan_targets(
            targets, kp_index
        )
        outcome.targets = targets
        outcome.planned = write_count
        outcome.skipped_same = skip_same
        outcome.skipped_other_kp = skip_other_kp

        if confirm:
            backup_target = (
                (backup_dir or db_path.parent)
                / f"{db_path.name}.{resolved_batch_id}.relabel-distance.bak"
            )
            backup_database(db_path, backup_target)
            outcome.backup_path = str(backup_target)
            outcome.written = apply_writes(
                connection, targets, batch_size=batch_size
            )
        else:
            outcome.written = 0

        # 统计「待重贴」的算式分布（仅 write 行），供报告与测试断言
        dist: Dict[str, int] = {}
        for target in targets:
            if target.action == "write":
                dist[target.form] = dist.get(target.form, 0) + 1
        outcome.relabel_distribution = dist
    finally:
        connection.close()

    return outcome


def write_report(report: Dict[str, Any], report_path: Path) -> Path:
    """把报告写成 JSON 文件。"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report_path


def print_summary(outcome: RelabelOutcome) -> None:
    """打印人读摘要（dry-run 也打印「打算做什么」）。"""
    print("=" * 72)
    print(f"[distance 二次拆分重贴] 批次 {outcome.batch_id}  模式={outcome.mode}")
    print(f"  数据库      : {outcome.db_path}")
    if outcome.backup_path:
        print(f"  备份        : {outcome.backup_path}")
    elif outcome.validation_errors:
        print("  备份        : (外键校验失败，整批中止，未备份未写入)")
    else:
        print("  备份        : (dry-run 不备份)")
    print(f"  distance 待处理 : {len(outcome.targets)} 条")
    print(f"  待重贴      : {outcome.planned} 条")
    print(f"  实际写入    : {outcome.written} 条")
    print(f"  幂等跳过(仍为行程): {outcome.skipped_same} 条")
    print(f"  其它归属跳过: {outcome.skipped_other_kp} 条")
    print(f"  重贴分布    : {outcome.relabel_distribution}")
    if outcome.validation_errors:
        print("  ❌ 外键校验失败（整批中止，零写入）：")
        for message in outcome.validation_errors:
            print(f"     - {message}")
    if outcome.errors:
        print("  ❌ 错误：")
        for message in outcome.errors:
            print(f"     - {message}")
    if outcome.mode == "dry-run" and not outcome.validation_errors:
        print("  ℹ️ dry-run：未写入任何数据。确认真实重贴请加 --confirm。")
    print("=" * 72)


def build_arg_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="distance 族 69 题按算式二次拆分重贴（dev-only，默认 dry-run）",
    )
    parser.add_argument(
        "db_path",
        nargs="?",
        default=str(BACKEND_DIR / "dev.db"),
        help="目标 SQLite 文件路径（默认 backend/dev.db）",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="真正写库（默认 dry-run，零写入）",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"每批事务条数（默认 {DEFAULT_BATCH_SIZE}）",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="JSON 报告落盘路径（不传则只打印摘要）",
    )
    parser.add_argument(
        "--backup-dir",
        default=None,
        help="备份文件落盘目录（默认与源库同目录）",
    )
    parser.add_argument(
        "--batch-id",
        default=None,
        help="指定批次号（默认自动生成）",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """脚本入口。

    Returns:
        进程退出码。
    """
    parser = build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    db_path = Path(args.db_path).expanduser()
    if not db_path.exists():
        print(f"❌ 数据库不存在: {db_path}", file=sys.stderr)
        return EXIT_DB_ERROR

    # 生产护栏：指向生产库时必须显式二次确认
    if args.confirm and (
        "prod" in str(db_path).lower()
        or os.environ.get("ALLOW_PROD_BACKFILL") != "1"
        and _looks_like_prod(db_path)
    ):
        if not os.environ.get("ALLOW_PROD_BACKFILL"):
            print(
                "❌ 疑似生产库；如确认是安全的生产副本，"
                "请设置环境变量 ALLOW_PROD_BACKFILL=1 后重试。",
                file=sys.stderr,
            )
            return EXIT_DB_ERROR

    try:
        outcome = run_relabel(
            db_path=db_path,
            confirm=bool(args.confirm),
            batch_size=int(args.batch_size),
            batch_id=args.batch_id,
            backup_dir=Path(args.backup_dir).expanduser() if args.backup_dir else None,
        )
    except sqlite3.Error as exc:  # pragma: no cover - 依赖真实 IO 故障
        print(f"❌ 数据库错误: {exc}", file=sys.stderr)
        return EXIT_DB_ERROR

    print_summary(outcome)

    if args.report:
        outcome.report_path = str(Path(args.report).expanduser())
        report_path = write_report(build_report(outcome), Path(outcome.report_path))
        print(f"  报告已写入: {report_path}")

    if outcome.validation_errors:
        return EXIT_FK_VALIDATION_FAILED
    return outcome.exit_code


def _looks_like_prod(db_path: Path) -> bool:
    """轻量判断：库文件名含 'prod' 或路径里出现 'prod' 字样。

    仅作护栏信号；真正的开关仍是 ``ALLOW_PROD_BACKFILL=1``。
    """
    return "prod" in str(db_path).lower()


if __name__ == "__main__":
    raise SystemExit(main())
