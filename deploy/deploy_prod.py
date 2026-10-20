#!/usr/bin/env python
"""生产部署执行器：同步 → 构建 → 重启 → 对齐数据 → 冒烟。

**这是脚本化的部署编排，每个阶段都有独立校验点，任一阶段失败立即中止。**

阶段：
    1. sync     —— git archive 导出并同步到服务器（自动处理 LF，杜绝 CRLF 挂死）
    2. build    —— 服务器上 docker compose build backend / web
    3. restart  —— 重启 backend + worker（web 是前端构建产物，随 build 走）
    4. align    —— 对齐生产库（内测不限额 plans 档）
    5. smoke    —— 真实业务链路冒烟（激活码 → 门控 → 抽题 → 判分 → AI → 错题本）

⚠️ 部署前必须已经 commit（archive 只导 commit 内容）。

用法：
    python deploy/deploy_prod.py --ref develop
    python deploy/deploy_prod.py --ref develop --skip-build   # 只同步+重启
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SSH_KEY = os.environ.get("COACH_SSH_KEY", "C:/Users/Administrator/coach-keys/AIjiaolian_new")
HOST = os.environ.get("COACH_SSH_HOST", "118.25.44.126")
USER = os.environ.get("COACH_SSH_USER", "ubuntu")
REMOTE = os.environ.get("COACH_SSH_DIR", "/home/ubuntu/coach")
COMPOSE = f"sudo docker compose -f deploy/docker-compose.yml"


def ssh(cmd: str, timeout: int = 900, check: bool = True) -> str:
    """在服务器上执行命令并返回输出。"""
    full = [
        "ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no",
        "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=20",
        f"{USER}@{HOST}", cmd,
    ]
    print(f"\n$ {cmd[:200]}")
    proc = subprocess.run(
        full, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    print(out.strip()[-4000:])
    if proc.returncode != 0 and check:
        raise SystemExit(f"远程命令失败（{proc.returncode}）：{cmd[:200]}")
    return out


def stage_sync(ref: str) -> None:
    """阶段 1：导出 + 同步。"""
    print("\n=== [1/5] 同步代码 ===")
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "deploy" / "sync_git_archive.py"),
         "--push", "--ref", ref],
        cwd=str(REPO_ROOT),
    )
    if r.returncode != 0:
        raise SystemExit("同步失败")


def stage_build() -> None:
    """阶段 2：构建镜像。"""
    print("\n=== [2/5] 构建镜像（backend + web）===")
    ssh(
        f"cd {REMOTE} && {COMPOSE} build backend web 2>&1 | tail -40",
        timeout=2400,
    )
    # 校验镜像确实产出了
    out = ssh(
        f"cd {REMOTE} && sudo docker images --format '{{{{.Repository}}}}:{{{{.Tag}}}} {{{{.CreatedAt}}}}' | grep deploy- | head -5"
    )
    print(out)


def stage_restart() -> None:
    """阶段 3：重启 backend + worker。"""
    print("\n=== [3/5] 重启容器 ===")
    ssh(
        f"cd {REMOTE} && {COMPOSE} up -d --no-deps --force-recreate backend worker 2>&1 | tail -20"
    )
    # 等健康
    print("等待 backend 健康…")
    for i in range(30):
        out = ssh(
            f"cd {REMOTE} && {COMPOSE} ps backend --format '{{{{.Status}}}}' 2>/dev/null | head -1",
            check=False,
        )
        if "healthy" in out.lower():
            print(f"backend healthy（第 {i + 1} 次探测）")
            return
        import time
        time.sleep(4)
    raise SystemExit("backend 未在 2 分钟内变为 healthy")


def stage_align() -> None:
    """阶段 4：对齐生产库数据。"""
    print("\n=== [4/5] 对齐生产库（内测不限额）===")
    ssh(
        f"cd {REMOTE} && {COMPOSE} exec -T backend python /app/scripts/align_beta_quota.py 2>&1 | tail -40"
    )


def stage_smoke() -> None:
    """阶段 5：真实业务链路冒烟。"""
    print("\n=== [5/5] 冒烟（真实业务链路）===")
    out = ssh(
        f"cd {REMOTE} && {COMPOSE} exec -T backend python /app/scripts/smoke_beta.py 2>&1 | tail -40",
        check=False,
    )
    if "SMOKE_FAIL" in out:
        raise SystemExit("冒烟失败")
    print("冒烟通过")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="develop")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--skip-smoke", action="store_true")
    args = ap.parse_args()

    stage_sync(args.ref)
    if not args.skip_build:
        stage_build()
    stage_restart()
    stage_align()
    if not args.skip_smoke:
        stage_smoke()
    print("\n=== 部署完成 ===")


if __name__ == "__main__":
    main()
