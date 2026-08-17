"""家长-孩子绑定（V4.2 商业核心）闭环测试。

覆盖：
    * 家长注册（role=parent）后绑定孩子（按手机号）；
    * 家长列出 / 查看已绑定孩子的概览（画像 + 周报 + 雷达）；
    * 学生角色无法访问 family 接口（403）；
    * 家长 A 无法查看家长 B 绑定的孩子（403 越权防护）。
"""

from __future__ import annotations


def _register(client, phone: str, role: str) -> dict:
    """发码 → 取随机码 → 按角色注册，返回 ``data`` 字典。"""
    r = client.post("/api/auth/code", json={"phone": phone})
    assert r.status_code == 200, r.text
    dev_code = r.json()["data"]["devCode"]
    r = client.post(
        "/api/auth/register",
        json={"phone": phone, "code": dev_code, "role": role},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_parent_bind_child_and_view_summary(client, new_phone):
    parent_phone = new_phone()
    child_phone = new_phone()

    parent = _register(client, parent_phone, "parent")
    child = _register(client, child_phone, "student")
    child_sid = child["user"]["studentId"]
    parent_token = parent["access"]

    # 家长角色不应带 studentId
    assert parent["user"]["studentId"] is None

    # 绑定孩子
    r = client.post(
        "/api/family/bind",
        json={"phone": child_phone, "relation": "father"},
        headers=_auth(parent_token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["studentId"] == child_sid

    # 列出孩子
    r = client.get("/api/family/children", headers=_auth(parent_token))
    assert r.status_code == 200
    children = r.json()["data"]
    assert len(children) == 1
    assert children[0]["student_id"] == child_sid

    # 家长视角查看孩子概览
    r = client.get(
        f"/api/family/children/{child_sid}/summary",
        headers=_auth(parent_token),
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["student_id"] == child_sid
    assert "profile" in data and "weekly" in data and "radar" in data


def test_student_cannot_access_family(client, new_phone):
    child = _register(client, new_phone(), "student")
    r = client.get("/api/family/children", headers=_auth(child["access"]))
    assert r.status_code == 403


def test_parent_cannot_view_other_child(client, new_phone):
    # 家长 A 绑孩子 A
    child_a_phone = new_phone()
    child_a = _register(client, child_a_phone, "student")
    parent_a = _register(client, new_phone(), "parent")
    sid_a = child_a["user"]["studentId"]
    r = client.post(
        "/api/family/bind",
        json={"phone": child_a_phone, "relation": "father"},
        headers=_auth(parent_a["access"]),
    )
    assert r.status_code == 200

    # 家长 B 绑孩子 B
    child_b_phone = new_phone()
    child_b = _register(client, child_b_phone, "student")
    parent_b = _register(client, new_phone(), "parent")
    sid_b = child_b["user"]["studentId"]
    r = client.post(
        "/api/family/bind",
        json={"phone": child_b_phone, "relation": "mother"},
        headers=_auth(parent_b["access"]),
    )
    assert r.status_code == 200

    # 家长 B 读孩子 A → 403 越权
    r = client.get(
        f"/api/family/children/{sid_a}/summary",
        headers=_auth(parent_b["access"]),
    )
    assert r.status_code == 403

    # 家长 A 读孩子 A → 200
    r = client.get(
        f"/api/family/children/{sid_a}/summary",
        headers=_auth(parent_a["access"]),
    )
    assert r.status_code == 200

    # 家长 B 读自己孩子 B → 200
    r = client.get(
        f"/api/family/children/{sid_b}/summary",
        headers=_auth(parent_b["access"]),
    )
    assert r.status_code == 200


def test_bind_missing_child_returns_404(client, new_phone):
    parent = _register(client, new_phone(), "parent")
    r = client.post(
        "/api/family/bind",
        json={"phone": new_phone(), "relation": "father"},
        headers=_auth(parent["access"]),
    )
    assert r.status_code == 404
