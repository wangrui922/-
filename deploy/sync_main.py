import os
"""将本地 main.py（含 lifespan 调度）同步进容器，并检查容器内依赖。"""
import paramiko

HOST = "118.25.44.126"; USER = "ubuntu"; PWD = os.environ.get("COACH_SSH_PASSWORD", "")
B = "/home/ubuntu/coach/backend"
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

sftp = cli.open_sftp()
sftp.put(r"D:\制作网页\错题教练\cuotixiaolaoshi\backend\app\main.py", f"{B}/app/main.py")
print("uploaded main.py")
sftp.close()

run("sudo docker cp /home/ubuntu/coach/backend/app/main.py deploy-backend-1:/app/app/main.py")
# 容器内检查依赖
run("sudo docker exec deploy-backend-1 sh -c 'python -c \"import celery, redis; print(\\\"celery\\\", celery.__version__, \\\"redis\\\", redis.__version__)\" 2>&1; python -c \"import apscheduler; print(\\\"apscheduler\\\", apscheduler.__version__)\" 2>&1'")
cli.close()
print("DONE")