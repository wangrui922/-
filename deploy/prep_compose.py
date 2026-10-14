import os
"""更新服务器 docker-compose.yml：backend/worker 改用已 commit 镜像 deploy-backend:async，避免服务器重新 build。
同时把本地 compose 的改动保持一致（build 保留 + image 指定 commit 镜像）。
"""
import paramiko

HOST = "118.25.44.126"; USER = "ubuntu"; PWD = os.environ.get("COACH_SSH_PASSWORD", "")

LOCAL_COMPOSE = r"D:\制作网页\错题教练\cuotixiaolaoshi\deploy\docker-compose.yml"
with open(LOCAL_COMPOSE, "r", encoding="utf-8") as f:
    content = f.read()

# 在 backend 与 worker 服务同时保留 build 与 image（本地无该镜像，但服务器用它避免 build）
# 服务器用 sed 把 build 块替换为 image
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

# 服务器上已有 compose（很可能已含 worker），先查看
run("grep -n 'worker\\|build\\|image' /home/ubuntu/coach/deploy/docker-compose.yml | head -30")
cli.close()
print("DONE")