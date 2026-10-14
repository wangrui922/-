import os
"""compose up -d backend worker（用 async 镜像），等待 healthy，观察 worker 日志。"""
import paramiko, time

HOST = "118.25.44.126"; USER = "ubuntu"; PWD = os.environ.get("COACH_SSH_PASSWORD", "")
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=30)

def run(cmd, timeout=180):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    print(f"$ {cmd}")
    if out: print(out)
    if err: print("STDERR:", err)
    return out, err

run("cd /home/ubuntu/coach/deploy && sudo docker compose up -d --no-deps backend worker", timeout=240)
time.sleep(20)
run("cd /home/ubuntu/coach/deploy && sudo docker compose ps")
cli.close()
print("DONE")