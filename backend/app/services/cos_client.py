"""腾讯云 COS 客户端封装（Manim 视频 CDN 前置）。

职责：
    1. 渲染完成后上传 mp4 到 COS；
    2. 生成 CDN 加速 URL；
    3. 本地热文件清理（保留最近 N 天）。

环境变量（从 .env.production 注入）：
    COS_SECRET_ID   -- 腾讯云 SecretId
    COS_SECRET_KEY  -- 腾讯云 SecretKey
    COS_REGION      -- 地域（如 ap-guangzhou）
    COS_BUCKET      -- Bucket 名（如 xuexu-cos-1330112249）
    COS_CDN_DOMAIN  -- CDN 加速域名（可选，默认用 COS 默认域名）
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("app.services.cos_client")

# 懒加载 COS 客户端
def _get_client():
    from qcloud_cos import CosConfig, CosS3Client
    
    secret_id = os.environ.get("COS_SECRET_ID", "")
    secret_key = os.environ.get("COS_SECRET_KEY", "")
    region = os.environ.get("COS_REGION", "ap-guangzhou")
    
    if not secret_id or not secret_key:
        raise RuntimeError("COS 凭证未配置")
    
    config = CosConfig(
        Region=region,
        SecretId=secret_id,
        SecretKey=secret_key,
        Scheme="https",
    )
    return CosS3Client(config)


def _bucket_name() -> str:
    return os.environ.get("COS_BUCKET", "xuexu-cos-1330112249")


def _cdn_domain() -> str:
    domain = os.environ.get("COS_CDN_DOMAIN", "")
    if domain:
        return domain.rstrip("/")
    # 默认用 COS 请求域名
    bucket = _bucket_name()
    region = os.environ.get("COS_REGION", "ap-guangzhou")
    return f"https://{bucket}.cos.{region}.myqcloud.com"


def upload_video(local_path: Path, key: str) -> Optional[str]:
    """上传本地视频到 COS，返回 CDN URL。

    Args:
        local_path: 本地 mp4 文件路径。
        key: COS 对象键（如 manim/{task_id}/output.mp4）。

    Returns:
        CDN URL（https://...）上传成功时；None 时上传失败（保留本地文件）。
    """
    if not local_path.is_file():
        logger.warning("COS upload skip: local file not found %s", local_path)
        return None
    
    try:
        client = _get_client()
        bucket = _bucket_name()
        
        response = client.upload_file(
            Bucket=bucket,
            Key=key,
            LocalFilePath=str(local_path),
            EnableMD5=False,  # woff2 字体已做 MD5，大视频省一次计算
        )
        etag = response.get("ETag", "")
        url = f"{_cdn_domain()}/{key}"
        logger.info("COS upload success key=%s etag=%s url=%s", key, etag, url)
        return url
    except Exception as exc:  # noqa: BLE001
        logger.error("COS upload failed key=%s: %s", key, exc)
        return None


def cleanup_local_videos(storage_dir: Path, keep_days: int = 7) -> int:
    """清理本地超过 keep_days 天的 manim 视频文件，释放磁盘。

    Args:
        storage_dir: 存储根目录（如 /app/data/storage）。
        keep_days: 保留最近 N 天。

    Returns:
        删除的文件数。
    """
    manim_dir = storage_dir / "manim"
    if not manim_dir.is_dir():
        return 0
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    removed = 0
    
    for task_dir in manim_dir.iterdir():
        if not task_dir.is_dir():
            continue
        video = task_dir / "output.mp4"
        if not video.is_file():
            continue
        mtime = datetime.fromtimestamp(video.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            try:
                video.unlink()
                removed += 1
                logger.info("Local video cleanup: removed %s", video)
            except OSError as exc:
                logger.warning("Local video cleanup failed %s: %s", video, exc)
    
    if removed:
        logger.info("Local video cleanup done: removed=%d keep_days=%d", removed, keep_days)
    return removed


__all__ = ["upload_video", "cleanup_local_videos"]
