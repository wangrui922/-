"""commit 新镜像 -> 重启 backend -> 启动 worker，分短命令执行。"""
import os

import paramiko

HOST = os.environ.get("COACH_SSH_HOST", "118.25.44.126")
USER = os.environ.get("COACH_SSH_USER", "ubuntu")
PWD = os.environ.get("COACH_SSH_PASSWORD")
if not PWD:
    raise SystemExit("缺少环境变量 COACH_SSH_PASSWORD（凭据不得写入代码）")
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=30)

def run(cmd, timeout=120):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    print(f"$ {cmd}")
    if out: print(out)
    if err: print("STDERR:", err)
    return out, err

# 1) commit 当前容器为新镜像（包含新代码 + celery 依赖）
run("sudo docker commit deploy-backend-1 deploy-backend:async", timeout=180)

# 2) 重启 backend 加载新代码
run("sudo docker restart deploy-backend-1", timeout=60)
cli.close()
print("DONE")