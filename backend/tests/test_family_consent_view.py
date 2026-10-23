"""家长端孩子监护人同意三态测试（V6.5 合规闸门 · B02 体验收尾）。

覆盖 ``GET /api/family/children/{student_id}/consent``：
    * 三态口径：从未同意（全空）/ 当前版本已同意 / 旧版本同意（版本失效）；
    * ``currentVersion`` 跟随 ``CONSENT_VERSION`` 配置（规则修订后展示口径同步）；
    * 鉴权与越权：未登录 401、学生角色 403、未绑定 403；
    * 读操作不锁：门控开启 + 孩子未同意时，本接口仍 200（同请求内练习类
      POST 仍 403，证明门控确实生效、只是不拦同意状态查询）。

测试铁律：**绝不设置 DATABASE_URL 环境变量**（conftest 默认 :memory:，
设了会触发文件库闸门报错退出）。门控开关 / 版本用 monkeypatch 替换
``system_config._cache`` 实现（与 test_consent_gate.py 同一手法）。
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from t03_helpers import assert_fail, assert_ok

#: 门控当前版本（与 ``system_config.DEFAULTS`` 的 ``CONSENT_VERSION`` 对齐）
GATE_VERSION = "v1"

#: 旧版本同意（版本失效反例）
STALE_VERSION = "v0"


# ====================================================================  fixtures
@pytest.fixture
def consent_gate(monkeypatch):
    """监护人同意门控开关 / 版本夹具（替换进程内配置缓存，用例结束自动还原）。"""

    def _apply(*, enabled: bool, version: str = GATE_VERSION) -> None:
        from app.core import system_config

        monkeypatch.setattr(
            system_config,
            "_cache",
            {"CONSENT_GATE_ENABLED": enabled, "CONSENT_VERSION": version},
        )

    yield _apply


# ====================================================================  helpers
def _register(client, phone: str, role: str) -> Dict[str, Any]:
    """发码 → 取随机码 → 按角色注册，返回 data 字典。"""
    r = client.post("/api/auth/code", json={"phone": phone})
    assert r.status_code == 200, r.text
    dev_code = r.json()["data"]["devCode"]
    r = client.post(
        "/api/auth/register",
        json={"phone": phone, "code": dev_code, "role": role},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _auth(token: str) -> Dict[str, str]:
    """带 Authorization 头的 dict。"""
    return {"Authorization": f"Bearer {token}"}


def _bind(client, parent_token: str, child_phone: str) -> str:
    """家长按手机号绑定孩子，返回 studentId。"""
    r = client.post(
        "/api/family/bind",
        json={"phone": child_phone, "relation": "father"},
        headers=_auth(parent_token),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["studentId"]


def _give_consent(client, token: str, *, version: str = GATE_VERSION) -> None:
    """以孩子本人账号提交一次指定版本的监护人同意（留痕落在孩子 user_id 上）。"""
    r = client.post(
        "/api/compliance/guardian-consent",
        json={
            "guardianPhone": "13911112222",
            "consentVersion": version,
            "agreed": True,
        },
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text


def _bound_pair(client, new_phone) -> Dict[str, str]:
    """注册家长 + 学生并完成绑定，返回 {parent_token, child_token, student_id}。"""
    child_phone = new_phone()
    child = _register(client, child_phone, "student")
    parent = _register(client, new_phone(), "parent")
    student_id = _bind(client, parent["access"], child_phone)
    return {
        "parent_token": parent["access"],
        "child_token": child["access"],
        "student_id": student_id,
    }


def _get_consent(client, parent_token: str, student_id: str) -> Dict[str, Any]:
    """家长查询孩子同意三态，返回 data。"""
    r = client.get(
        f"/api/family/children/{student_id}/consent",
        headers=_auth(parent_token),
    )
    return assert_ok(r)


# ====================================================================  三态口径
def test_child_consent_never_agreed(client, new_phone) -> None:
    """状态一：从未同意 → granted=false 且时间/版本为空，currentVersion=v1。"""
    pair = _bound_pair(client, new_phone)

    data = _get_consent(client, pair["parent_token"], pair["student_id"])
    assert data == {
        "granted": False,
        "agreedAt": None,
        "consentVersion": None,
        "currentVersion": GATE_VERSION,
    }


def test_child_consent_granted_current_version(client, new_phone) -> None:
    """状态二：当前版本（v1）已同意 → granted=true，时间/版本齐全。"""
    pair = _bound_pair(client, new_phone)
    _give_consent(client, pair["child_token"], version=GATE_VERSION)

    data = _get_consent(client, pair["parent_token"], pair["student_id"])
    assert data["granted"] is True
    assert data["consentVersion"] == GATE_VERSION
    assert data["agreedAt"], "agreedAt 不应为空"
    assert data["currentVersion"] == GATE_VERSION


def test_child_consent_stale_version(client, new_phone) -> None:
    """状态三：只有旧版本（v0）同意 → granted=false 但版本非空（版本失效）。"""
    pair = _bound_pair(client, new_phone)
    _give_consent(client, pair["child_token"], version=STALE_VERSION)

    data = _get_consent(client, pair["parent_token"], pair["student_id"])
    assert data["granted"] is False
    assert data["consentVersion"] == STALE_VERSION
    assert data["agreedAt"], "旧同意时间应回显，供家长端展示「上次同意」"
    assert data["currentVersion"] == GATE_VERSION


def test_child_consent_current_version_follows_config(
    client, consent_gate, new_phone
) -> None:
    """规则修订（CONSENT_VERSION=v2）后：旧 v1 同意即失效，currentVersion 同步为 v2。"""
    consent_gate(enabled=False, version="v2")
    pair = _bound_pair(client, new_phone)
    _give_consent(client, pair["child_token"], version=GATE_VERSION)

    data = _get_consent(client, pair["parent_token"], pair["student_id"])
    assert data["granted"] is False
    assert data["consentVersion"] == GATE_VERSION
    assert data["currentVersion"] == "v2"


# ====================================================================  鉴权与越权
def test_child_consent_requires_login(client, new_phone) -> None:
    """未登录：401。"""
    pair = _bound_pair(client, new_phone)
    r = client.get(f"/api/family/children/{pair['student_id']}/consent")
    assert_fail(r, 401)


def test_child_consent_rejects_student_role(client, new_phone) -> None:
    """学生角色不允许查看（family 模块全线限定 parent）。"""
    pair = _bound_pair(client, new_phone)
    r = client.get(
        f"/api/family/children/{pair['student_id']}/consent",
        headers=_auth(pair["child_token"]),
    )
    assert_fail(r, 403)


def test_child_consent_rejects_unbound_parent(client, new_phone) -> None:
    """家长 B 查家长 A 绑定的孩子：403 越权。"""
    pair = _bound_pair(client, new_phone)
    other_parent = _register(client, new_phone(), "parent")

    r = client.get(
        f"/api/family/children/{pair['student_id']}/consent",
        headers=_auth(other_parent["access"]),
    )
    assert_fail(r, 403)


# ====================================================================  读操作不锁
def test_child_consent_readable_when_gate_enabled(
    client, consent_gate, new_phone
) -> None:
    """门控开启 + 孩子未同意：同意状态查询仍 200（读操作不挂门控）。

    同用例内对照：练习类 POST 仍被门控 403 且话术固定——证明门控确实
    生效，只是刻意不拦同意状态查询本身。
    """
    from app.core.deps import CONSENT_GATE_DENIED_MSG

    consent_gate(enabled=True)
    pair = _bound_pair(client, new_phone)

    data = _get_consent(client, pair["parent_token"], pair["student_id"])
    assert data["granted"] is False

    r = client.post(
        "/api/practice/session",
        json={},
        headers=_auth(pair["child_token"]),
    )
    assert_fail(r, 403)
    assert r.json()["msg"] == CONSENT_GATE_DENIED_MSG
