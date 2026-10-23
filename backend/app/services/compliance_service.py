"""T13 合规服务：监护人同意留痕（V6.5 60 人内测合规闸门）。

职责：
    * ``record_guardian_consent``：记录一次监护人同意。幂等——同一账号
      同一 ``consent_version`` 重复提交返回已有记录，不报错、不新建；
    * ``get_consent_status``：查询某账号在某版本（缺省取最新一条）的
      同意状态；
    * ``get_consent_detail``：查询同意三态详情（当前版本是否同意 +
      最近一次同意的版本/时间），供家长端/孩子端展示「已同意 /
      未同意 / 版本失效」。

事务：只 ``flush``，``commit`` 由 ``app.db.get_db`` 收口
（与 ``student_dna_service`` 同一约定）。

合规口径：
    * 仅 ``agreed=true`` 才落库；``agreed=false`` 一律 400 拒绝记录
      （不同意时的降级行为见 ``docs/监护人同意流程说明.md``）；
    * ``ip`` / ``user_agent`` / ``channel`` 为同意取证字段，随请求写入，
      对应《儿童个人信息网络保护规定》第十条的同意留痕要求。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.response import ApiError, CODE_BAD_REQUEST
from app.models.base import utcnow
from app.models.compliance import GuardianConsent


def _parse_uuid(raw: Any, field: str) -> uuid.UUID:
    """把入参安全转 UUID，失败按 400 处理。"""
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise ApiError(CODE_BAD_REQUEST, f"{field} 非法") from exc


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """把时间规整成 tz-aware UTC。

    SQLite 方言读回 ``DateTime`` 是 naive 的（无时区后缀），而全库统一 UTC
    存储（见 ``app/models/base.py::utcnow``），因此 naive 值一律按 UTC 补齐，
    保证同一时刻在「内存对象」与「库读回」两条路径上序列化结果逐字一致
    （幂等返回已有记录时 agreedAt 字符串不能漂移）。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _serialize(row: GuardianConsent) -> Dict[str, Any]:
    """同意记录 -> 出参 dict（恒为已同意态：只有同意过才会有行）。"""
    agreed_at = _as_utc(row.agreed_at)
    return {
        "agreed": True,
        "agreedAt": agreed_at.isoformat() if agreed_at is not None else None,
        "consentVersion": row.consent_version,
    }


def record_guardian_consent(
    db: Session,
    *,
    user_id: Any,
    guardian_phone: Optional[str],
    consent_version: str,
    agreed: bool,
    channel: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Dict[str, Any]:
    """记录一次监护人同意（幂等）。

    Args:
        db: 数据库会话（只 flush，不 commit，事务边界交给调用方）。
        user_id: 同意所属账号（``users.id``，UUID 或其字符串形式）。
        guardian_phone: 监护人手机号，可为空（仅留痕）。
        consent_version: 规则版本号，必填非空。
        agreed: 是否同意；``False`` 直接 400，不落库。
        channel: 提交渠道（web / miniapp 等），可为空。
        ip: 提交方 IP（合规取证），可为空。
        user_agent: 客户端 UA（合规取证），可为空。

    Returns:
        ``{"agreed": True, "agreedAt": iso8601, "consentVersion": str}``。

    Raises:
        ApiError: 400 —— ``agreed=false`` / 版本号为空 / user_id 非法。
    """
    if not agreed:
        raise ApiError(CODE_BAD_REQUEST, "agreed 必须为 true 才能记录监护人同意")

    version = (consent_version or "").strip()
    if not version:
        raise ApiError(CODE_BAD_REQUEST, "consentVersion 不能为空")

    uid = _parse_uuid(user_id, "userId")

    # 幂等：同一账号同一版本只留一条，重复提交直接返回已有记录
    existing = db.execute(
        select(GuardianConsent).where(
            GuardianConsent.user_id == uid,
            GuardianConsent.consent_version == version,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _serialize(existing)

    row = GuardianConsent(
        user_id=uid,
        guardian_phone=(guardian_phone or "").strip() or None,
        consent_version=version,
        agreed_at=utcnow(),
        channel=(channel or "").strip() or None,
        ip=(ip or "").strip() or None,
        user_agent=(user_agent or "").strip() or None,
    )
    db.add(row)
    db.flush()
    return _serialize(row)


def get_consent_status(
    db: Session,
    *,
    user_id: Any,
    consent_version: Optional[str] = None,
) -> Dict[str, Any]:
    """查询同意状态。

    Args:
        db: 数据库会话。
        user_id: 账号 ID（UUID 或其字符串形式）。
        consent_version: 指定版本；为空时取该账号最新一条同意记录。

    Returns:
        ``{"agreed": bool, "agreedAt": str|None, "consentVersion": str|None}``；
        从未同意时三者分别为 ``False / None / None``。
    """
    uid = _parse_uuid(user_id, "userId")
    stmt = select(GuardianConsent).where(GuardianConsent.user_id == uid)

    version = (consent_version or "").strip()
    if version:
        stmt = stmt.where(GuardianConsent.consent_version == version)

    stmt = stmt.order_by(
        GuardianConsent.agreed_at.desc(), GuardianConsent.created_at.desc()
    )
    row = db.execute(stmt).scalars().first()
    if row is None:
        return {"agreed": False, "agreedAt": None, "consentVersion": None}
    return _serialize(row)


def get_consent_detail(
    db: Session,
    *,
    user_id: Any,
    current_version: str,
) -> Dict[str, Any]:
    """查询同意详情（三态展示：已同意 / 未同意 / 版本失效）。

    与 ``get_consent_status`` 的分工：后者按「指定版本」回答一个布尔；
    本函数一次性回答「当前版本是否同意 + 最近一次同意的版本/时间」，
    供前端区分「从未同意」与「规则更新后旧同意失效」两种未同意态。

    口径（与 ``deps.require_guardian_consent`` 门控逐字对齐）：
        1. 先查 ``(user_id, consent_version=current_version)``——有即
           ``granted=true``，时间/版本取该记录（门控放行的就是它）；
        2. 没有 → 再查该账号最近一条同意记录（不限版本）：有则
           ``granted=false`` 但 ``consentVersion`` 非空（版本失效），
           时间/版本取这条旧记录供展示「上次同意」；
        3. 一条都没有 → 全空（从未同意）。

    Args:
        db: 数据库会话。
        user_id: 账号 ID（UUID 或其字符串形式）。
        current_version: 服务端当前规则版本（调用方读 ``CONSENT_VERSION``）。

    Returns:
        ``{"granted": bool, "agreedAt": str|None, "consentVersion": str|None,
        "currentVersion": str}``。
    """
    uid = _parse_uuid(user_id, "userId")
    version = (current_version or "").strip() or "v1"

    current_row = db.execute(
        select(GuardianConsent).where(
            GuardianConsent.user_id == uid,
            GuardianConsent.consent_version == version,
        )
    ).scalar_one_or_none()
    if current_row is not None:
        detail = _serialize(current_row)
        return {
            "granted": True,
            "agreedAt": detail["agreedAt"],
            "consentVersion": detail["consentVersion"],
            "currentVersion": version,
        }

    latest_row = db.execute(
        select(GuardianConsent)
        .where(GuardianConsent.user_id == uid)
        .order_by(
            GuardianConsent.agreed_at.desc(), GuardianConsent.created_at.desc()
        )
    ).scalars().first()
    if latest_row is None:
        return {
            "granted": False,
            "agreedAt": None,
            "consentVersion": None,
            "currentVersion": version,
        }

    detail = _serialize(latest_row)
    return {
        "granted": False,
        "agreedAt": detail["agreedAt"],
        "consentVersion": detail["consentVersion"],
        "currentVersion": version,
    }


__all__ = [
    "record_guardian_consent",
    "get_consent_status",
    "get_consent_detail",
]
