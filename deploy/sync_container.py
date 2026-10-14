import os
"""在容器内补齐新代码：创建 tasks 目录、复制 tasks/api 模块、验证。"""
import paramiko

HOST = "118.25.44.126"; USER = "ubuntu"; PWD = os.environ.get("COACH_SSH_PASSWORD", "")
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=30)

def run(cmd, timeout=90):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    print(f"$ {cmd}")
    if out: print(out)
    if err: print("STDERR:", err)
    return out, err

B = "/home/ubuntu/coach/backend"

# 1) 容器内创建 tasks 目录
run(f"sudo docker exec deploy-backend-1 sh -c 'mkdir -p /app/app/tasks'")

# 2) 复制 tasks 三件套进容器
for f in ["__init__.py", "ai_tasks.py", "cron_jobs.py"]:
    run(f"sudo docker cp {B}/app/tasks/{f} deploy-backend-1:/app/app/tasks/{f}")

# 3) 复制 api/tasks.py 与修改过的 api/__init__.py
run(f"sudo docker cp {B}/app/api/tasks.py deploy-backend-1:/app/app/api/tasks.py")
run(f"sudo docker cp {B}/app/api/__init__.py deploy-backend-1:/app/app/api/__init__.py")

# 4) 验证容器内文件齐备
run("sudo docker exec deploy-backend-1 sh -c 'ls -la /app/app/tasks/ && ls -la /app/app/api/ | grep tasks'")
cli.close()
print("DONE")