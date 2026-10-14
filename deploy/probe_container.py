import os
"""探查 deploy-backend-1 容器内目录结构。"""
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

run("sudo docker exec deploy-backend-1 sh -c 'pwd; ls /app/app 2>/dev/null || ls /app'" )
run("sudo docker exec deploy-backend-1 sh -c 'find /app -maxdepth 3 -name tasks -type d 2>/dev/null'")
cli.close()
print("DONE")