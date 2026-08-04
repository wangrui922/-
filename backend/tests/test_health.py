"""health 接口与 security 工具单测。"""

from __future__ import annotations

import pytest

from app.core.response import ApiError, CODE_UNAUTHORIZED
from app.core.security import (
    ROLE_STUDENT,
    create_access_token,
    verify_token,
)


# ---------------------------------------------------------------- health
def test_health_ok(client):
    """GET /api/health 返回 code=0 且 data.db 非空。"""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["msg"] == "ok"
    assert body["data"]["db"] in ("sqlite", "postgres")
    assert body["data"]["db"]  # 非空


# ------------------------------------------------------------ security
def test_security_token_roundtrip():
    """create_token 后能 verify_token 解出相同 sub / sid。"""
    token = create_access_token(sub="42", role=ROLE_STUDENT, sid="7")
    payload = verify_token(token)
    assert payload["sub"] == "42"
    assert payload.get("sid") == "7"
    assert payload.get("role") == ROLE_STUDENT


def test_security_invalid_token_raises_401():
    """非法 token 抛 401 业务异常。"""
    with pytest.raises(ApiError) as exc_info:
        verify_token("this.is.not.a.valid.jwt")
    assert exc_info.value.code == CODE_UNAUTHORIZED


def test_security_missing_token_raises_401():
    """空 token 同样抛 401。"""
    with pytest.raises(ApiError) as exc_info:
        verify_token("")
    assert exc_info.value.code == CODE_UNAUTHORIZED
