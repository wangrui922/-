"""SSH 检查服务器当前状态：文件、容器、redis。

凭据一律走环境变量，禁止硬编码：
    set COACH_SSH_PASSWORD=xxxx        (Windows)
    export COACH_SSH_PASSWORD=xxxx     (Linux/macOS)
可选覆盖：COACH_SSH_HOST / COACH_SSH_USER
"""
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

def run(cmd, timeout=60):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    print(f"$ {cmd}")
    if out: print(out)
    if err: print("STDERR:", err)
    return out, err

run("ls -l /home/ubuntu/coach/backend/app/tasks/")
run("docker compose -f /home/ubuntu/coach/deploy/docker-compose.yml ps")
run("head -3 /home/ubuntu/coach/backend/app/tasks/cron_jobs.py")
cli.close()
print("DONE")