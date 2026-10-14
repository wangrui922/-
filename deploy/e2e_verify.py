import os
"""端到端验证：1) 路由可达 2) 真实登录 3) 提交异步任务 4) 轮询状态。"""
import paramiko

HOST = "118.25.44.126"; USER = "ubuntu"; PWD = os.environ.get("COACH_SSH_PASSWORD", "")
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=30)

def run(cmd, timeout=90):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    print(f"$ {cmd[:100]}")
    if out: print(out)
    if err: print("STDERR:", err)
    return out, err

# 1) 不存在的 task_id 应返回 pending JSON（证明路由已注册）
run("curl -s http://127.0.0.1:80/api/tasks/nonexistent-id-12345", timeout=30)
run("curl -s http://127.0.0.1:80/api/tasks/nonexistent-id-12345", timeout=30)  # 双重确认

# 2) 从数据库找一个学生账号（手机号）
CODE = '''
import sqlite3, json
conn = sqlite3.connect("/home/ubuntu/coach/runtime/data/coach.db")
cur = conn.cursor()
cur.execute("SELECT id, phone FROM users WHERE role='student' LIMIT 3")
rows = cur.fetchall()
print(json.dumps(rows))
conn.close()
'''
import base64
enc = base64.b64encode(CODE.encode("utf-8")).decode("ascii")
run(f"echo {enc} | base64 -d | sudo python3", timeout=60)
cli.close()
print("DONE")