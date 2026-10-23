"""family 接口 camelCase 契约守卫（2026-10-23 修复回归）。

背景：``family_service`` 返回 raw dict（snake_case），而前端
``src/api/family.ts`` 的类型与 JSDoc 全按 camelCase 读。此前
``list_children`` 返回 ``student_id`` 前端读 ``studentId``——
家长端孩子列表点击跳转拿到 ``undefined``，整条家长链路静默坏掉，
且旧测试把错误契约固化（断言 ``student_id``）。

本文件锁：**所有 family 出口的响应体不得出现 snake_case key**。
判据 = 响应 JSON 递归扫描 key，命中 ``^[a-z]+(_[a-z]+)+$`` 即红。
"""

from __future__ import annotations

import re
from typing import Any

_SNAKE_KEY = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")


def _snake_keys(value: Any, path: str = "$") -> list[str]:
    """递归收集响应体里的 snake_case key 路径。"""
    bad: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            here = f"{path}.{k}"
            if isinstance(k, str) and _SNAKE_KEY.match(k):
                bad.append(here)
            bad.extend(_snake_keys(v, here))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            bad.extend(_snake_keys(item, f"{path}[{i}]"))
    return bad


def _register(client, phone: str, role: str) -> dict:
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


def test_family_endpoints_never_return_snake_keys(client, new_phone):
    """绑定 → 列表 → 概览 → 错题 → 记录 → 掌握度 → 权益：全链路无 snake key。"""
    parent = _register(client, new_phone(), "parent")
    child = _register(client, new_phone(), "student")
    child_sid = child["user"]["studentId"]
    headers = _auth(parent["access"])

    r = client.post(
        "/api/family/bind",
        json={"phone": child["user"]["phone"] if "phone" in child["user"] else None, "relation": "father"},
        headers=headers,
    )
    # bind 入参要手机号——从注册响应的 user 里取不到就跳过绑定断言，
    # 直接用已建学生走需要绑定关系的接口前先补绑定。
    if r.status_code != 200:
        # 从注册请求反推：用新号重新注册孩子拿到手机号
        child_phone = new_phone()
        child2 = _register(client, child_phone, "student")
        child_sid = child2["user"]["studentId"]
        r = client.post(
            "/api/family/bind",
            json={"phone": child_phone, "relation": "father"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
    assert r.status_code == 200, r.text

    endpoints = [
        ("GET", "/api/family/children"),
        ("GET", f"/api/family/children/{child_sid}/summary"),
        ("GET", f"/api/family/children/{child_sid}/mistakes"),
        ("GET", f"/api/family/children/{child_sid}/attempts"),
        ("GET", f"/api/family/children/{child_sid}/mastery"),
        ("GET", f"/api/family/children/{child_sid}/entitlement"),
        ("GET", f"/api/family/children/{child_sid}/consent"),
    ]
    for method, url in endpoints:
        resp = client.request(method, url, headers=headers)
        assert resp.status_code == 200, f"{url} -> {resp.status_code}: {resp.text}"
        bad = _snake_keys(resp.json())
        assert not bad, f"{url} 返回 snake_case key（前端读不到）：{bad[:5]}"
