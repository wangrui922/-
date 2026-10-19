"""从「冷母版」重建本地 ``backend/dev.db``（回归测试可复现性的地基）。

背景（为什么要有这个脚本）
--------------------------
``backend/dev.db`` 是 gitignored 的开发题库，但**跑 pytest 会往里写数据**（题库表
本身会被改，不只是那个会被 append 的运维日志）。于是同一个 commit 上，不同人、
不同轮次跑出来的失败数互相打架（实测见过 5 / 6 / 16 三套数字），大家没法拿
"passed 数"当验收基准。

解法是三层，缺一不可：

1. **母版冷**：``_master_dev.db`` 自身一次都没被 pytest 跑过；
2. **每次都重建**：跑测前用本脚本从母版重建一份快照，**跑完即弃**；
3. **母版永不参与运行**：拿母版直接跑 = 母版被污染 = 之后所有快照都是热的
   （这是最难发现的一种假阴性：两次运行照样"一致"，但比的是一个已偏移的基线）。

为什么**不能**用 ``cp`` / ``shutil.copyfile``
-------------------------------------------
源库带 **WAL**（``app/db.py`` 里的 ``_set_sqlite_pragma`` 会开 ``journal_mode=WAL``），
数据分散在 ``dev.db`` + ``dev.db-wal`` + ``dev.db-shm`` 三个文件里。只拷主库文件会
拿到一个缺页/半提交状态的副本，SQLite 打开时报::

    sqlite3.DatabaseError: database disk image is malformed

``shutil.copyfile`` 之类的字节级复制同样不行——它绕过了 SQLite 的事务协议，不保证
拿到的是一个一致的事务快照。
唯一可靠的做法是用 **sqlite3 的 online backup API**（``Connection.backup()``）：它在
**事务一致点**上逐页拷贝，天然处理 WAL 与并发写入，出来的库一定能开。

正因为这个坑，本脚本对 cp 一律不做兜底——宁可报错，也不给你一份"看起来能用、
其实是坏的"的库。

用法::

    cd backend

    # 1) 重建本地 dev.db（默认写 backend/dev.db）
    python scripts/reset_dev_db.py

    # 2) 只体检，不写任何东西（CI 里建议跑在测试之前）
    python scripts/reset_dev_db.py --check

    # 3) CI 前置：库不在或被跑脏就自动重建，否则什么都不做（推荐放在测试之前）
    python scripts/reset_dev_db.py --ensure

    # 4) 指定母版 / 目标（例如给另一个 worktree 起快照）
    python scripts/reset_dev_db.py --master F:/错题教练/_wt/_master_dev.db \\
                                   --target ../_wt/other/backend/dev.db

    # 5) 期望题目数可按需覆盖（比如换了新母版）
    python scripts/reset_dev_db.py --expect-questions 4166

退出码：
    0 成功（含 --check 通过）；1 母版/目标异常；2 校验未通过（questions 数不符等）。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

#: 脚本位于 ``<worktree>/backend/scripts/``
_BACKEND_DIR: Path = Path(__file__).resolve().parents[1]

#: worktree 根（``Git worktree add`` 出来的每个文件夹都是一个根）
_WORKTREE_ROOT: Path = _BACKEND_DIR.parent

#: 母版默认位置。worktree 全部挂在 ``<repo>/_wt/<name>/`` 下，所以再上一级就是
#: ``_wt/`` 本身——放在这里可以保证**任何 worktree 里的测试都够不着它**，
#: 母版因此永远是"冷"的。可用 ``--master`` 或环境变量 ``MASTER_DEV_DB`` 覆盖。
_DEFAULT_MASTER_CANDIDATES: Tuple[Path, ...] = (
    _WORKTREE_ROOT.parent / "_master_dev.db",  # <repo>/_wt/_master_dev.db
    _WORKTREE_ROOT / "_master_dev.db",         # 兜底：直接放 worktree 根
)

#: 目标默认就是本 worktree 的开发库
DEFAULT_TARGET: Path = _BACKEND_DIR / "dev.db"

#: 母版应有的题目数（团队约定的干净母版口径）
#: 2026-10-16：A 类残缺题 292 条物理删除后由 4458 调整为 4166（全部 active）。
#: 删除前已建备份表 questions_a_class_backup_20261016 /
#: answers_a_class_backup_20261016（母版与各 dev.db 快照内均保留）。
EXPECTED_QUESTIONS: int = 4166

#: 母版应有的表数
EXPECTED_TABLES: int = 38


def resolve_master(explicit: Optional[str] = None) -> Path:
    """按「命令行 > 环境变量 > 候选路径」确定母版位置。

    Args:
        explicit: ``--master`` 传入的路径，为空则退到环境变量/候选路径。

    Returns:
        第一个命中的母版绝对路径；都没命中时返回优先级最高的那个（后续报错用）。
    """
    if explicit:
        return Path(explicit).expanduser().resolve()

    from_env = os.environ.get("MASTER_DEV_DB")
    if from_env:
        return Path(from_env).expanduser().resolve()

    for candidate in _DEFAULT_MASTER_CANDIDATES:
        if candidate.is_file():
            return candidate.resolve()
    return _DEFAULT_MASTER_CANDIDATES[0].resolve()


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """以**只读**方式打开 SQLite。

    只读 URI 保证本脚本**绝不修改母版**——哪怕调用方写错了方向。

    Args:
        db_path: 数据库文件绝对路径。

    Returns:
        只读连接。

    Raises:
        sqlite3.DatabaseError: 库已损坏（典型：用 cp 拷出来的半成品）。
    """
    uri = f"file:{db_path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def verify_db(db_path: Path, label: str) -> Dict[str, int]:
    """对库做一次最小体检。

    Args:
        db_path: 待检数据库绝对路径。
        label: 打印用的人类可读名称。

    Returns:
        ``{"questions": n, "tables": n}``。

    Raises:
        RuntimeError: ``integrity_check`` 未通过（库坏了，必须重建）。
    """
    conn = open_readonly(db_path)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if not result or str(result[0]).lower() != "ok":
            raise RuntimeError(
                f"{label} 完整性校验失败（integrity_check={result!r}）：{db_path}\n"
                f"  → 这份库多半是用 cp 从带 WAL 的源库拷出来的半成品。"
                f"删掉它，用 python scripts/reset_dev_db.py 重新从母版 backup。"
            )
        questions = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
    finally:
        conn.close()
    print(f"  {label}: questions={questions} tables={tables} integrity=ok")
    return {"questions": int(questions), "tables": int(tables)}


def content_fingerprint(db_path: Path) -> str:
    """算一个**与页级布局无关**的内容指纹。

    为什么要这个：``sha256(整个文件)`` 会因为 VACUUM / WAL 检查点 / 空闲页复用而变，
    哪怕一行数据都没改（实测同一份题的三个副本文件大小相同但 sha256 各不相同）。
    按「表行数 + 关键表有序行内容」算摘要，才能真正回答"这两份库是不是同一份数据"。

    列结构也进指纹（024 事故加固）：母版执行加列迁移后，dev.db 若漏同步，
    旧版指纹（表名+行数+内容）完全一致，会误报「就绪」不重建——schema guard
    随后才红，CI 前置检查形同虚设。纳入 ``PRAGMA table_info`` 的列名/类型/
    非空/默认值/主键序五元组后，列漂移直接导致指纹不符、触发重建。

    Args:
        db_path: 数据库绝对路径。

    Returns:
        16 进制摘要字符串（前 16 位便于人眼比对，内部用完整摘要）。
    """
    conn = open_readonly(db_path)
    digest = hashlib.sha256()
    try:
        tables: List[str] = sorted(
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        )
        for table in tables:
            digest.update(f"{table}={conn.execute(f'SELECT COUNT(*) FROM \"{table}\"').fetchone()[0]};".encode())
            # 列结构五元组（cid 序号不含——它只反映历史加列顺序，不影响语义）
            columns = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            for col in columns:
                digest.update(f"|{col[1]},{col[2]},{col[3]},{col[4]},{col[5]}".encode())
        for row in conn.execute(
            "SELECT id, content, type, options, status, kp_verified FROM questions ORDER BY id"
        ):
            digest.update(repr(row).encode("utf-8"))
        for row in conn.execute(
            "SELECT question_id, correct_answer FROM answers ORDER BY question_id"
        ):
            digest.update(repr(row).encode("utf-8"))
    finally:
        conn.close()
    return digest.hexdigest()


def reset_from_master(master: Path, target: Path, expect_questions: int) -> int:
    """用 sqlite3 backup API 从母版重建目标库。

    Args:
        master: 冷母版路径（只读）。
        target: 目标库路径（会被覆盖）。
        expect_questions: 期望的题目数，不符即报错。

    Returns:
        进程退出码：0 成功，非 0 失败。
    """
    if not master.is_file():
        print(f"[错误] 母版不存在：{master}")
        print("       → 用 --master 指定正确路径，或设环境变量 MASTER_DEV_DB。")
        return 1

    print(f"[1/4] 体检母版（只读，绝不修改）：{master}")
    try:
        master_stat = verify_db(master, "冷母版")
    except sqlite3.DatabaseError as exc:
        print(f"[错误] 母版打不开/已损坏：{exc}")
        return 1
    except RuntimeError as exc:
        print(f"[错误] {exc}")
        return 1
    if master_stat["questions"] != expect_questions:
        print(
            f"[错误] 母版题目数应为 {expect_questions}，实际 {master_stat['questions']}。"
            f"母版可能已被跑脏，请换一份干净母版。"
        )
        return 1

    # 目标库的伴随文件（WAL / SHM）必须一起清掉，否则新库会被旧 WAL 回写污染
    target.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        stale = Path(str(target) + suffix)
        if stale.exists():
            stale.unlink()
            print(f"[2/4] 清除旧目标/伴随文件：{stale.name}")

    print(f"[3/4] backup API 重建：{master.name} → {target}")
    src = open_readonly(master)
    dst = sqlite3.connect(str(target))
    try:
        with dst:
            src.backup(dst)  # ★ online backup：事务一致点逐页拷贝，天然处理 WAL
    except sqlite3.DatabaseError as exc:  # pragma: no cover - 母版损坏才会触发
        print(f"[错误] backup 失败：{exc}")
        return 1
    finally:
        src.close()
        dst.close()

    print(f"[4/4] 校验新库：{target}")
    try:
        new_stat = verify_db(target, "新快照")
    except RuntimeError as exc:
        print(f"[错误] {exc}")
        return 1

    if new_stat["questions"] != expect_questions:
        print(f"[错误] 新快照题目数不符：期望 {expect_questions}，实际 {new_stat['questions']}")
        return 2

    src_fp = content_fingerprint(master)
    dst_fp = content_fingerprint(target)
    if src_fp != dst_fp:
        print(
            "[错误] 新快照与母版内容指纹不一致：\n"
            f"  母版   {src_fp[:32]}\n"
            f"  新快照 {dst_fp[:32]}"
        )
        return 2

    print(f"  content fingerprint 一致: {src_fp[:16]}")
    print(f"[完成] {target} 已从冷母版重建，questions={new_stat['questions']}")
    return 0


def check_only(master: Path, target: Path, expect_questions: int) -> int:
    """只体检目标库是否与母版一致，不写任何文件（CI 前置检查用）。

    Args:
        master: 冷母版路径。
        target: 待检目标库路径。
        expect_questions: 期望的题目数。

    Returns:
        0 一致；2 不一致；1 文件缺失/损坏。
    """
    if not target.is_file():
        print(f"[错误] 目标库不存在：{target}")
        return 1
    try:
        target_stat = verify_db(target, "目标库")
    except sqlite3.DatabaseError as exc:
        print(f"[错误] 目标库打不开/已损坏：{exc}")
        return 1
    except RuntimeError as exc:
        print(f"[错误] {exc}")
        return 1

    if not master.is_file():
        print(f"[警告] 母版不存在，跳过内容指纹比对：{master}")
        return 0 if target_stat["questions"] == expect_questions else 2

    try:
        master_stat = verify_db(master, "冷母版")
    except (sqlite3.DatabaseError, RuntimeError) as exc:
        print(f"[错误] 母版异常：{exc}")
        return 1

    problems: List[str] = []
    if target_stat["questions"] != expect_questions:
        problems.append(
            f"题目数不符：期望 {expect_questions}，实际 {target_stat['questions']}"
        )
    if master_stat["questions"] != expect_questions:
        problems.append(
            f"母版题目数不符：期望 {expect_questions}，实际 {master_stat['questions']}"
            f"（母版可能已被跑脏，请换一份）"
        )
    src_fp = content_fingerprint(master)
    dst_fp = content_fingerprint(target)
    if src_fp != dst_fp:
        problems.append(
            "内容指纹不一致（dev.db 已被改写）：\n"
            f"    母版  {src_fp[:32]}\n    dev.db {dst_fp[:32]}"
        )

    if problems:
        print("[不通过] dev.db 与冷母版不一致：")
        for item in problems:
            print(f"  - {item}")
        print("  → 修：python scripts/reset_dev_db.py")
        return 2

    print(f"[通过] {target} 与冷母版一致 (fingerprint {src_fp[:16]})")
    return 0


def ensure(master: Path, target: Path, expect_questions: int) -> int:
    """CI 前置：确保目标库**存在且与母版一致**，不一致就直接重建。

    为什么需要这个模式：``backend/dev.db`` 在不在会直接改变 16 条用例的成败
    （11 条变 skip、5 条变 failed），所以 CI 不能"听天由命"——要么保证库在且干净，
    要么明确失败。``--ensure`` 把这两种都消掉：**先体检，不合格就地重建**。

    Args:
        master: 冷母版路径。
        target: 目标库路径。
        expect_questions: 期望的题目数。

    Returns:
        0 目标库已就绪；非 0 表示连重建都救不回来（母版坏了）。
    """
    code = check_only(master, target, expect_questions)
    if code == 0:
        print(f"[就绪] {target} 已是干净的冷快照，无需重建")
        return 0
    print(f"[重建] 目标库不可用或已被改写（--check 退出码 {code}），从母版重建")
    return reset_from_master(master, target, expect_questions)


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    parser = argparse.ArgumentParser(
        description="从冷母版重建 backend/dev.db（sqlite3 backup API，禁止 cp）",
    )
    parser.add_argument(
        "--master",
        default=None,
        help=f"冷母版路径（默认依次找 {_DEFAULT_MASTER_CANDIDATES[0]} 等候选，"
        f"也可用环境变量 MASTER_DEV_DB）",
    )
    parser.add_argument(
        "--target",
        default=None,
        help=f"目标库路径（默认 {DEFAULT_TARGET}）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只体检并 diff 目标库与母版，不写任何文件",
    )
    parser.add_argument(
        "--ensure",
        action="store_true",
        help="CI 前置：目标库不存在或已被改写时自动从母版重建，否则什么都不做",
    )
    parser.add_argument(
        "--expect-questions",
        type=int,
        default=EXPECTED_QUESTIONS,
        help=f"期望的题目数（默认 {EXPECTED_QUESTIONS}）",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """脚本入口。

    Args:
        argv: 命令行参数；``None`` 时读 ``sys.argv[1:]``。

    Returns:
        进程退出码。
    """
    args = build_parser().parse_args(argv)
    master = resolve_master(args.master)
    target = Path(args.target).expanduser().resolve() if args.target else DEFAULT_TARGET

    if args.ensure:
        return ensure(master, target, args.expect_questions)
    if args.check:
        return check_only(master, target, args.expect_questions)
    return reset_from_master(master, target, args.expect_questions)


if __name__ == "__main__":
    raise SystemExit(main())
