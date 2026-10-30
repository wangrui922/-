#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生产库灌入 A-2 同伴视角种子语料的运维入口（本地一键，进容器执行）。

把「生产库灌语料」纳入标准运维流程：本机准备好 CSV → 一条命令
SCP 进服务器数据卷 + 在 backend 容器内跑 ``import_peer_corpus.py``。

为什么进容器跑：容器内的 ``import_peer_corpus.py`` 已随镜像发布，且
``SessionLocal()`` 自动用容器的 ``DATABASE_URL=sqlite:////app/data/coach.db``
（数据卷 ``../runtime/data:/app/data``），无需指定库路径。

安全：
  * 默认 dry-run（容器内脚本不加 --confirm 不落库）；本助手显式传 --confirm 才写。
  * --release 需带 --reviewer（人工放行留痕，符合 archived→released 闸门）。
  * --coverage / --list 为只读，不传 CSV、不落库，可随时查生产状态。

用法（本地执行）：
  # 只读：看生产已放行覆盖
  python deploy/seed_peer_corpus.py --coverage
  python deploy/seed_peer_corpus.py --list

  # 灌一批（先 dry-run 预览，不加 --confirm）
  python deploy/seed_peer_corpus.py --csv 本地/path/seed.csv

  # 真正落库（写生产）
  python deploy/seed_peer_corpus.py --csv 本地/path/seed.csv --confirm --batch 20261101

  # 落库 + 立即人工放行整批
  python deploy/seed_peer_corpus.py --csv 本地/path/seed.csv --confirm --batch 20261101 --release --reviewer 玥玥

容器内等价命令（供人工复核）：
  sudo docker compose -f deploy/docker-compose.yml exec -T backend \\
      python /app/scripts/import_peer_corpus.py --csv /app/data/seed.csv --confirm
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SSH_KEY = "C:/Users/Administrator/coach-keys/AIjiaolian_new"
HOST = "118.25.44.126"
USER = "ubuntu"
REMOTE = "/home/ubuntu/coach"
COMPOSE = "sudo docker compose -f deploy/docker-compose.yml"
# 数据卷宿主机目录（容器内 /app/data 映射到这里）
DATA_VOLUME_HOST = f"{REMOTE}/runtime/data"


def ssh(cmd: str, timeout: int = 600, check: bool = True) -> str:
    full = [
        "ssh", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no",
        "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=20",
        f"{USER}@{HOST}", cmd,
    ]
    print(f"\n$ {cmd[:240]}")
    proc = subprocess.run(full, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    out = (proc.stdout or "") + (proc.stderr or "")
    print(out.strip()[-3000:])
    if proc.returncode != 0 and check:
        raise SystemExit(f"远程命令失败（{proc.returncode}）：{cmd[:200]}")
    return out


def scp(local: str, remote_path: str) -> None:
    full = [
        "scp", "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no",
        "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=20",
        local, f"{USER}@{HOST}:{remote_path}",
    ]
    print(f"\n$ scp {Path(local).name} -> {remote_path}")
    proc = subprocess.run(full, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)
    if proc.returncode != 0:
        raise SystemExit(f"scp 失败（{proc.returncode}）：{proc.stderr[-1000:]}")


def main() -> None:
    ap = argparse.ArgumentParser(description="生产库灌入 A-2 同伴视角种子语料（进容器执行）")
    ap.add_argument("--csv", default=None, help="本地种子 CSV 路径（导入时必填）")
    ap.add_argument("--reviewer", default=None, help="审核人（--release 必填，留痕）")
    ap.add_argument("--batch", default=None, help="批次号（写入 seed_batch）")
    ap.add_argument("--confirm", action="store_true", help="★ 真正落库（默认仅 dry-run 预览）")
    ap.add_argument("--release", action="store_true", help="导入后整批人工放行 archived")
    ap.add_argument("--coverage", action="store_true", help="只读：打印生产放行覆盖验收")
    ap.add_argument("--list", action="store_true", help="只读：列出生产待审 archived")
    ap.add_argument("--dedup-report", action="store_true",
                    help="只读：导入前预览分类（列出新增/拦截/去重清单，不落库）")
    args = ap.parse_args()

    # ---- 只读子命令 ----
    if args.coverage:
        ssh(f"cd {REMOTE} && {COMPOSE} exec -T backend python /app/scripts/import_peer_corpus.py --coverage")
        return
    if args.list:
        ssh(f"cd {REMOTE} && {COMPOSE} exec -T backend python /app/scripts/import_peer_corpus.py --list")
        return

    # ---- 导入前预览（只读，供二次确认）----
    if args.dedup_report:
        if not args.csv:
            raise SystemExit("--dedup-report 必填 --csv（预览用）")
        if not Path(args.csv).exists():
            raise SystemExit(f"本地 CSV 不存在：{args.csv}")
        basename = Path(args.csv).name
        remote_csv = f"{DATA_VOLUME_HOST}/{basename}"
        container_csv = f"/app/data/{basename}"
        scp(args.csv, remote_csv)
        print("\n[dry-run] 以下为导入前预览（只读，未落库）；确认无误后去掉 --dedup-report 并加 --confirm 执行")
        ssh(f"cd {REMOTE} && {COMPOSE} exec -T backend python /app/scripts/import_peer_corpus.py --csv {container_csv}")
        return

    # ---- 导入子命令 ----
    if not args.csv:
        raise SystemExit("--csv 必填（导入模式）。只读请加 --coverage / --list。")
    if not Path(args.csv).exists():
        raise SystemExit(f"本地 CSV 不存在：{args.csv}")
    if args.release and not args.reviewer:
        raise SystemExit("--release 必须带 --reviewer（人工放行留痕）")

    basename = Path(args.csv).name
    remote_csv = f"{DATA_VOLUME_HOST}/{basename}"
    container_csv = f"/app/data/{basename}"

    # 1) 传 CSV 进数据卷
    scp(args.csv, remote_csv)

    # 2) 容器内导入（默认 dry-run；--confirm 才写）
    if args.confirm:
        print("\n⚠️ 即将对【生产库】执行写操作（peer_view_corpus）")
    else:
        print("\n[dry-run] 容器脚本未加 --confirm，不会落库（预览如下）")
    import_cmd = (
        f"cd {REMOTE} && {COMPOSE} exec -T backend "
        f"python /app/scripts/import_peer_corpus.py --csv {container_csv}"
    )
    if args.batch:
        import_cmd += f" --batch {args.batch}"
    if args.confirm:
        import_cmd += " --confirm"
    ssh(import_cmd)

    # 3) 可选：整批放行
    if args.release:
        ssh(
            f"cd {REMOTE} && {COMPOSE} exec -T backend "
            f"python /app/scripts/import_peer_corpus.py --release-all --reviewer {args.reviewer}"
        )
        # 放行后顺手打印覆盖，便于确认达标
        ssh(f"cd {REMOTE} && {COMPOSE} exec -T backend python /app/scripts/import_peer_corpus.py --coverage")


if __name__ == "__main__":
    main()
