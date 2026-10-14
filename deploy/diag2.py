"""诊断：web nginx 代理 + users 表实际数据。"""
import os

import paramiko, base64

HOST = os.environ.get("COACH_SSH_HOST", "118.25.44.126")
USER = os.environ.get("COACH_SSH_USER", "ubuntu")
PWD = os.environ.get("COACH_SSH_PASSWORD")
if not PWD:
    raise SystemExit("缺少环境变量 COACH_SSH_PASSWORD（凭据不得写入代码）")
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=30)

def run(cmd, timeout=90):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    print(f"$ {cmd[:120]}")
    if out: print(out)
    if err: print("STDERR:", err)
    return out, err

# web 容器与代理配置
run("sudo docker ps --format '{{.Names}} {{.Ports}}'")
run("sudo docker exec deploy-web-1 sh -c 'cat /etc/nginx/conf.d/default.conf 2>/dev/null || cat /etc/nginx/nginx.conf' 2>&1 | head -40", timeout=60)
# users 表
CODE = '''
import sqlite3, json
conn = sqlite3.connect("/home/ubuntu/coach/runtime/data/coach.db")
cur = conn.cursor()
cur.execute("SELECT sql FROM sqlite_master WHERE name='users'")
print("SCHEMA:", cur.fetchone())
cur.execute("SELECT id, phone, nickname, role, created_at FROM users LIMIT 5")
for r in cur.fetchall():
    print(r)
conn.close()
'''
enc = base64.b64encode(CODE.encode("utf-8")).decode("ascii")
run(f"echo {enc} | base64 -d | sudo python3", timeout=60)
cli.close()
print("DONE")