#!/usr/bin/env python3
"""一键创建腾讯云 SCF 函数 + 定时触发器（错题教练每日同步）。

用法：
    pip install tencentcloud-sdk-python
    python create_scf.py

前提：
    * 当前 AK/SK 已关联 QcloudSCFFullAccess 策略（否则报 UnauthorizedOperation）
    * scf-daily-sync.zip 与本脚本在同一目录
"""

import base64
import json
import os
import sys

from tencentcloud.common.credential import Credential
from tencentcloud.scf.v20180416 import scf_client, models
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.common.profile.client_profile import ClientProfile

# --------------------------------------------------------------------------- 配置
SECRET_ID = "***REMOVED***"
SECRET_KEY = "***REMOVED***"
REGION = "ap-beijing"

FUNCTION_NAME = "coach-daily-sync"
RUNTIME = "Python3.9"
HANDLER = "index.main_handler"
MEMORY_SIZE = 64
TIMEOUT = 60
DESCRIPTION = "错题教练每日同步触发器：调用服务器 cron 端点执行掌握度刷新+周报+会话清理"

ENV_VARIABLES = {
    "CRON_URL": "https://118.25.44.126/api/cron/daily-sync",
    "CRON_TOKEN": "***REMOVED***",
}

TRIGGER_NAME = "daily-sync"
TRIGGER_TYPE = "timer"
# 腾讯云 SCF 定时触发器 cron 格式（6 位）：秒 分 时 日 月 周
# 0 5 2 * * * * 表示每天 02:05
TRIGGER_DESC = "0 5 2 * * * *"

ZIP_FILENAME = "scf-daily-sync.zip"


def _read_zip() -> bytes:
    path = os.path.join(os.path.dirname(__file__), ZIP_FILENAME)
    if not os.path.exists(path):
        print(f"[ERR] 未找到 {path}，请确认 zip 包与脚本在同一目录。")
        sys.exit(1)
    with open(path, "rb") as f:
        return f.read()


def _get_client():
    cred = Credential(SECRET_ID, SECRET_KEY)
    http_profile = HttpProfile(endpoint="scf.tencentcloudapi.com")
    client_profile = ClientProfile(httpProfile=http_profile)
    return scf_client.ScfClient(cred, REGION, client_profile)


def create_function():
    client = _get_client()
    req = models.CreateFunctionRequest()
    req.FunctionName = FUNCTION_NAME
    req.Runtime = RUNTIME
    req.Handler = HANDLER
    req.MemorySize = MEMORY_SIZE
    req.Timeout = TIMEOUT
    req.Description = DESCRIPTION
    req.Code = models.Code()
    req.Code.ZipFile = base64.b64encode(_read_zip()).decode()
    req.Environment = models.Environment()
    req.Environment.Variables = [
        models.Variable(Key=k, Value=v) for k, v in ENV_VARIABLES.items()
    ]

    print(f"[INFO] 创建函数 {FUNCTION_NAME} ...")
    resp = client.CreateFunction(req)
    print(f"[OK] 函数创建成功，RequestId={resp.RequestId}")
    return resp.RequestId


def create_trigger():
    client = _get_client()
    req = models.CreateTriggerRequest()
    req.FunctionName = FUNCTION_NAME
    req.TriggerName = TRIGGER_NAME
    req.Type = TRIGGER_TYPE
    req.TriggerDesc = TRIGGER_DESC
    req.CustomArgument = ""
    req.Enable = "OPEN"

    print(f"[INFO] 创建定时触发器 {TRIGGER_NAME} ({TRIGGER_DESC}) ...")
    resp = client.CreateTrigger(req)
    print(f"[OK] 触发器创建成功，RequestId={resp.RequestId}")
    return resp.RequestId


def main():
    print("=" * 60)
    print("错题教练 SCF 云函数一键创建")
    print("=" * 60)

    try:
        create_function()
    except Exception as exc:
        err = str(exc)
        if "ResourceInUse" in err:
            print(f"[WARN] 函数 {FUNCTION_NAME} 已存在，跳过创建")
        elif "UnauthorizedOperation" in err:
            print(f"[ERR] 权限不足：AK/SK 未关联 QcloudSCFFullAccess 策略")
            print(f"      请在腾讯云 CAM → 用户 → 权限 → 关联策略后重试")
            sys.exit(1)
        else:
            print(f"[ERR] 创建函数失败: {err[:500]}")
            sys.exit(1)

    try:
        create_trigger()
    except Exception as exc:
        err = str(exc)
        if "ResourceInUse" in err:
            print(f"[WARN] 触发器 {TRIGGER_NAME} 已存在，跳过创建")
        else:
            print(f"[ERR] 创建触发器失败: {err[:500]}")
            sys.exit(1)

    print("-" * 60)
    print("[DONE] 全部完成！")
    print(f"       函数名: {FUNCTION_NAME}")
    print(f"       触发器: 每天 02:05 ({TRIGGER_DESC})")
    print(f"       目标URL: {ENV_VARIABLES['CRON_URL']}")
    print("-" * 60)
    print("验证方式：")
    print("  1. 登录腾讯云 SCF 控制台查看函数日志")
    print("  2. 在控制台点「测试」手动触发一次")
    print("  3. 服务器日志应出现 'cron daily-sync finished'")


if __name__ == "__main__":
    main()