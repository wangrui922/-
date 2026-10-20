#!/usr/bin/env python
"""用 ``git archive`` 导出 develop 分支并同步到生产服务器（2026-10-20）。

为什么必须用 git archive 而不是 tar/zip 拷工作区：
    Windows 工作区因 ``core.autocrlf=true`` 存的是 CRLF。直接 tar 同步会把
    ``\\r`` 带到服务器，于是 Dockerfile 里的
    ``RUN pip install -r requirements.txt`` 变成 ``...requirements.txt\\r``，
    pip 会去 PyPI 拉一个**名叫 "requirements.txt\\r" 的假包**，反复重试至挂死。
    实测 2026-10-20：build 45 分钟不结束、load average 0.14、CPU 0.3%、
    pip 进程 CPU 时间停在 0:08。

    ``git archive`` 导出的是 **blob 内容**，遵守 .gitattributes
    （Dockerfile/requirements.txt/*.sh/*.sql/*.yml 强制 eol=lf），
    天生是 LF，从根上避免这个坑。

前置：
    * 工作区改动必须**已提交**（archive 只导 commit 里的内容）。
    * SSH 私钥：``C:/Users/Administrator/coach-keys/github_coach_deploy``。
    * 服务器目录：``/home/ubuntu/coach``。

用法：
    python deploy/sync_git_archive.py            # 只导出到本地 .qa_cache/ship
    python deploy/sync_git_archive.py --push     # 导出并同步到服务器
    python deploy/sync_git_archive.py --push --ref <commit>
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIP_DIR = REPO_ROOT / ".qa_cache" / "ship"
SSH_KEY = Path("C:/Users/Administrator/coach-keys/github_coach_deploy")
REMOTE_HOST = os.environ.get("COACH_SSH_HOST", "118.25.44.126")
REMOTE_USER = os.environ.get("COACH_SSH_USER", "ubuntu")
REMOTE_DIR = os.environ.get("COACH_SSH_DIR", "/home/ubuntu/coach")


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> str:
    """执行命令并返回 stdout。"""
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0 and check:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(f"命令失败（{proc.returncode}）：{' '.join(cmd)}")
    return proc.stdout


def export(ref: str) -> Path:
    """用 git archive 导出指定 ref 到 .qa_cache/ship/<ref>.tar.gz。"""
    SHIP_DIR.mkdir(parents=True, exist_ok=True)
    out = SHIP_DIR / f"{ref.replace('/', '_')}.tar.gz"
    with out.open("wb") as fh:
        subprocess.run(
            ["git", "archive", "--format=tar.gz", "-o", str(out), ref],
            cwd=str(REPO_ROOT),
            check=True,
        )
    print(f"[export] {ref} → {out}  ({out.stat().st_size / 1024:.0f} KB)")
    return out


def verify_lf(tar_path: Path) -> None:
    """校验归档内关键构建文件是 LF —— 防止 CRLF 混入（45 分钟挂死的根因）。"""
    must_be_lf = (
        "backend/Dockerfile",
        "backend/requirements.txt",
        "student-web/Dockerfile.production",
    )
    with tarfile.open(tar_path, "r:gz") as tf:
        names = set(tf.getnames())
        bad: list[str] = []
        for name in must_be_lf:
            if name not in names:
                bad.append(f"{name}（不在归档里）")
                continue
            member = tf.extractfile(name)
            if member is None:
                bad.append(f"{name}（无法读取）")
                continue
            data = member.read()
            if b"\r\n" in data:
                bad.append(f"{name}（含 CRLF！）")
        if bad:
            print("[verify] ⚠️ 行尾校验未通过：", file=sys.stderr)
            for b in bad:
                print(f"    - {b}", file=sys.stderr)
            raise SystemExit("归档行尾异常，中止同步（会导致 build 挂死）")
        print(f"[verify] 行尾校验通过（{len(must_be_lf)} 个构建文件均为 LF）")


def push(tar_path: Path) -> None:
    """上传归档到服务器并解包覆盖（保留服务器上的 .env 等未纳管文件）。"""
    if not SSH_KEY.exists():
        raise SystemExit(f"SSH 私钥不存在：{SSH_KEY}")
    remote_tmp = "/tmp/coach_sync.tar.gz"
    scp = [
        "scp", "-i", str(SSH_KEY), "-o", "StrictHostKeyChecking=no",
        str(tar_path), f"{REMOTE_USER}@{REMOTE_HOST}:{remote_tmp}",
    ]
    run(scp)
    print(f"[push] 上传到 {remote_tmp}")

    ssh = [
        "ssh", "-i", str(SSH_KEY), "-o", "StrictHostKeyChecking=no",
        f"{REMOTE_USER}@{REMOTE_HOST}",
    ]
    # 服务器上解包：排除 .env.production / runtime 等本地数据目录
    cmd = (
        f"set -e; "
        f"mkdir -p {REMOTE_DIR}; "
        f"cd {REMOTE_DIR}; "
        f"tar -xzf {remote_tmp} "
        f"--exclude='.env.production' "
        f"--exclude='runtime' "
        f"--exclude='.qa_cache'; "
        f"rm -f {remote_tmp}; "
        f"echo '--- 解包完成，行尾抽查 ---'; "
        f"for f in backend/Dockerfile backend/requirements.txt "
        f"student-web/Dockerfile.production; do "
        f"  if grep -qU $'\\r' \"$f\" 2>/dev/null; then echo \"CRLF: $f\"; fi; "
        f"done; "
        f"echo 'OK'"
    )
    out = run(ssh + [cmd])
    print(out)
    if "CRLF:" in out:
        raise SystemExit("服务器端检出 CRLF，中止（会导致 build 挂死）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="同步到服务器")
    ap.add_argument("--ref", default="develop", help="要导出的 ref（默认 develop）")
    args = ap.parse_args()

    tar_path = export(args.ref)
    verify_lf(tar_path)
    if args.push:
        push(tar_path)
    print("DONE")


if __name__ == "__main__":
    main()
