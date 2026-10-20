#!/usr/bin/env python
"""生产环境真实业务链路冒烟（2026-10-20）。

**为什么不能用 /api/health 代替**：实测（2026-10-20）health 返回 200，但
practice/session 与 /api/chat 全 500 —— health 只探进程存活，不探业务。

本脚本按真实用户路径跑完整链路（端点与字段均已对照后端 schema 核实）：
    1. 激活码进入        POST /api/auth/activation/activate  {code, pin, nickname}
    2. 监护人同意门控    POST /api/practice/session → 403「监护人同意」
                         POST /api/compliance/guardian-consent → 200
                         POST /api/practice/session → 放行
    3. 抽题              POST /api/practice/session  {count}
    4. 作答判分          POST /api/practice/answer   {session_id, question_id, selected_answer}
    5. AI 讲题（真实 ARK）POST /api/chat  {text}
    6. 错题本            GET  /api/mistake/list
    7. 复习计划          GET  /api/progress/adaptive-plan
    8. 权益额度无限      GET  /api/billing/entitlement  → dailyAiLimit = -1
    9. admin 端点鉴权    学生访问 /admin/dashboard/overview、/billing/pending → 403

⚠️ 脚本会创建测试数据，**结束后自动清理**。

用法（服务器容器内）：
    sudo docker exec deploy-backend-1 python /app/scripts/smoke_beta.py
    sudo docker exec deploy-backend-1 python /app/scripts/smoke_beta.py --keep
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8000")
API = f"{BASE}/api"
PIN = "123456"
RESULTS: list[tuple[str, bool, str]] = []


# ==================================================================== 工具
def call(method: str, path: str, body: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    """调用 API，返回 (http_status, json_body)。"""
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, {"raw": raw[:500]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:500]}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  |  {detail[:220]}" if detail else ""))
    return ok


def get_db():
    """拿到 app.db 的 Session（容器内直连生产库）。"""
    sys.path.insert(0, "/app")
    from app.db import SessionLocal

    return SessionLocal()


# ==================================================================== 冒烟
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留测试数据（默认自动清理）")
    args = ap.parse_args()

    print(f"=== 生产冒烟 {datetime.now(timezone.utc).isoformat()} ===")
    print(f"目标：{BASE}\n")

    suffix = uuid.uuid4().hex[:8]
    nickname = f"冒烟{suffix[:4]}"
    token: str | None = None
    created_user_ids: list[str] = []
    db = get_db()

    # ---------------------------------------------------------- 0. 探活
    print("[0] 探活（仅参考，不作为通过依据）")
    st, _ = call("GET", "/health")
    check("health 可达", st == 200, f"status={st}")

    # ---------------------------------------------------------- 1. 激活码进入
    print("\n[1] 激活码进入")
    from sqlalchemy import select

    from app.models.activation import ActivationCode

    try:
        code_row = db.execute(
            select(ActivationCode).where(ActivationCode.user_id.is_(None)).limit(1)
        ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        code_row = None
        print(f"  读激活码失败：{exc}")
    if code_row is None:
        check("存在未使用激活码", False, "库里没有未使用的激活码，无法测激活链路")
        return finish(args, db, created_user_ids)
    check("存在未使用激活码", True, f"code={code_row.code[:10]}…")

    st, body = call(
        "POST", "/auth/activation/activate",
        {"code": code_row.code, "pin": PIN, "nickname": nickname},
    )
    ok = st == 200 and body.get("code") == 0
    check("激活码激活成功", ok, f"status={st} body={str(body)[:220]}")
    if not ok:
        return finish(args, db, created_user_ids)
    data = body.get("data") or {}
    token = data.get("token") or data.get("accessToken") or data.get("access_token")
    user_id = data.get("user_id") or data.get("userId")
    if user_id:
        created_user_ids.append(str(user_id))
    check("拿到 access token", bool(token))
    if not token:
        print(f"  data keys={sorted(data.keys())}")
        return finish(args, db, created_user_ids)

    # ---------------------------------------------------------- 2. 门控
    print("\n[2] 监护人同意门控")
    st, body = call("POST", "/practice/session", {"count": 1}, token=token)
    gated = st == 403 and "监护人同意" in str(body.get("msg", ""))
    check("未同意 → 403 且话术含「监护人同意」", gated, f"status={st} msg={body.get('msg')!r}")

    st, body = call("POST", "/compliance/guardian-consent", {"agreed": True}, token=token)
    check("提交同意 → 200", st == 200 and body.get("code") == 0, f"status={st} body={str(body)[:200]}")

    st, body = call("POST", "/practice/session", {"count": 1}, token=token)
    released = st == 200 and body.get("code") == 0
    check("再请求 → 已放行（非 403）", released, f"status={st} body={str(body)[:250]}")
    session_payload = body.get("data") or {}

    # ---------------------------------------------------------- 3. 抽题
    print("\n[3] 抽题")
    questions = session_payload.get("questions") or session_payload.get("items") or []
    check("抽到题目", len(questions) > 0, f"questions={len(questions)} pool={session_payload.get('pool_size')}")
    if not questions:
        print(f"  data keys={sorted(session_payload.keys())}")
        return finish(args, db, created_user_ids)

    session_id = session_payload.get("session_id") or session_payload.get("sessionId")
    q = questions[0]
    qid = q.get("id") or q.get("question_id") or q.get("questionId")
    answer = q.get("answer") or q.get("correct_answer") or q.get("correctAnswer")
    check("题目含 id", bool(qid), f"id={qid} 题面={str(q.get('stem') or q.get('question') or '')[:60]}")
    check("含 session_id", bool(session_id), f"session={session_id}")

    # ---------------------------------------------------------- 4. 判分
    print("\n[4] 作答判分")
    st, body = call(
        "POST", "/practice/answer",
        {"session_id": session_id, "question_id": qid, "selected_answer": str(answer or "")},
        token=token,
    )
    check("判分返回 200", st == 200 and body.get("code") == 0, f"status={st} body={str(body)[:220]}")

    # ---------------------------------------------------------- 5. AI 讲题
    print("\n[5] AI 讲题（真实 ARK 调用）")
    st, body = call(
        "POST", "/chat",
        {"text": f"请讲解这道题：{str(q.get('stem') or q.get('question') or '1+1等于几')}"},
        token=token,
    )
    ai_ok = st == 200 and body.get("code") == 0
    if ai_ok:
        d = body.get("data") or {}
        if d.get("need_rephoto"):
            check("AI 讲题（低置信度，需重拍）", True, f"confidence={d.get('confidence')}")
        else:
            content = str(d.get("analysis") or "") + str(d.get("first_question") or "")
            check("AI 讲题有实质回答", len(content) > 10, f"长度={len(content)}")
    else:
        check("AI 讲题", False, f"status={st} body={str(body)[:250]}")

    # ---------------------------------------------------------- 6. 错题本
    print("\n[6] 错题本")
    st, body = call("GET", "/mistake/list", token=token)
    check("错题本可读", st == 200 and body.get("code") == 0, f"status={st}")

    # ---------------------------------------------------------- 7. 复习计划
    print("\n[7] 复习计划")
    st, body = call("GET", "/progress/adaptive-plan", token=token)
    check("复习计划可读", st == 200 and body.get("code") == 0, f"status={st}")

    # ---------------------------------------------------------- 8. 额度无限
    print("\n[8] 权益额度（内测不限额）")
    st, body = call("GET", "/billing/entitlement", token=token)
    if st == 200 and body.get("code") == 0:
        d = body.get("data") or {}
        check("dailyAiLimit = -1（无限）", d.get("dailyAiLimit") == -1, f"实际={d.get('dailyAiLimit')}")
        check("practiceDailyLimit = -1（无限）", d.get("practiceDailyLimit") == -1,
              f"实际={d.get('practiceDailyLimit')}")
        check("字段为 camelCase（前端可读）", "planName" in d, f"keys={sorted(d.keys())[:9]}")
    else:
        check("entitlement 可读", False, f"status={st} body={str(body)[:220]}")

    # ---------------------------------------------------------- 9. admin 端点
    print("\n[9] admin 新增端点（鉴权边界）")
    st, _ = call("GET", "/admin/dashboard/overview", token=token)
    check("学生访问 overview → 403", st == 403, f"status={st}")
    st, _ = call("GET", "/billing/pending", token=token)
    check("学生访问 billing/pending → 403", st == 403, f"status={st}")
    st, _ = call("GET", "/admin/dashboard/overview")
    check("未登录访问 overview → 401", st == 401, f"status={st}")

    return finish(args, db, created_user_ids)


def finish(args, db, created_user_ids) -> int:
    """汇总 + 清理。"""
    print("\n=== 汇总 ===")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [r for r in RESULTS if not r[1]]
    print(f"通过 {passed}/{len(RESULTS)}")
    for name, _, detail in failed:
        print(f"  FAIL: {name} | {detail[:300]}")

    if not args.keep:
        print("\n=== 清理测试数据 ===")
        try:
            from sqlalchemy import delete, select

            from app.models.activation import ActivationCode
            from app.models.ai import AiMessage, AiSession
            from app.models.learning import Attempt, Mistake
            from app.models.user import Student, User

            for uid in created_user_ids:
                try:
                    uu = uuid.UUID(uid)
                except ValueError:
                    continue
                user = db.execute(select(User).where(User.id == uu)).scalar_one_or_none()
                if user is None:
                    continue
                sids = [
                    s.id for s in db.execute(
                        select(Student).where(Student.user_id == user.id)
                    ).scalars().all()
                ]
                for sid in sids:
                    db.execute(delete(AiMessage).where(AiMessage.session_id.in_(
                        select(AiSession.id).where(AiSession.student_id == sid))))
                    db.execute(delete(AiSession).where(AiSession.student_id == sid))
                    db.execute(delete(Mistake).where(Mistake.student_id == sid))
                    db.execute(delete(Attempt).where(Attempt.student_id == sid))
                    db.execute(delete(Student).where(Student.id == sid))
                db.execute(delete(ActivationCode).where(ActivationCode.user_id == user.id))
                db.execute(delete(User).where(User.id == user.id))
                print(f"  已清理 user {uid}")
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            print(f"  清理失败（需人工处理）：{exc}")
    else:
        print("\n(--keep 模式，未清理)")

    if failed:
        print("\nSMOKE_FAIL")
        return 1
    print("\nSMOKE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
