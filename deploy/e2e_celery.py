"""端到端验证 Celery 异步链路：
1) 容器内创建测试学生 + 生成 access token
2) API 提交 chat-start 异步任务
3) 轮询 /api/tasks/{id} 直到完成
"""
import paramiko, base64, json, time

HOST = "118.25.44.126"; USER = "ubuntu"; PWD = os.environ.get("COACH_SSH_PASSWORD", "")
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=30)

def run(cmd, timeout=120):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    print(f"$ {cmd[:100]}")
    if out: print(out)
    if err: print("STDERR:", err)
    return out, err

# 1) 容器内创建测试学生并生成 token
CODE = r'''
import uuid, sys, json
sys.path.insert(0, "/app")
import os
os.environ.setdefault("APP_ENV", "prod")
from app.db import SessionLocal
from app.models.user import User, Student
from app.services.auth_service import build_token_payload, hash_password

db = SessionLocal()
phone = "13900001111"
u = db.query(User).filter(User.phone == phone).first()
if not u:
    u = User(
        id=uuid.uuid4(), phone=phone, nickname="异步测试生",
        role="student", status="active",
        access_pin_hash=hash_password("Test1234"),
    )
    db.add(u); db.flush()
    s = Student(id=u.id, user_id=u.id, grade=6)
    db.add(s); db.commit()
else:
    s = db.query(Student).filter(Student.user_id == u.id).first()
payload = build_token_payload(u, s)
print(json.dumps({"token": payload.get("accessToken") or payload.get("access"), "uid": str(u.id)}))
db.close()
'''
enc = base64.b64encode(CODE.encode("utf-8")).decode("ascii")
out, _ = run(f"sudo docker exec deploy-backend-1 sh -c 'echo {enc} | base64 -d | python3'", timeout=90)
token = None
try:
    data = json.loads(out.splitlines()[-1])
    token = data["token"]
except Exception as e:
    print("TOKEN EXTRACT FAIL:", e, "| raw:", out[:200])
    cli.close(); raise SystemExit(1)

api = "http://127.0.0.1"
# 2) 提交异步任务
submit = f"""curl -s -X POST {api}/api/tasks/async/chat-start -H "Authorization: Bearer {token}" -H "Content-Type: application/json" -d '{{"text":"写一段20字内向初中生解释什么是一元一次方程的回答"}}'"""
out2, _ = run(submit, timeout=60)
print("SUBMIT:", out2)
task_id = None
try:
    task_id = json.loads(out2)["data"]["task_id"]
except Exception as e:
    print("TASK_ID FAIL:", e, "| raw:", out2[:300])
    cli.close(); raise SystemExit(1)

# 3) 轮询 10 次 x 8s
for i in range(10):
    time.sleep(8)
    out3, _ = run(f"curl -s {api}/api/tasks/{task_id}", timeout=30)
    print(f"POLL[{i}] {out3[:300]}")
    if '"status":"ok"' in out3 or '"status": "ok"' in out3 or '"status":"error"' in out3 or '"status": "error"' in out3:
        print("== FINAL STATE REACHED ==")
        break
cli.close()
print("DONE")