"""T02 账号与数据层冒烟测试。

覆盖：随机验证码登录、未登录 401、1-12 年级边界、学段同步、限流。
依赖 conftest 的 :memory: SQLite（StaticPool 共享连接，跨请求持久）。
"""
from __future__ import annotations

import hashlib

from sqlalchemy import text

from conftest import login_with_code


def _login(client):
    """随机验证码登录，返回 ``data``。"""
    return login_with_code(client)


def test_profile_requires_auth(client):
    assert client.get("/api/student/profile").status_code == 401


def test_auth_flow_and_grade_validation(client):
    # 1) 未登录不能拿档案（上面已验，这里再确认一次登录链路）
    data = _login(client)
    token = data["access"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2) 登录后可读档案
    r = client.get("/api/student/profile", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["code"] == 0

    # 3) 小学、初中、高中都能保存，并同步正确学段
    for grade, stage in ((3, "primary"), (8, "junior"), (12, "senior")):
        updated = client.put(
            "/api/student/profile",
            headers=headers,
            json={"grade": grade, "goal": "考满分"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["data"]["grade"] == grade
        assert updated.json()["data"]["stage"] == stage

    # 4) 年级边界以 1-12 为准，0 和 13 必须 400
    for grade in (0, 13):
        bad = client.put(
            "/api/student/profile",
            headers=headers,
            json={"grade": grade, "goal": "考满分"},
        )
        assert bad.status_code == 400, bad.text

    # 5) 高中年级写入后可回读
    again = client.get("/api/student/profile", headers=headers)
    assert again.json()["data"]["grade"] == 12
    assert again.json()["data"]["stage"] == "senior"


def test_login_returns_tokens(client):
    data = _login(client)
    assert "access" in data and "refresh" in data
    assert isinstance(data["access"], str) and len(data["access"]) > 10


def test_activation_code_creates_a_student_account(client):
    """首次输入有效激活码应创建学生账号，而不是绕过登录直接进入。"""
    from app.db import engine

    code = "YY-7K3P-9M2R-4X8D-A6FQ"
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS activation_codes (
                    id CHAR(36) PRIMARY KEY,
                    code_digest VARCHAR(64) NOT NULL UNIQUE,
                    label VARCHAR(50),
                    user_id CHAR(36) UNIQUE REFERENCES users(id) ON DELETE SET NULL,
                    activated_at TEXT,
                    expires_at TEXT,
                    revoked_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO activation_codes (
                    id, code_digest, label, created_at, updated_at
                ) VALUES (
                    '00000000-0000-0000-0000-000000000801', :digest, 'beta-01',
                    '2026-08-20T00:00:00+00:00', '2026-08-20T00:00:00+00:00'
                )
                """
            ),
            {"digest": digest},
        )

    response = client.post(
        "/api/auth/activation/activate",
        json={"code": code, "nickname": "小明", "pin": "246810"},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["isNewUser"] is True
    assert data["user"]["nickname"] == "小明"
    assert data["user"]["studentId"]
    assert data["access"]


# ================================================================ P0-3 新增
def test_send_code_returns_dev_code(client):
    """dev 环境 /api/auth/code 回带随机验证码。"""
    r = client.post("/api/auth/code", json={"phone": "13800000000"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["sent"] is True
    assert data["devCode"] is not None
    assert len(data["devCode"]) == 6


def test_login_with_wrong_code_fails(client):
    """错误验证码登录 → 401。"""
    # 先发码（保证有验证码存在）
    client.post("/api/auth/code", json={"phone": "13800000000"})
    # 用错误码登录
    r = client.post("/api/auth/login", json={"phone": "13800000000", "code": "999999"})
    assert r.status_code == 401


def test_login_without_sending_code_fails(client):
    """未发码直接登录 → 401。"""
    r = client.post("/api/auth/login", json={"phone": "13800000000", "code": "123456"})
    assert r.status_code == 401


def test_send_code_cooldown(client):
    """60s 内重复发码 → 429。"""
    phone = "13700000001"
    r1 = client.post("/api/auth/code", json={"phone": phone})
    assert r1.status_code == 200
    r2 = client.post("/api/auth/code", json={"phone": phone})
    assert r2.status_code == 429


def test_send_code_hourly_limit(client):
    """1h 内发码超过 5 次 → 429（第 6 次被限流）。"""
    from app.core.ratelimit import code_send_limiter, SEND_CODE_COOLDOWN_SEC
    # 预填充 5 条 cooldown 记录（绕过 60s 限制）
    for _ in range(5):
        code_send_limiter.check(f"cooldown:13700000002", 999, SEND_CODE_COOLDOWN_SEC)
    r = client.post("/api/auth/code", json={"phone": "13700000002"})
    # cooldown 不会拦（999 上限），但 hourly 检查会让第 6 次失败
    # 实际上 cooldown 检查也会通过（999 上限），但 hourly 上限 5 会拦截
    assert r.status_code == 429
