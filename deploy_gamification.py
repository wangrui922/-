# -*- coding: utf-8 -*-
"""Paramiko 部署脚本：上传游戏化后端 + 前端构建产物，执行数据库迁移，重启容器。"""
import os
import sys
import io
import zipfile
import paramiko

HOST = "118.25.44.126"
USER = "ubuntu"
KEY_PATH = r"D:\制作网页\错题教练\AIjiaolian.pem"
DEPLOY_DIR = "/home/ubuntu/coach"


def get_client():
    pk = paramiko.RSAKey.from_private_key_file(KEY_PATH)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(hostname=HOST, username=USER, pkey=pk, timeout=15)
    return c


def upload_zip(sftp, local_dir: str, remote_path: str):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(local_dir):
            for f in files:
                fp = os.path.join(root, f)
                arc = os.path.relpath(fp, local_dir)
                zf.write(fp, arc)
    buf.seek(0)
    sftp.putfo(buf, remote_path)
    print(f"[upload] {remote_path} ({buf.getbuffer().nbytes} bytes)")


def exec_cmd(c, cmd):
    print(f"[exec] {cmd[:80]}...")
    stdin, stdout, stderr = c.exec_command(cmd)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    if out:
        print(out[:400])
    if err:
        print("[stderr]", err[:400])
    return out, err


def main():
    print("connecting...")
    client = get_client()
    sftp = client.open_sftp()
    print("connected")

    # 1. 上传后端增量
    print("=== backend zip ===")
    upload_zip(sftp, r"D:\制作网页\错题教练\cuotixiaolaoshi\deploy-package\backend\app", "/tmp/gamify_backend.zip")
    exec_cmd(client, f"cd {DEPLOY_DIR} && unzip -o /tmp/gamify_backend.zip -d backend/app/")

    # 2. 上传前端构建产物
    print("=== frontend dist zip ===")
    upload_zip(sftp, r"D:\制作网页\错题教练\cuotixiaolaoshi\student-web\dist", "/tmp/gamify_web.zip")
    exec_cmd(client, f"cd {DEPLOY_DIR} && unzip -o /tmp/gamify_web.zip -d deploy/student-web/")

    # 3. 数据库迁移
    print("=== db migrate ===")
    db_path = f"{DEPLOY_DIR}/runtime/data/coach.db"
    exec_cmd(client, f"cd {DEPLOY_DIR} && cat backend/migrations/012_gamification_sqlite.sql | sqlite3 {db_path}")

    # 4. 检查场景题表字段
    print("=== check scenario columns ===")
    out, _ = exec_cmd(client, f"sqlite3 {db_path} \"PRAGMA table_info(questions);\"")
    if "scenario_type" not in out:
        print("[WARN] scenario_type column missing, add ALTER TABLE")
        exec_cmd(client, f"sqlite3 {db_path} \"ALTER TABLE questions ADD COLUMN scenario_type TEXT;\"")
        exec_cmd(client, f"sqlite3 {db_path} \"ALTER TABLE questions ADD COLUMN scenario_template TEXT;\"")
        exec_cmd(client, f"sqlite3 {db_path} \"ALTER TABLE questions ADD COLUMN scenario_params TEXT;\"")

    # 5. 上传场景题脚本并执行
    print("=== scenario questions ===")
    local_script = r"D:\制作网页\错题教练\cuotixiaolaoshi\backend\scripts\generate_scenario_bank.py"
    sftp.put(local_script, "/tmp/generate_scenario_bank.py")
    exec_cmd(client, f"cd {DEPLOY_DIR} && python3 /tmp/generate_scenario_bank.py {db_path}")

    # 6. 重启后端容器
    print("=== restart backend ===")
    exec_cmd(client, f"cd {DEPLOY_DIR}/deploy && docker compose stop backend worker && docker compose up -d backend worker")

    # 7. 简单健康检查
    print("=== healthcheck ===")
    out, err = exec_cmd(client, f"docker exec deploy-backend-1 python -c \"import app.services.game_service; print('game_service OK')\" 2>&1")
    if "game_service OK" not in (out + err):
        print("[WARN] game_service import failed")

    print("=== done ===")
    client.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[ERROR]", e)
        sys.exit(1)
