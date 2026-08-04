"""T02 账号与数据层冒烟测试。

覆盖：dev 万能码登录、未登录 401、年级越界 400、合法年级写入生效。
依赖 conftest 的 :memory: SQLite（StaticPool 共享连接，跨请求持久）。
"""
from __future__ import annotations


def _login(client):
    r = client.post("/api/auth/login", json={"phone": "13800000000", "code": "000000"})
    assert r.status_code == 200, r.text
    return r.json()["data"]


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
