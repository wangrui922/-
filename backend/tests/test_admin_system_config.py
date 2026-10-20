"""后台系统配置管理端点单测（GET/PUT /admin/configs、POST /configs/reset）。

覆盖（与 #49 任务书验收逐条对应）：
    1. **端点鉴权**：无 token 401、学生角色 403、admin 200；
    2. **列表口径**：DEFAULTS 全量键在列（含 V6.5 三个新键）、DB 无记录
       回退默认值、说明文案非空（DEFAULTS 加键不同步文案会被抓）；
    3. **写入门禁**：未知键 400、非数字 400、bool 白名单外 400、
       nan/inf 400，且被拒后 DB 保持原值（不写坏库）；
    4. **缓存失效**：PUT 成功后 ``get_config`` 直接读到新值——锁死
       ``reload_config()`` 被调用（这类接口最静的坑）；
    5. **upsert 双路径**：首建（无行）与更新（有行）都写对；
    6. **reset**：全部键回默认值（含缓存）。

口径备注：本文件**不设** ``DATABASE_URL``（conftest 基线闸门，走会话级
共享内存 SQLite）。configs 是全局单例状态、跨用例会互相污染，因此每条
用例自备所需键状态，不假设全局默认。
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.core.system_config import DEFAULTS, get_config
from app.models.system_config import SystemConfig


# ==================================================================== 工具
def admin_headers(make_token) -> dict:
    """admin 角色的 Authorization 头。"""
    return {"Authorization": f"Bearer {make_token(sub='1', role='admin')}"}


def student_headers(make_token) -> dict:
    """学生角色的 Authorization 头（越权用例用）。"""
    return {"Authorization": f"Bearer {make_token(sub='2', role='student', sid='2')}"}


def get_configs(client, make_token):
    """请求配置列表，返回 {key: item}。"""
    r = client.get("/api/admin/configs", headers=admin_headers(make_token))
    assert r.status_code == 200, r.text
    assert r.json()["code"] == 0
    return {it["key"]: it for it in r.json()["data"]["items"]}


def put_config(client, make_token, key: str, value: str):
    """PUT 单个配置项。"""
    return client.put(
        f"/api/admin/configs/{key}",
        json={"value": value},
        headers=admin_headers(make_token),
    )


def clear_all_configs(db) -> None:
    """清空配置表（构造「DB 无记录」初始态）。"""
    db.execute(delete(SystemConfig))
    db.commit()


# ==================================================================== 鉴权
def test_configs_endpoints_require_admin(client, make_token):
    """三个端点均只放行 admin：无 token 401、学生 403。"""
    assert client.get("/api/admin/configs").status_code == 401
    r = client.get("/api/admin/configs", headers=student_headers(make_token))
    assert r.status_code == 403

    r = client.put(
        "/api/admin/configs/STUCK_TO_L2",
        json={"value": "9"},
        headers=student_headers(make_token),
    )
    assert r.status_code == 403

    r = client.post("/api/admin/configs/reset", headers=student_headers(make_token))
    assert r.status_code == 403

    # admin 放行
    assert client.get("/api/admin/configs", headers=admin_headers(make_token)).status_code == 200


# ==================================================================== 列表口径
def test_configs_list_full_keys_and_fallback(client, db, make_token):
    """DB 无记录时：DEFAULTS 全量键在列、回退默认值、说明文案非空。"""
    clear_all_configs(db)

    items = get_configs(client, make_token)

    # 1) 键集合与 DEFAULTS 一致（V6.5 三键必须在列）
    assert set(items) == set(DEFAULTS.keys())
    for key in ("CONSENT_GATE_ENABLED", "CONSENT_VERSION", "CONFIDENCE_RETHRESHOLD"):
        assert key in items

    # 2) 无 DB 记录 → 展示值 = 代码默认值
    for key, (default_str, _cast) in DEFAULTS.items():
        assert items[key]["value"] == default_str, key
        assert items[key]["default_value"] == default_str, key
        # 3) 说明文案非空（DEFAULTS 加键忘同步文案即红）
        assert items[key]["description"], key


# ==================================================================== 写入门禁
def test_update_rejects_bad_values_and_keeps_db(client, db, make_token):
    """未知键 / 非数字 / bool 白名单外 / nan / inf → 400，DB 保持原值。"""
    clear_all_configs(db)

    # 未知键
    r = put_config(client, make_token, "NOT_A_KEY", "1")
    assert r.status_code == 400
    assert r.json()["code"] == 400

    # int 键给非数字
    r = put_config(client, make_token, "STUCK_TO_L2", "abc")
    assert r.status_code == 400

    # float 键给非数字
    r = put_config(client, make_token, "REPEAT_SIMILARITY", "高")
    assert r.status_code == 400

    # bool 键白名单外（"maybe" 若放行会存得进、读出来是 False）
    r = put_config(client, make_token, "CONSENT_GATE_ENABLED", "maybe")
    assert r.status_code == 400
    assert "开关型配置" in r.json()["msg"]

    # float 键拒绝 nan / inf
    for bad in ("nan", "inf", "-inf"):
        r = put_config(client, make_token, "CONFIDENCE_RETHRESHOLD", bad)
        assert r.status_code == 400, bad

    # 空值（schema min_length=1，校验异常统一 400）
    r = put_config(client, make_token, "STUCK_TO_L2", "")
    assert r.status_code == 400

    # 被拒后 DB 无任何写入：列表仍全部回退默认
    items = get_configs(client, make_token)
    for key, (default_str, _cast) in DEFAULTS.items():
        assert items[key]["value"] == default_str, key


def test_update_accepts_valid_values(client, db, make_token):
    """合法值全部放行（int / float / bool 白名单 / str 键）。"""
    clear_all_configs(db)

    assert put_config(client, make_token, "STUCK_TO_L2", "4").status_code == 200
    assert put_config(client, make_token, "REPEAT_SIMILARITY", "0.85").status_code == 200
    assert put_config(client, make_token, "HAS_SUBSTANCE_MIN_LEN", "20").status_code == 200
    # bool 键白名单内的大小写混写
    assert put_config(client, make_token, "CONSENT_GATE_ENABLED", "ON").status_code == 200
    # str 键（CONSENT_VERSION）任意非空串
    assert put_config(client, make_token, "CONSENT_VERSION", "v2").status_code == 200


# ==================================================================== 缓存失效（核心）
def test_update_invalidates_process_cache(client, db, make_token):
    """PUT 成功后 get_config 直接读到新值（锁死 reload_config 被调用）。"""
    clear_all_configs(db)

    r = put_config(client, make_token, "REPEAT_SIMILARITY", "0.9")
    assert r.status_code == 200
    assert r.json()["data"]["value"] == "0.9"

    # 直接读 service（绕开 HTTP）：缓存不清这里会读到旧值 0.7
    assert get_config("REPEAT_SIMILARITY") == pytest.approx(0.9)

    # 列表同样反映新值
    items = get_configs(client, make_token)
    assert items["REPEAT_SIMILARITY"]["value"] == "0.9"


def test_update_bool_key_reads_back(client, db, make_token):
    """bool 键：白名单 token 写入后 get_config 拿到对应布尔。"""
    clear_all_configs(db)

    assert put_config(client, make_token, "CONSENT_GATE_ENABLED", "true").status_code == 200
    assert get_config("CONSENT_GATE_ENABLED") is True

    assert put_config(client, make_token, "CONSENT_GATE_ENABLED", "off").status_code == 200
    assert get_config("CONSENT_GATE_ENABLED") is False


# ==================================================================== upsert 双路径
def test_update_upsert_then_update_paths(client, db, make_token):
    """首建（无行）与更新（有行）两条路径都写对，说明文案落到行上。

    复验一律走**独立新会话**：本项目 ``SessionLocal`` 配了
    ``expire_on_commit=False``（app/db.py），fixture 会话 ``db`` 的
    identity map 在 commit 后不过期，重复 SELECT 会拿到第一次读进的
    旧对象——端点其实已提交新值，用 fixture 会话复验会读到假象。
    """
    from app.db import SessionLocal

    def _db_row() -> SystemConfig:
        with SessionLocal() as s:
            return s.execute(
                select(SystemConfig).where(SystemConfig.key == "HAS_SUBSTANCE_MIN_LEN")
            ).scalar_one()

    clear_all_configs(db)

    # 首建路径（无行 → INSERT）
    r = put_config(client, make_token, "HAS_SUBSTANCE_MIN_LEN", "20")
    assert r.status_code == 200
    row = _db_row()
    assert row.value == "20"
    assert row.description  # 建行时带上中文说明
    assert get_config("HAS_SUBSTANCE_MIN_LEN") == 20

    # 更新路径（有行 → UPDATE）
    r = put_config(client, make_token, "HAS_SUBSTANCE_MIN_LEN", "15")
    assert r.status_code == 200
    row = _db_row()
    assert row.value == "15"
    assert row.description  # 更新不抹掉说明
    assert get_config("HAS_SUBSTANCE_MIN_LEN") == 15


# ==================================================================== reset
def test_reset_restores_defaults(client, db, make_token):
    """reset：全部键回默认值（接口返回值 / 列表 / get_config 三处一致）。"""
    # 先弄脏几个键
    assert put_config(client, make_token, "REPEAT_SIMILARITY", "0.99").status_code == 200
    assert put_config(client, make_token, "CONSENT_GATE_ENABLED", "true").status_code == 200
    assert put_config(client, make_token, "CONSENT_VERSION", "v9").status_code == 200

    r = client.post("/api/admin/configs/reset", headers=admin_headers(make_token))
    assert r.status_code == 200
    assert r.json()["data"]["reset"] is True
    assert r.json()["data"]["items"] == list(DEFAULTS.keys())

    # 列表全部回默认
    items = get_configs(client, make_token)
    for key, (default_str, _cast) in DEFAULTS.items():
        assert items[key]["value"] == default_str, key

    # get_config（缓存侧）同样回默认
    assert get_config("REPEAT_SIMILARITY") == pytest.approx(0.7)
    assert get_config("CONSENT_GATE_ENABLED") is False
    assert get_config("CONSENT_VERSION") == "v1"
