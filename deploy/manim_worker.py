"""Manim 渲染 worker（独立容器，deploy-manim）。

职责：
    1. 从 Redis 队列 ``manim:queue`` 阻塞取任务 id（blpop）；
    2. 幂等校验：任务行存在且 status=queued 才算有效（避免重复渲染）；
    3. 渲染 ``{storage}/manim/{task_id}/scene.py`` -> output.mp4
       （``manim -ql`` = 480p15，2 核 4G 机器 30~120s/条）；
    4. 直接写共享 SQLite（``/app/data/coach.db``）回填状态与 video_path；
    5. 渲染完成上传腾讯云 COS 并回填 cdn_url（视频 CDN 前置，上传失败不阻塞）。

依赖：redis、sqlite3 标准库、系统 manim+ffmpeg+中文字体。
与主镜像完全隔离：不 import app.*，单个文件可独立部署。

环境变量（全部由 compose ``.env.production`` 注入，无任何硬编码凭据）：
    - ``CELERY_BROKER_URL``  Redis 地址，默认 ``redis://redis:6379/0``
    - ``DATABASE_URL``       SQLite DSN，默认 ``sqlite:////app/data/coach.db``
    - ``STORAGE_DIR``        任务目录根，默认 ``/app/data/storage``
    - ``COS_SECRET_ID`` / ``COS_SECRET_KEY``  COS 凭据（未配置则跳过上传）
    - ``COS_REGION`` / ``COS_BUCKET`` / ``COS_CDN_DOMAIN``  COS 目标配置
    - ``MANIM_RENDER_TIMEOUT``  单条渲染超时秒数，默认 360
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("manim-worker")

#: 环境变量（compose 注入）；默认值与 backend 容器一致
BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
DB_PATH = os.environ.get("DATABASE_URL", "sqlite:////app/data/coach.db").replace("sqlite:///", "")
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", "/app/data/storage"))

# COS 配置（视频 CDN 前置）
COS_SECRET_ID = os.environ.get("COS_SECRET_ID", "")
COS_SECRET_KEY = os.environ.get("COS_SECRET_KEY", "")
COS_REGION = os.environ.get("COS_REGION", "ap-guangzhou")
COS_BUCKET = os.environ.get("COS_BUCKET", "xuexu-cos-1330112249")
COS_CDN_DOMAIN = os.environ.get("COS_CDN_DOMAIN", "")

QUEUE = "manim:queue"
#: 单条渲染超时保护（manim -ql 最慢场景），超时 kill 并标记 failed
RENDER_TIMEOUT_SEC = int(os.environ.get("MANIM_RENDER_TIMEOUT", "360"))
#: 队列空转时等待秒数
IDLE_SLEEP = 2.0


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _load_task(task_id: str) -> sqlite3.Row | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, status FROM manim_tasks WHERE id = ?", (task_id,)
        ).fetchone()
    return row


def _update_status(task_id: str, status: str, **fields) -> None:
    sets = ["status = ?"]
    values: list = [status]
    for key, value in fields.items():
        sets.append(f"{key} = ?")
        values.append(value)
    values.append(task_id)
    sql = f"UPDATE manim_tasks SET {', '.join(sets)} WHERE id = ?"
    with _conn() as conn:
        conn.execute(sql, values)
        conn.execute(
            "UPDATE manim_tasks SET finished_at = datetime('now') WHERE id = ? AND status IN ('done','failed')",
            (task_id,),
        )


def _render(task_id: str) -> int:
    """执行渲染，返回渲染耗时（秒）。成功后 output.mp4 落在任务目录。"""
    task_dir = STORAGE_DIR / "manim" / task_id
    scene_file = task_dir / "scene.py"
    if not scene_file.is_file():
        raise FileNotFoundError(f"scene.py 不存在: {scene_file}")

    # 输出统一到任务目录下的 build/，避免污染仓库根
    media_dir = task_dir / "build"
    media_dir.mkdir(parents=True, exist_ok=True)

    start = time.monotonic()
    cmd = [
        "manim",
        "-ql",  # 480p15：2 核 4G 机器的性价比档位
        "--format=mp4",
        "--media_dir", str(media_dir),
        str(scene_file),
        "Scene",
    ]
    logger.info("渲染开始 task=%s cmd=%s", task_id, " ".join(cmd))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=RENDER_TIMEOUT_SEC,
        cwd=str(task_dir),
    )
    elapsed = int(time.monotonic() - start)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout)[-2000:]
        raise RuntimeError(f"manim 渲染失败 returncode={proc.returncode}: {tail}")

    # 视频在 build/videos/scene/480p15/Scene.mp4，移动到 output.mp4
    candidates = [
        media_dir / "videos" / "scene" / "480p15" / "Scene.mp4",
        media_dir / "videos" / "480p15" / "Scene.mp4",
        media_dir / "Scene.mp4",
    ]
    src = next((p for p in candidates if p.is_file()), None)
    if src is None:
        found = sorted(media_dir.rglob("*.mp4"))
        src = found[0] if found else None
    if src is None:
        raise RuntimeError("渲染完成但未找到 mp4 输出文件")
    dst = task_dir / "output.mp4"
    if dst.exists():
        dst.unlink()
    src.replace(dst)
    logger.info("渲染完成 task=%s 耗时=%ss 大小=%sKB", task_id, elapsed, dst.stat().st_size // 1024)
    return elapsed


def _upload_cos(task_id: str) -> Optional[str]:
    """上传视频到腾讯云 COS，返回 CDN URL。上传失败返回 None（不影响本地文件）。"""
    if not COS_SECRET_ID or not COS_SECRET_KEY:
        return None
    try:
        from qcloud_cos import CosConfig, CosS3Client
        config = CosConfig(Region=COS_REGION, SecretId=COS_SECRET_ID, SecretKey=COS_SECRET_KEY, Scheme="https")
        client = CosS3Client(config)
        local_path = STORAGE_DIR / "manim" / task_id / "output.mp4"
        key = f"manim/{task_id}/output.mp4"
        client.upload_file(Bucket=COS_BUCKET, Key=key, LocalFilePath=str(local_path), EnableMD5=False)
        domain = COS_CDN_DOMAIN.rstrip("/") if COS_CDN_DOMAIN else f"https://{COS_BUCKET}.cos.{COS_REGION}.myqcloud.com"
        url = f"{domain}/{key}"
        logger.info("COS upload success task=%s url=%s", task_id, url)
        return url
    except Exception as exc:  # noqa: BLE001
        logger.warning("COS upload failed task=%s: %s", task_id, exc)
        return None


def _handle(task_id: str) -> None:
    """处理单个任务（幂等 + 渲染 + COS 上传 + 回写）。"""
    row = _load_task(task_id)
    if row is None:
        logger.warning("任务不存在，跳过: %s", task_id)
        return
    if row["status"] != "queued":
        logger.info("任务已处理过(%s)，跳过: %s", row["status"], task_id)
        return

    _update_status(task_id, "rendering")
    try:
        elapsed = _render(task_id)
    except Exception as exc:  # noqa: BLE001 - 一切失败都转 failed
        logger.error("渲染失败 task=%s: %s", task_id, exc)
        _update_status(task_id, "failed", error=str(exc)[:1000])
        return
    # 上传 COS（非阻塞，失败不影响本地 done 状态）
    cdn_url = _upload_cos(task_id)
    _update_status(
        task_id,
        "done",
        video_path=f"manim/{task_id}/output.mp4",
        duration_sec=elapsed,
        cdn_url=cdn_url,
    )


def _drain_pending_from_db() -> None:
    """队列 miss 兜底：扫描所有 queued 行重新入队（进程启动时执行一次）。"""
    try:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT id FROM manim_tasks WHERE status = 'queued' ORDER BY created_at"
            ).fetchall()
    except sqlite3.OperationalError as exc:
        logger.warning("扫描 queued 任务失败(表可能尚未创建): %s", exc)
        return
    if not rows:
        return
    client = redis.Redis.from_url(BROKER_URL, decode_responses=True, retry_on_timeout=True)
    for row in rows:
        client.rpush(QUEUE, row["id"])
        logger.info("兜底重新入队 task=%s", row["id"])


def main() -> None:
    logger.info("Manim worker 启动 broker=%s queue=%s storage=%s", BROKER_URL, QUEUE, STORAGE_DIR)
    _drain_pending_from_db()
    client = redis.Redis.from_url(BROKER_URL, decode_responses=True, retry_on_timeout=True)
    logger.info("等待任务...")
    while True:
        try:
            item = client.blpop(QUEUE, timeout=5)
        except Exception as exc:  # noqa: BLE001 - 断线重连
            logger.warning("Redis 连接异常: %s，2s 后重试", exc)
            time.sleep(IDLE_SLEEP)
            continue
        if item is None:
            continue
        _task_id = item[1]
        try:
            _handle(str(_task_id))
        except Exception as exc:  # noqa: BLE001 - 单任务失败不拖垮 worker
            logger.error("处理任务异常 task=%s: %s", _task_id, exc)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
