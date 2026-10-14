"""权益网关：FastAPI 依赖，控制接口访问。

用法::

    from app.core.entitlement import require_active_sub, check_ai_quota_dep

    @router.post("/chat")
    async def chat(..., user=Depends(require_active_sub)):
        ...

    @router.post("/ai-explain")
    async def ai_explain(..., _=Depends(check_ai_quota_dep)):
        ...

设计要点：
    * 免费套餐也有 active 订阅，所以 ``require_active_sub`` 不挡免费用户。
    * ``check_ai_quota_dep`` 是副依赖：既验证登录，又检查 AI 配额。
    * 所有依赖都走 ``get_db`` 拿 session，不额外开连接。
"""

from __future__ import annotations

import uuid

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user
from app.db import get_db
from app.services import subscription_service as svc


async def require_active_sub(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CurrentUser:
    """要求用户有有效订阅（免费也算）。无订阅时自动开通免费套餐。"""
    uid = uuid.UUID(user.sub)
    svc.get_or_create_free_subscription(db, uid)
    db.commit()
    return user


async def check_ai_quota_dep(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CurrentUser:
    """检查 AI 讲解配额。超限抛 429。"""
    uid = uuid.UUID(user.sub)
    svc.check_ai_quota(db, uid)
    db.commit()
    return user


__all__ = [
    "require_active_sub",
    "check_ai_quota_dep",
]
