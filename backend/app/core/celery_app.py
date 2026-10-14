"""Celery 任务队列配置（轻量实现，适配 2 核 4G VPS）。

设计约束：
    * Redis 作为 broker 与 backend（默认端口 6379，单节点即可）
    * Worker 进程数 1（2 核机器上 backend 已占 2 gunicorn workers）
    * 所有 AI 调用任务使用 ``ark_client.ark_respond_async``（原生 async）
    * 任务状态查询走 API，前端轮询获取结果
    * 失败重试：指数退避，最多 3 次
"""

from __future__ import annotations

import os
from celery import Celery  # type: ignore[import-untyped]

BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0")

celery_app = Celery(
    "ai_coach",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=["app.tasks.ai_tasks", "app.tasks.cron_jobs"],
)

celery_app.conf.update(
    result_expires=3600 * 24,           # 结果保留 24 小时
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_default_retry_delay=10,         # 首次重试间隔 10 秒
    task_max_retries=3,                  # 最多重试 3 次
    worker_prefetch_multiplier=1,          # 一次只拿一个任务（防止长任务阻塞）
    broker_connection_retry_on_startup=True,
)
