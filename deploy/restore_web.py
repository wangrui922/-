import os
"""恢复 web/admin-web 容器：先看镜像是否存在，再以无 build 方式启动。"""
import paramiko

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

run("sudo docker images | grep -E 'deploy-web|deploy-admin-web'")
run("cd /home/ubuntu/coach/deploy && sudo docker compose up -d --no-deps web admin-web", timeout=300)
run("sudo docker ps --format '{{.Names}} {{.Status}} {{.Ports}}'")
# 验证 80 端口代理
run("curl -s -o /dev/null -w 'health:%{http_code}\\n' http://127.0.0.1/api/health", timeout=30)
run("curl -s -o /dev/null -w 'root:%{http_code}\\n' http://127.0.0.1/", timeout=30)
run("curl -s -o /dev/null -w 'admin:%{http_code}\\n' http://127.0.0.1:8080/", timeout=30)
cli.close()
print("DONE")