"""#65 复习排程接口单测（TestClient 端到端）。

覆盖：
    * GET  /api/practice/review/today  返回今日该复习的知识点；
    * POST /api/practice/review/record  记录一次复习并推进排程。
"""

from __future__ import annotations

import uuid

from app.db import SessionLocal
from app.models.content import KnowledgePoint
from app.services import review_service

# login_with_code 定义在 conftest 模块级，pytest 会收集；这里直接 import 以便复用
from tests.conftest import login_with_code  # type: ignore


def test_review_today_and_record(client, new_phone):
    phone = new_phone()
    data = login_with_code(client, phone)
    token = data["access"]
    headers = {"Authorization": f"Bearer {token}"}

    # 取当前学生 id
    me = client.get("/api/auth/me", headers=headers).json()["data"]
    sid = uuid.UUID(me["studentId"])

    # 直接落一个知识点 + 初始复习排程行
    s = SessionLocal()
    try:
        kp = KnowledgePoint(id=uuid.uuid4(), name="复习测试KP", code="CZ_RV_TEST", sort=0)
        s.add(kp)
        s.flush()
        review_service.seed_initial(s, sid, [kp.id])
        s.commit()
    finally:
        s.close()

    # GET 今日复习：应包含刚初始化的知识点（due_at=None → 立即到期）
    r = client.get("/api/practice/review/today", headers=headers)
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    assert any(it["kp_id"] == str(kp.id) for it in items)
    assert r.json()["data"]["total"] >= 1

    # POST 记录一次「答对且讲清」→ quality 5 → 首次间隔 1 天
    r2 = client.post(
        "/api/practice/review/record",
        headers=headers,
        json={"kp_id": str(kp.id), "correct": True, "verdict": "correct"},
    )
    assert r2.status_code == 200, r2.text
    d2 = r2.json()["data"]
    assert d2["repetitions"] == 1
    assert d2["interval_days"] == 1
    assert d2["ease_factor"] == 2.6
    assert d2["due_at"] is not None
