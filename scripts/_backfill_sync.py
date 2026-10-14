"""T01 git 回填：把根目录工作副本（最新基线）同步进主 git 仓库工作树。

策略：覆盖式合并（不删除目标独有文件），保证两侧资产并集。
排除：node_modules / .git / dist / __pycache__ / .pytest-tmp / .qa_cache / 日志 / dev.db*
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

SRC = Path(r"F:\错题教练\cuotixiaolaoshi")
DST = Path(r"F:\错题教练\01-当前主项目\cuotixiaolaoshi")

EXCLUDE_DIRS = {"node_modules", ".git", "dist", "__pycache__", ".pytest-tmp", ".qa_cache"}
EXCLUDE_SUFFIX = {".log"}
EXCLUDE_NAMES = {"dev.db", "dev.db-shm", "dev.db-wal"}

copied = 0
skipped = 0


def should_skip(rel_parts: tuple[str, ...], name: str) -> bool:
    if any(p in EXCLUDE_DIRS for p in rel_parts):
        return True
    if name in EXCLUDE_NAMES or Path(name).suffix in EXCLUDE_SUFFIX:
        return True
    return False


def walk(src_dir: Path, dst_dir: Path, rel: tuple[str, ...] = ()) -> None:
    global copied, skipped
    for entry in src_dir.iterdir():
        rel_new = rel + (entry.name,)
        if entry.is_dir():
            if entry.name in EXCLUDE_DIRS:
                continue
            walk(entry, dst_dir / entry.name, rel_new)
            continue
        if should_skip(rel, entry.name):
            skipped += 1
            continue
        target = dst_dir / entry.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry, target)
        copied += 1


def main() -> int:
    if not SRC.is_dir() or not DST.is_dir():
        print("源或目标目录不存在")
        return 1
    walk(SRC, DST)
    print(f"已复制文件：{copied}")
    print(f"已跳过（依赖/缓存/数据库/日志）：{skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
