import os
"""容器内验证新代码可导入（base64 传脚本避免引号问题）。"""
import base64, paramiko

HOST = "118.25.44.126"; USER = "ubuntu"; PWD = os.environ.get("COACH_SSH_PASSWORD", "")
CODE = '''
from app.main import app
from app.api import tasks as tasks_router
from app.core.celery_app import celery_app
from app.tasks import ai_tasks, cron_jobs
celery_app.loader.import_default_modules()
routes = [r.path for r in app.routes if "tasks" in getattr(r, "path", "")]
print("tasks routes:", routes)
names = [n for n in ["ai_chat_start","ai_chat_message","ai_exercise_gen","ai_exercise_guide","ai_exercise_judge","ai_exercise_summary"] if n in celery_app.tasks]
print("celery tasks:", names)
print("IMPORT OK")
'''
ENC = base64.b64encode(CODE.encode("utf-8")).decode("ascii")

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=30)

def run(cmd, timeout=120):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    print(f"$ {cmd[:80]}...")
    if out: print(out)
    if err: print("STDERR:", err)
    return out, err

dec = f'echo {ENC} | base64 -d | python'
run(f"sudo docker exec deploy-backend-1 sh -c 'cd /app && {dec}'", timeout=120)
cli.close()
print("DONE")