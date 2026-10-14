"""一次性凭据收敛脚本：把散落在各 .py 中的 SSH 明文密码替换为环境变量读取。

用法：python scripts/_sanitize_credentials.py
约定（AGENTS.md）：密钥、token、密码不进入代码或文档。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PWD_PAT = re.compile(r'PWD\s*=\s*"[^"]*"')
REPL = 'PWD = os.environ.get("COACH_SSH_PASSWORD", "")'

SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv"}


def main() -> int:
    changed: list[str] = []
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            src = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not PWD_PAT.search(src):
            continue
        new = PWD_PAT.sub(REPL, src)
        if not re.search(r"^import os\b|^import os$", new, flags=re.M):
            new = "import os\n" + new
        path.write_text(new, encoding="utf-8")
        changed.append(str(path.relative_to(ROOT)))

    print(f"已处理文件数：{len(changed)}")
    for item in changed:
        print("  -", item)
    print("\n运行前请设置：set COACH_SSH_PASSWORD=<服务器密码>  (Windows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
