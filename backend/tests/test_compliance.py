"""V6.5 T13 · 监护人同意留痕测试（60 人内测合规闸门）。

覆盖：
    * 迁移双方言：022 的 PG / SQLite 两份必须同批存在，关键列齐全；
    * SQLite DDL 可执行：真跑一遍 022_sqlite（含 users 前置表），
      验证建表 + UNIQUE(user_id, consent_version) + 索引 + 外键真实生效；
    * 业务：首次同意落库（含 ip/user_agent/channel 取证字段）、
      重复同意幂等、agreed=false 拒绝记录、未登录 401；
    * 查状态三态：从未同意 / 已同意 / 版本不同；
    * 角色：学生本人与家长均可提交，admin 403。

测试铁律：**绝不设置 DATABASE_URL 环境变量**（conftest 默认走 :memory:，
设了会触发文件库闸门报错退出）。
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest
from sqlalchemy import func, select

from app.models.compliance import GuardianConsent

#: migrations 目录（tests/ 的上一级是 backend/）
MIGRATIONS_DIR: Path = Path(__file__).resolve().parents[1] / "migrations"

#: 测试固定同意版本（与内测版《儿童个人信息处理规则》对应）
CONSENT_VERSION = "v1-internal-test"


# ====================================================================  helpers
def _login_student(client, phone: str) -> Dict[str, Any]:
    """发码 → 取随机码 → 登录（首次登录自动建 student），返回 data 字典。"""
    r = client.post("/api/auth/code", json={"phone": phone})
    assert r.status_code == 200, r.text
    dev_code = r.json()["data"]["devCode"]
    r = client.post("/api/auth/login", json={"phone": phone, "code": dev_code})
    assert r.status_code == 200, r.text
    return r.json()["data"]


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


def _consent_rows(db, user_id: str) -> List[GuardianConsent]:
    """取某账号的全部同意记录（按账号过滤，避免会话级共享库的跨用例干扰）。"""
    return list(
        db.execute(
            select(GuardianConsent).where(GuardianConsent.user_id == user_id)
        )
        .scalars()
        .all()
    )


def _consent_count(db, user_id: str) -> int:
    """某账号的同意记录条数。"""
    return int(
        db.execute(
            select(func.count())
            .select_from(GuardianConsent)
            .where(GuardianConsent.user_id == user_id)
        ).scalar_one()
    )


def _post_consent(client, token: str, *, agreed: bool = True, version: str = CONSENT_VERSION):
    """提交一次监护人同意的便捷封装。"""
    return client.post(
        "/api/compliance/guardian-consent",
        json={
            "guardianPhone": "13911112222",
            "consentVersion": version,
            "agreed": agreed,
        },
        headers=_auth(token),
    )


# ====================================================================  双方言迁移
def test_migration_pair_exists_with_columns() -> None:
    """022 双方言迁移必须同批存在，关键列齐全。"""
    for filename in (
        "022_guardian_consents.sql",
        "022_guardian_consents_sqlite.sql",
    ):
        sql = (MIGRATIONS_DIR / filename).read_text(encoding="utf-8").lower()
        assert "create table" in sql, f"{filename} 未建表"
        for token in (
            "user_id",
            "guardian_phone",
            "consent_version",
            "agreed_at",
            "channel",
            "ip",
            "user_agent",
            "created_at",
        ):
            assert token in sql, f"{filename} 缺列 {token}"


def test_sqlite_migration_actually_creates_table() -> None:
    """022 SQLite DDL 真跑一遍：建表 + UNIQUE + 索引 + 外键生效。"""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        # 前置表（外键引用目标）
        conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, phone TEXT)")
        conn.executescript(
            (MIGRATIONS_DIR / "022_guardian_consents_sqlite.sql").read_text(
                encoding="utf-8"
            )
        )

        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "guardian_consents" in tables, "DDL 未建出表 guardian_consents"

        uid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO users (id, phone) VALUES (?, ?)", (uid, "13900000001")
        )
        conn.execute(
            "INSERT INTO guardian_consents "
            "(id, user_id, consent_version, agreed_at, created_at) "
            "VALUES (?, ?, 'v1', '2026-01-01', '2026-01-01')",
            (str(uuid.uuid4()), uid),
        )
        # UNIQUE(user_id, consent_version)：同账号同版本重复 → IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO guardian_consents "
                "(id, user_id, consent_version, agreed_at, created_at) "
                "VALUES (?, ?, 'v1', '2026-01-01', '2026-01-01')",
                (str(uuid.uuid4()), uid),
            )
        # 同账号不同版本允许
        conn.execute(
            "INSERT INTO guardian_consents "
            "(id, user_id, consent_version, agreed_at, created_at) "
            "VALUES (?, ?, 'v2', '2026-01-02', '2026-01-02')",
            (str(uuid.uuid4()), uid),
        )
        # 外键生效：不存在的 user_id → IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO guardian_consents "
                "(id, user_id, consent_version, agreed_at, created_at) "
                "VALUES (?, ?, 'v1', '2026-01-01', '2026-01-01')",
                (str(uuid.uuid4()), str(uuid.uuid4())),
            )
        # user_id 索引存在
        indexes = {
            row[1] for row in conn.execute("PRAGMA index_list('guardian_consents')")
        }
        assert "idx_gc_user_id" in indexes, f"缺 user_id 索引，实际：{indexes}"
    finally:
        conn.close()


# ====================================================================  业务闭环
def test_first_consent_records_with_forensics(client, db, new_phone) -> None:
    """首次同意：落库一行，agreed/agreedAt/consentVersion 与取证字段齐全。"""
    student = _login_student(client, new_phone())
    user_id: str = student["user"]["userId"]

    r = client.post(
        "/api/compliance/guardian-consent",
        json={
            "guardianPhone": "13911112222",
            "consentVersion": CONSENT_VERSION,
            "agreed": True,
        },
        headers={
            **_auth(student["access"]),
            "User-Agent": "pytest-agent/1.0",
            "X-Client-Channel": "web",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["agreed"] is True
    assert data["consentVersion"] == CONSENT_VERSION
    assert data["agreedAt"], "agreedAt 不应为空"

    # 落库一行，且 ip / user_agent / channel 取证字段齐全
    rows = _consent_rows(db, user_id)
    assert len(rows) == 1
    row = rows[0]
    assert str(row.user_id) == user_id
    assert row.guardian_phone == "13911112222"
    assert row.consent_version == CONSENT_VERSION
    assert row.user_agent == "pytest-agent/1.0"
    assert row.channel == "web"
    assert row.ip, "ip 应留痕"


def test_repeat_consent_is_idempotent(client, db, new_phone) -> None:
    """重复同意幂等：同一 user+version 只一条，agreedAt 不变，不报错。"""
    student = _login_student(client, new_phone())
    user_id: str = student["user"]["userId"]

    r1 = _post_consent(client, student["access"])
    r2 = _post_consent(client, student["access"])
    assert r1.status_code == r2.status_code == 200
    d1, d2 = r1.json()["data"], r2.json()["data"]
    assert d1["agreedAt"] == d2["agreedAt"], "重复提交应返回已有记录，不得改写 agreedAt"

    assert _consent_count(db, user_id) == 1


def test_agreed_false_is_rejected(client, db, new_phone) -> None:
    """agreed=false 拒绝记录：400 且不落库。"""
    student = _login_student(client, new_phone())
    user_id: str = student["user"]["userId"]

    r = _post_consent(client, student["access"], agreed=False)
    assert r.status_code == 400, r.text

    assert _consent_count(db, user_id) == 0


def test_consent_requires_login(client, new_phone) -> None:
    """未登录 401（POST 与 GET 均需登录）。"""
    payload = {
        "guardianPhone": "13911112222",
        "consentVersion": CONSENT_VERSION,
        "agreed": True,
    }
    r = client.post("/api/compliance/guardian-consent", json=payload)
    assert r.status_code == 401, r.text

    r = client.get("/api/compliance/guardian-consent")
    assert r.status_code == 401, r.text


def test_status_never_agreed(client, new_phone) -> None:
    """状态一：从未同意 → agreed=false 且其余字段为 None。"""
    student = _login_student(client, new_phone())
    r = client.get(
        "/api/compliance/guardian-consent", headers=_auth(student["access"])
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {
        "agreed": False,
        "agreedAt": None,
        "consentVersion": None,
    }


def test_status_agreed_and_other_version(client, new_phone) -> None:
    """状态二/三：已同意（缺省查最新 + 显式查该版本均 agreed）；版本不同 → 未同意态。"""
    student = _login_student(client, new_phone())
    headers = _auth(student["access"])
    assert _post_consent(client, student["access"]).status_code == 200

    # 已同意（缺省查最新一条）
    r = client.get("/api/compliance/guardian-consent", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["agreed"] is True
    assert data["consentVersion"] == CONSENT_VERSION
    assert data["agreedAt"]

    # 显式查已同意的版本
    r = client.get(
        f"/api/compliance/guardian-consent?consent_version={CONSENT_VERSION}",
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["agreed"] is True

    # 版本不同 → 未同意态
    r = client.get(
        "/api/compliance/guardian-consent?consent_version=v2-next",
        headers=headers,
    )
    assert r.status_code == 200
    other = r.json()["data"]
    assert other["agreed"] is False
    assert other["agreedAt"] is None
    assert other["consentVersion"] is None


def test_parent_role_allowed(client, new_phone) -> None:
    """家长角色可提交/查询监护人同意。"""
    parent = _register(client, new_phone(), "parent")
    assert _post_consent(client, parent["access"]).status_code == 200

    r = client.get(
        "/api/compliance/guardian-consent", headers=_auth(parent["access"])
    )
    assert r.status_code == 200
    assert r.json()["data"]["agreed"] is True


def test_admin_role_forbidden(client, make_token) -> None:
    """admin 角色 403（仅学生本人或家长可操作）。"""
    token = make_token(sub=str(uuid.uuid4()), role="admin", sid=None)
    r = _post_consent(client, token)
    assert r.status_code == 403, r.text

    r = client.get("/api/compliance/guardian-consent", headers=_auth(token))
    assert r.status_code == 403, r.text
