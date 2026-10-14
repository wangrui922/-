import os
"""上传修复后的 cron_jobs.py / celery_app.py 到宿主机，再 docker cp 进运行中容器。"""
import paramiko

HOST = "118.25.44.126"
USER = "ubuntu"
PWD = os.environ.get("COACH_SSH_PASSWORD", "")
LOCAL = r"D:\制作网页\错题教练\cuotixiaolaoshi\backend"

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=30)
sftp = cli.open_sftp()

files = [
    ("app/tasks/cron_jobs.py", "/home/ubuntu/coach/backend/app/tasks/cron_jobs.py"),
    ("app/core/celery_app.py", "/home/ubuntu/coach/backend/app/core/celery_app.py"),
]
for rel, remote in files:
    sftp.put(rf"{LOCAL}\{rel.replace('/', chr(92))}", remote)
    print("uploaded:", remote)

def run(cmd, timeout=90):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    print(f"$ {cmd}")
    if out: print(out)
    if err: print("STDERR:", err)
    return out, err

# docker cp 进容器（容器内路径 /app）
run("sudo docker cp /home/ubuntu/coach/backend/app/tasks/cron_jobs.py deploy-backend-1:/app/app/tasks/cron_jobs.py")
run("sudo docker cp /home/ubuntu/coach/backend/app/core/celery_app.py deploy-backend-1:/app/app/core/celery_app.py")
sftp.close(); cli.close()
print("DONE")