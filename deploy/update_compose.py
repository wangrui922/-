import os
"""把服务器 compose 的 backend/worker build 改为 image: deploy-backend:async（避免重新 build 超时）。"""
import paramiko

HOST = "118.25.44.126"; USER = "ubuntu"; PWD = os.environ.get("COACH_SSH_PASSWORD", "")
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

# 用 python 在服务器上原地替换 backend/worker 的 build 块为 image
script = r'''
import re
p = "/home/ubuntu/coach/deploy/docker-compose.yml"
s = open(p, encoding="utf-8").read()
# backend build 块
s = s.replace("""  backend:
    build:
      context: ../backend
      dockerfile: Dockerfile
""", """  backend:
    image: deploy-backend:async
""")
# worker build 块
s = s.replace("""  worker:
    build:
      context: ../backend
      dockerfile: Dockerfile
""", """  worker:
    image: deploy-backend:async
""")
open(p, "w", encoding="utf-8").write(s)
print("compose updated")
'''
import base64
enc = base64.b64encode(script.encode("utf-8")).decode("ascii")
run(f"echo {enc} | base64 -d | sudo python3", timeout=60)
run("grep -n 'image\\|build\\|worker\\|backend:' /home/ubuntu/coach/deploy/docker-compose.yml | head -20")
cli.close()
print("DONE")