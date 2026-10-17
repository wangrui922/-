"""T13 合规接口：监护人同意留痕（V6.5 60 人内测合规闸门）。

    POST /api/compliance/guardian-consent  记录监护人同意（幂等）
    GET  /api/compliance/guardian-consent  查询同意状态（?consent_version= 可选）

硬约定：
    * 需要登录（``get_current_user``）；学生本人或家长角色均可，
      其他角色 403；未登录 401；
    * 仅 ``agreed=true`` 落库；``agreed=false`` 按 400 拒绝记录
      （不同意时的降级行为见 ``docs/监护人同意流程说明.md``）；
    * ip / user_agent 从 ``Request`` 取，随同意记录一并留痕（合规取证）；
    * 响应恒为 ``{"code":0,"msg":"ok","data":{agreed,agreedAt,consentVersion}}``。
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user
from app.core.response import ApiError, CODE_BAD_REQUEST, CODE_FORBIDDEN, ok
from app.db import get_db
from app.schemas.compliance import GuardianConsentIn
from app.services import compliance_service

router = APIRouter(prefix="/compliance", tags=["compliance"])

#: 允许提交 / 查询监护人同意的角色（学生本人或家长）
_ALLOWED_ROLES = ("student", "parent")


def _assert_allowed(current: CurrentUser) -> None:
    """仅学生本人或家长角色可操作监护人同意，其余 403。

    话术刻意避开连续「监护人同意」四字：前端 ``api/http.ts`` 的 403
    兜底按该关键词拉同意引导弹窗，角色拒绝属于另一类错误，不能误弹。
    """
    if current.role not in _ALLOWED_ROLES:
        raise ApiError(CODE_FORBIDDEN, "仅学生或家长账号可提交同意")


def _current_user_id(current: CurrentUser) -> uuid.UUID:
    """把 JWT 里的 sub 安全转成 UUID（异常按 400，避免 500）。"""
    try:
        return uuid.UUID(current.sub)
    except (TypeError, ValueError) as exc:
        raise ApiError(CODE_BAD_REQUEST, "登录态异常，请重新登录") from exc


def _client_ip(request: Request) -> Optional[str]:
    """从请求取客户端 IP：优先 X-Forwarded-For 首跳，回落 ``request.client``。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    if request.client is not None and request.client.host:
        return request.client.host
    return None


@router.post("/guardian-consent")
async def record_consent(
    payload: GuardianConsentIn,
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """记录监护人同意。

    同一账号 + 同一 ``consentVersion`` 重复提交时幂等返回已有记录，不报错。

    Returns:
        ``{"code":0,"msg":"ok","data":{agreed,agreedAt,consentVersion}}``。

    Raises:
        ApiError: 401 未登录；400 ``agreed=false`` / 参数非法；403 角色不允许。
    """
    _assert_allowed(current)
    data = compliance_service.record_guardian_consent(
        db,
        user_id=_current_user_id(current),
        guardian_phone=payload.guardian_phone,
        consent_version=payload.consent_version,
        agreed=payload.agreed,
        channel=request.headers.get("x-client-channel"),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return ok(data)


@router.get("/guardian-consent")
async def query_consent(
    consent_version: Optional[str] = None,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """查询当前用户的监护人同意状态。

    Args:
        consent_version: 可选；指定版本时查该版本，缺省查最新一条同意记录。

    Returns:
        ``{"code":0,"msg":"ok","data":{agreed,agreedAt,consentVersion}}``；
        从未同意时 ``agreed=false`` 且其余字段为 ``None``。
    """
    _assert_allowed(current)
    data = compliance_service.get_consent_status(
        db,
        user_id=_current_user_id(current),
        consent_version=consent_version,
    )
    return ok(data)


__all__ = ["router"]
