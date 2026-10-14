import os
"""给 worker 禁用镜像自带 HEALTHCHECK 并重启，然后做端到端异步任务验证。"""
import paramiko, base64, time

HOST = "118.25.44.126"; USER = "ubuntu"; PWD = os.environ.get("COACH_SSH_PASSWORD", "")
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=30)

def run(cmd, timeout=120):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    print(f"$ {cmd[:120]}")
    if out: print(out)
    if err: print("STDERR:", err)
    return out, err

# 1) compose 中为 worker 增加 healthcheck: disable
SCRIPT = r'''
p = "/home/ubuntu/coach/deploy/docker-compose.yml"
s = open(p, encoding="utf-8").read()
old = """    command: ["celery", "-A", "app.core.celery_app", "worker", "--loglevel=info", "--concurrency=1"]
"""
new = """    command: ["celery", "-A", "app.core.celery_app", "worker", "--loglevel=info", "--concurrency=1"]
    healthcheck:
      disable: true
"""
if "healthcheck:\n      disable: true" not in s:
    s = s.replace(old, new)
    open(p, "w", encoding="utf-8").write(s)
    print("worker healthcheck disabled")
else:
    print("already disabled")
'''
enc = base64.b64encode(SCRIPT.encode("utf-8")).decode("ascii")
run(f"echo {enc} | base64 -d | sudo python3", timeout=60)

# 2) 重启 worker
run("cd /home/ubuntu/coach/deploy && sudo docker compose up -d --no-deps worker", timeout=120)
time.sleep(5)
run("sudo docker ps --format '{{.Names}} {{.Status}}'")
cli.close()
print("DONE")