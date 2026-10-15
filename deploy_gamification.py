# -*- coding: utf-8 -*-
"""Paramiko 部署脚本：上传游戏化后端 + 前端构建产物，执行数据库迁移，重启容器。

凭据一律走环境变量，禁止硬编码（T02 安全整改，对齐 deploy/check_server.py 惯例）：
    set COACH_SSH_PASSWORD=xxxx        (Windows)
    export COACH_SSH_PASSWORD=xxxx     (Linux/macOS)
可选覆盖：COACH_SSH_HOST / COACH_SSH_USER / COACH_SSH_KEY（私钥文件路径）

COACH_SSH_KEY 支持 ed25519 / rsa / ecdsa 自动识别（2026-10-15 轮换后为 ed25519）。

本地源目录已随仓库迁移全部失效（旧路径 D:\\制作网页\\错题教练 已删除），
现改为环境变量注入、缺失即报错，不再有指向旧磁盘位置的硬编码：
    COACH_SRC_BACKEND   后端 app 目录（待上传的 backend/app）
    COACH_SRC_WEBDIST   前端构建产物目录（student-web/dist）
    COACH_SRC_SCRIPT    generate_scenario_bank.py 路径
"""
import os
import sys
import io
import zipfile
import paramiko
from paramiko import ECDSAKey, Ed25519Key, RSAKey

HOST = os.environ.get("COACH_SSH_HOST", "118.25.44.126")
USER = os.environ.get("COACH_SSH_USER", "ubuntu")
KEY_PATH = os.environ.get("COACH_SSH_KEY", "")
if not KEY_PATH:
    raise SystemExit(
        "缺少环境变量 COACH_SSH_KEY（SSH 私钥路径，凭据不得写入代码；"
        "原硬编码路径 D:\\制作网页\\错题教练\\AIjiaolian.pem 已随旧副本删除，"
        "私钥现由用户保管在仓库外）"
    )
DEPLOY_DIR = "/home/ubuntu/coach"

SRC_BACKEND = os.environ.get("COACH_SRC_BACKEND", "")
SRC_WEBDIST = os.environ.get("COACH_SRC_WEBDIST", "")
SRC_SCRIPT = os.environ.get("COACH_SRC_SCRIPT", "")


def _load_pkey(path: str):
    """按私钥内容自动识别类型（ed25519 / rsa / ecdsa）。

    2026-10-15 密钥轮换后新密钥为 ed25519（OpenSSH 新格式），
    原 paramiko.RSAKey 写死 RSA 会直接解析失败，故改为依次尝试。
    """
    errors = []
    for cls in (Ed25519Key, RSAKey, ECDSAKey):
        try:
            return cls.from_private_key_file(path)
        except Exception as e:  # noqa: BLE001 - 收集全部失败原因后统一报错
            errors.append(f"{cls.__name__}: {e}")
    raise SystemExit(
        f"无法加载私钥 {path}（仅支持未加密的 ed25519/rsa/ecdsa 私钥）："
        + "; ".join(errors)
    )


def get_client():
    pk = _load_pkey(KEY_PATH)
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
    if not (SRC_BACKEND and SRC_WEBDIST and SRC_SCRIPT):
        raise SystemExit(
            "缺少本地源目录环境变量：COACH_SRC_BACKEND / COACH_SRC_WEBDIST / "
            "COACH_SRC_SCRIPT（旧硬编码路径已失效，见模块 docstring）"
        )
    print("connecting...")
    client = get_client()
    sftp = client.open_sftp()
    print("connected")

    # 1. 上传后端增量
    print("=== backend zip ===")
    upload_zip(sftp, SRC_BACKEND, "/tmp/gamify_backend.zip")
    exec_cmd(client, f"cd {DEPLOY_DIR} && unzip -o /tmp/gamify_backend.zip -d backend/app/")

    # 2. 上传前端构建产物
    print("=== frontend dist zip ===")
    upload_zip(sftp, SRC_WEBDIST, "/tmp/gamify_web.zip")
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
    sftp.put(SRC_SCRIPT, "/tmp/generate_scenario_bank.py")
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
