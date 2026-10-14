# SCF 云函数：错题教练每日同步触发器（Tencent Cloud SCF / 云函数）
#
# 架构说明：
#   生产库是服务器本地 SQLite（/home/ubuntu/coach/runtime/data/coach.db），
#   云函数无法直连文件型数据库。因此本函数采用「HTTP 触发」模式：
#   定时向服务器端 cron 端点发送请求，由部署在服务器上的 backend 执行
#   掌握度刷新 / 周报生成 / 会话清理（逻辑与 APScheduler 的 daily_sync 一致）。
#
# 部署方式：
#   1. 把本脚本打包为 zip（Python3.9 运行时，仅用标准库，无需 requirements）
#   2. 上传至腾讯云 SCF 控制台创建函数，入口 main_handler
#   3. 配置定时触发器（Timer），cron: 0 5 2 * * * *（每天 02:05，错开服务器 02:00）
#   4. 环境变量：
#        CRON_URL   = https://118.25.44.126/api/cron/daily-sync
#        CRON_TOKEN = <与服务器 .env.production 的 CRON_TOKEN 一致>

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CRON_URL = os.environ.get("CRON_URL", "")
CRON_TOKEN = os.environ.get("CRON_TOKEN", "")


def main_handler(event, context):
    """SCF 入口函数：触发服务器端每日同步。

    Args:
        event: 触发器传入的事件（Timer 触发时为空 dict）
        context: SCF 运行时上下文

    Returns:
        {"statusCode": 200, "body": json.dumps(result)}
    """
    logger.info("SCF daily sync trigger started")

    if not CRON_URL or not CRON_TOKEN:
        msg = "CRON_URL / CRON_TOKEN 未配置"
        logger.error(msg)
        return {
            "statusCode": 500,
            "body": json.dumps({"status": "error", "error": msg}, ensure_ascii=False),
        }

    req = urllib.request.Request(
        CRON_URL,
        data=b"",
        method="POST",
        headers={"X-Cron-Token": CRON_TOKEN, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            logger.info("server responded %s: %s", resp.status, body)
            return {"statusCode": 200, "body": body}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        logger.error("HTTP %s: %s", exc.code, detail)
        return {"statusCode": 502, "body": json.dumps({"status": "error", "error": detail}, ensure_ascii=False)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("trigger failed: %s", exc)
        return {
            "statusCode": 500,
            "body": json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False),
        }