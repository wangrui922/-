"""错题本接口（T03 · 3.4，T04 · B10 扩展）。

    POST /api/mistake                记录一道错题（幂等）
    GET  /api/mistake/list           错题本列表，支持三态筛选
    PUT  /api/mistake/{id}/solve     标记为已攻克（幂等，T04 新增）

三态口径（见 ``app.services.learning_service``）：
    * ``unsolved``   未掌握——未攻克，且该题从没答对过；
    * ``practicing`` 练习中——未攻克，但已答对过至少一次；
    * ``mastered``   已掌握——已标记攻克。

硬约定：
    * 需要登录且 ``user.sid`` 非空；
    * ``POST /api/mistake`` 挂 ``require_guardian_consent`` 监护人同意门控
      （``CONSENT_GATE_ENABLED`` 默认关，开启后无当前版本同意返 403）；
      GET 列表 / PUT 攻克不在门控范围（读与既有写路径保持原样）；
    * 出参 camelCase，时间 ISO 8601（UTC 存储）——
      本文件是 T03 既有接口，**沿用 camelCase 不改**，
      仅 T04 的 ``/api/profile/*`` 新接口用 snake_case；
    * 用 sync ``def`` 路由，FastAPI 自动放线程池执行，不阻塞事件循环；
    * ``/solve`` 越权访问返 **404**（不返 403），避免泄露资源存在性。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, require_guardian_consent
from app.core.response import ok
from app.db import get_db
from app.models.learning import Mistake
from app.schemas.common import BaseSchema
from app.services import learning_service, review_service
from app.services.learning_service import MISTAKE_STATES, require_student_id

router = APIRouter(tags=["mistake"])


class MistakeCreate(BaseSchema):
    """``POST /api/mistake`` 请求体。

    Attributes:
        question_id: 题目 ID（别名 ``questionId``）。
        error_type: 错误类型（别名 ``errorType``），如「审题不清」。
        error_reason: 错误原因（别名 ``errorReason``）。
    """

    question_id: str
    error_type: Optional[str] = None
    error_reason: Optional[str] = None


class MistakeSolve(BaseSchema):
    """``PUT /api/mistake/{id}/solve`` 请求体（整体可选）。

    Attributes:
        note: 攻克备注 / 学生自述心得，可为空。
    """

    note: Optional[str] = None


def _to_item(mistake: Mistake, due: Optional[str] = None) -> Dict[str, Any]:
    """把 ``Mistake`` 序列化成 camelCase 出参（时间转 ISO 8601）。

    ⚠️ 本文件是 T03 既有接口，沿用 camelCase 不改。
    kpId/kpName/grade 为 T01 新增字段：
      - kpId/kpName 来自 Mistake.kp_id（快照），kp_name 通过 relationship 取
      - grade 来自 Question.grade（通过 question relationship 取）
      - kpId 为空时前端不渲染知识点条 / 不显示「练同类题」按钮
    due 为复习调度联动新增字段：
      - 该题题目粒度复习卡的下次到期时间（ISO 8601），学生端展示
        「何时该再练」；
      - 无卡（建卡前历史数据 / 建卡失败）或已攻克（卡片暂停调度）时
        为 None，前端不展示该信息。

    Args:
        mistake: 错题实例。
        due: 该题复习卡到期时间（ISO 8601 或 None），由列表接口按
            (student, question) join review_cards 得出。
    """
    return {
        "id": str(mistake.id),
        "questionId": str(mistake.question_id),
        "errorType": mistake.error_type,
        "errorReason": mistake.error_reason,
        "note": mistake.note,
        "solved": mistake.solved,
        "solvedAt": mistake.solved_at.isoformat() if mistake.solved_at else None,
        "createdAt": mistake.created_at.isoformat() if mistake.created_at else None,
        # ★ T01 新增：知识点深链出参
        "kpId": str(mistake.kp_id) if mistake.kp_id else None,
        "kpName": mistake.kp.name if mistake.kp else None,
        "grade": mistake.question.grade if mistake.question else None,
        "questionText": mistake.question.content if mistake.question else "",
        "originalImageUrl": mistake.question.image_url if mistake.question else None,
        "workImageUrls": list(mistake.work_image_urls or []),
        # ★ 复习调度联动：何时该再练（无卡/已暂停为 None）
        "due": due,
    }


@router.post("/mistake", dependencies=[Depends(require_guardian_consent)])
def create_mistake(
    body: MistakeCreate = Body(...),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """记录一道错题（同一题存在未攻克记录时直接复用，不重复建档）。

    Returns:
        ``{"code":0,"msg":"ok","data":{"id":"uuid","solved":false}}``。

    Raises:
        ApiError: 400 未绑定学生 / questionId 非法 / 题目不存在。
    """
    student_id = require_student_id(current.sid)
    mistake = learning_service.record_mistake(
        db,
        student_id=student_id,
        question_id=body.question_id,
        error_type=body.error_type,
        error_reason=body.error_reason,
    )
    return ok({"id": str(mistake.id), "solved": mistake.solved})


@router.get("/mistake/list")
def list_mistakes(
    state: Optional[str] = Query(
        None,
        description=f"三态筛选，可选值：{'/'.join(MISTAKE_STATES)}；不传返回全部",
    ),
    kp_id: Optional[str] = Query(
        None,
        alias="kp_id",
        description="按知识点筛选错题（UUID 字符串）；不传返回全部",
    ),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """按三态筛选错题本，``createdAt`` 倒序。

    出参每条错题带 ``due``（何时该再练）：该题题目粒度复习卡的下次到期
    时间（ISO 8601）；无复习卡或已攻克（卡片暂停）为 ``None``。

    Returns:
        ``{"code":0,"msg":"ok","data":{"items":[...],"total":n}}``。

    Raises:
        ApiError: 400 未绑定学生 / state 取值非法 / kp_id 非法 UUID。
    """
    student_id = require_student_id(current.sid)
    items = learning_service.list_mistakes(
        db, student_id=student_id, state=state, kp_id=kp_id
    )
    # 复习卡到期时间（学生端「何时该再练」）：按 (student, question) 从
    # review_cards join 得出；无卡 / 已攻克暂停 → None（前端不展示）。
    due_map = review_service.get_question_due_map(db, student_id)
    return ok(
        {
            "items": [
                _to_item(m, due_map.get(str(m.question_id))) for m in items
            ],
            "total": len(items),
        }
    )


@router.put("/mistake/{mistake_id}/solve")
def solve_mistake(
    mistake_id: str = Path(..., description="错题 ID"),
    body: Optional[MistakeSolve] = Body(None),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """把一道错题标记为「已攻克」（T04 · B10，幂等）。

    行为：
        * 首次调用写 ``solved=True`` + ``solvedAt``，并让该知识点的
          掌握度缓存失效（B11），下次看板刷新即反映最新状态；
        * 重复调用不刷新 ``solvedAt``，直接回显当前状态；
        * ``note`` 可选，传了非空值就覆盖；
        * 错题不存在 **或不属于当前学生**，一律 404。

    Returns:
        ``{"code":0,"msg":"ok","data":{"id","questionId","errorType",
        "errorReason","note","solved","solvedAt","createdAt"}}``。

    Raises:
        ApiError: 400 未绑定学生 / ID 非法；401 未登录；404 错题不存在或非本人。
    """
    student_id = require_student_id(current.sid)
    mistake = learning_service.mark_mistake_solved(
        db,
        mistake_id=mistake_id,
        student_id=student_id,
        note=body.note if body is not None else None,
    )
    return ok(_to_item(mistake))


__all__ = ["router", "MistakeCreate", "MistakeSolve"]
