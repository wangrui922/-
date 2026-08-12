"""T02 账号与数据层冒烟测试。

覆盖：随机验证码登录、未登录 401、年级越界 400、合法年级写入生效、限流。
依赖 conftest 的 :memory: SQLite（StaticPool 共享连接，跨请求持久）。
"""
from __future__ import annotations

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

    # 3) 年级越界(0/7) 必须 400
    bad = client.put("/api/student/profile", headers=headers, json={"grade": 7, "goal": "考满分"})
    assert bad.status_code == 400, bad.text

    # 4) 合法年级(3) 写入并回读一致
    ok = client.put("/api/student/profile", headers=headers, json={"grade": 3, "goal": "考满分"})
    assert ok.status_code == 200, ok.text
    again = client.get("/api/student/profile", headers=headers)
    assert again.json()["data"]["grade"] == 3


def test_login_returns_tokens(client):
    data = _login(client)
    assert "access" in data and "refresh" in data
    assert isinstance(data["access"], str) and len(data["access"]) > 10


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
