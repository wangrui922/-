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
#: 同意门控的规则版本——**不要硬编码**：运行时从生产库 system_config 读
#: （见 resolve_consent_version）；这里只作为环境变量覆盖的入口。
CONSENT_VERSION_OVERRIDE = os.environ.get("SMOKE_CONSENT_VERSION")
#: 冒烟用的监护人手机号（仅留痕，不发短信）
GUARDIAN_PHONE = os.environ.get("SMOKE_GUARDIAN_PHONE", "13900000000")
RESULTS: list[tuple[str, bool, str]] = []


def _is_uuid(value: str) -> bool:
    """判断字符串是否为合法 UUID（用于拼 raw SQL 的 IN 列表）。"""
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _jwt_claim(token: str, claim: str) -> str | None:
    """从 JWT payload 里取一个 claim（不验签，只为拿到 id 做清理目标）。

    激活接口的响应体里**没有 user_id**，只有 access token；
    token 的 payload 带 ``sub``（用户 id）与 ``sid``（学生 id），
    这是冒烟清理能定位到本次测试数据的唯一可靠来源。
    """
    import base64
    import json

    parts = (token or "").split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload + padding)
        data = json.loads(raw)
    except Exception:  # noqa: BLE001 - token 坏了就当没有
        return None
    val = data.get(claim)
    return str(val) if val else None


def resolve_consent_version(db) -> str:
    """从生产库读当前 ``CONSENT_VERSION``。

    门控判定用的是 ``system_config.CONSENT_VERSION``（改动规则版本后旧同意
    记录一律失效）。冒烟脚本若硬编码 v1，一旦生产改成 v2 就会写出「同意 v1」
    的记录 → 门控不认 → 后续断言全崩，且看不出真实原因。**必须跟生产读。**
    """
    from sqlalchemy import select

    from app.models.system_config import SystemConfig

    try:
        v = db.execute(
            select(SystemConfig.value).where(SystemConfig.key == "CONSENT_VERSION")
        ).scalar_one_or_none()
        if v and str(v).strip():
            return str(v).strip()
    except Exception as exc:  # noqa: BLE001
        print(f"  读 CONSENT_VERSION 失败（回退 v1）：{exc}")
    return "v1"


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
    created_student_ids: list[str] = []
    db = get_db()

    # 同意门控的规则版本：优先环境变量覆盖，否则跟生产 system_config 读
    consent_version = CONSENT_VERSION_OVERRIDE or resolve_consent_version(db)
    print(f"门控版本 consentVersion={consent_version}\n")

    # ---------------------------------------------------------- 0. 探活
    print("[0] 探活（仅参考，不作为通过依据）")
    st, _ = call("GET", "/health")
    check("health 可达", st == 200, f"status={st}")

    # ---------------------------------------------------------- 1. 激活码进入
    print("\n[1] 激活码进入")
    from sqlalchemy import select

    from app.models.activation import ActivationCode
    from app.services import auth_service

    # 激活码库里只存 ``code_digest``（SHA-256），**明文不可恢复**——
    # 所以不能从库里捞现成码来用（旧版本脚本踩过
    # ``'ActivationCode' object has no attribute 'code'`` 这个坑），
    # 必须现场生成一枚未绑定的码，测完再删掉。
    plain_code = ""
    created_code_id = None
    try:
        codes = auth_service.generate_activation_codes(
            db, 1, label="smoke_beta 冒烟临时码"
        )
        plain_code = codes[0]
        created_code_id = db.execute(
            select(ActivationCode.id).where(
                ActivationCode.code_digest == auth_service._activation_digest(plain_code)
            )
        ).scalar_one_or_none()
        db.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"  生成临时激活码失败：{exc}")

    if not plain_code:
        check("生成临时激活码", False, "无法生成，激活链路无法测")
        return finish(args, db, created_user_ids, created_code_id, created_student_ids)
    check("生成临时激活码", True, f"code={plain_code[:10]}…")

    st, body = call(
        "POST", "/auth/activation/activate",
        {"code": plain_code, "pin": PIN, "nickname": nickname},
    )
    ok = st == 200 and body.get("code") == 0
    check("激活码激活成功", ok, f"status={st} body={str(body)[:220]}")
    if not ok:
        return finish(args, db, created_user_ids, created_code_id, created_student_ids)
    data = body.get("data") or {}
    token = data.get("token") or data.get("accessToken") or data.get("access_token")
    user_id = data.get("user_id") or data.get("userId")
    if user_id:
        created_user_ids.append(str(user_id))
    check("拿到 access token", bool(token))
    if not token:
        print(f"  data keys={sorted(data.keys())}")
        return finish(args, db, created_user_ids, created_code_id, created_student_ids)

    # ---- 兜底：从 JWT payload 解出 sub / sid ----
    # ⚠️ 激活接口的 data 里**没有 user_id 字段**（只有 access token），
    #    所以上面 created_user_ids 一直是空的 → 清理阶段全程空转，
    #    生产库每次冒烟都留一批孤儿数据（2026-10-21 实测确认）。
    #    JWT payload 里带 sub（用户 id）和 sid（学生 id），
    #    直接解出来当作清理目标，不依赖响应体字段。
    if not created_user_ids:
        for claim, bucket in (("sub", created_user_ids), ("sid", created_student_ids)):
            val = _jwt_claim(token, claim)
            if val and _is_uuid(val):
                bucket.append(val)
        if created_user_ids:
            print(f"  已从 JWT 解出清理目标 sub={created_user_ids[0]} sid={created_student_ids}")

    # ---------------------------------------------------------- 2. 门控
    print("\n[2] 监护人同意门控")
    st, body = call("POST", "/practice/session", {"count": 1}, token=token)
    gated = st == 403 and "监护人同意" in str(body.get("msg", ""))
    check("未同意 → 403 且话术含「监护人同意」", gated, f"status={st} msg={body.get('msg')!r}")

    # 入参是 camelCase：``guardianPhone`` / ``consentVersion`` / ``agreed``
    # （后端 schema 字段名是 snake_case，但 BaseSchema 配了 camelCase 别名，
    # 只传 ``agreed`` 会 400 "guardianPhone Field required"）。
    st, body = call(
        "POST", "/compliance/guardian-consent",
        {
            "guardianPhone": GUARDIAN_PHONE,
            "consentVersion": consent_version,
            "agreed": True,
        },
        token=token,
    )
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
        return finish(args, db, created_user_ids, created_code_id, created_student_ids)

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
    check("复习计划可读", st == 200 and body.get("code") == 0, f"status={st} body={str(body)[:250]}")

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

    return finish(args, db, created_user_ids, created_code_id, created_student_ids)


def finish(args, db, created_user_ids, created_code_id=None, created_student_ids=None) -> int:
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
            from sqlalchemy import delete, select, text

            from app.models.activation import ActivationCode
            from app.models.ai import AiMessage, AiSession
            from app.models.learning import Attempt, Mistake
            from app.models.user import Student, User

            # ---- 第一步：先把要删的 id 全收集起来 ----
            # ⚠️ 必须先收集再删除。若边查边删，下面兜底段的
            #    ``select id from students where user_id in (...)`` 子查询
            #    会在 students 行已被删掉之后执行 → 恒返回空集 → 子表一条都删不掉。
            #    （这正是 2026-10-21 生产残留的直接原因：兜底段"执行了"但删了 0 行。）
            target_uids: list[str] = []
            target_sids: list[str] = []
            # JWT 解出的 sid 是最可靠的 student 目标（库里 students 行可能已被删）
            for sid in (created_student_ids or []):
                if _is_uuid(sid) and sid not in target_sids:
                    target_sids.append(sid)
            for uid in created_user_ids:
                if not _is_uuid(uid):
                    continue
                # 无条件加入：即使库里这行已不存在，user_id 维度的表
                # （guardian_consents / subscriptions / payment_transactions）
                # 仍可能挂着指向它的行，必须一起清。
                if uid not in target_uids:
                    target_uids.append(uid)
                user = db.execute(
                    select(User).where(User.id == uuid.UUID(uid))
                ).scalar_one_or_none()
                if user is None:
                    continue
                for s in db.execute(
                    select(Student).where(Student.user_id == user.id)
                ).scalars().all():
                    if str(s.id) not in target_sids:
                        target_sids.append(str(s.id))

            if target_uids or target_sids:
                uids = ",".join(f"'{u}'" for u in target_uids) or "''"
                sids_sql = (
                    ",".join(f"'{s}'" for s in target_sids) if target_sids else "''"
                )
                sids_in = f"({sids_sql})"

                # ---- 第二步：删核心表（ORM）----
                # ⚠️ 先收集 ai_sessions.id：下面会删掉 ai_sessions 行，
                #    之后子查询就找不到 session 了，ai_messages 会成孤儿。
                target_session_ids: list[str] = []
                for sid in target_sids:
                    for s in db.execute(
                        select(AiSession.id).where(AiSession.student_id == sid)
                    ).scalars().all():
                        target_session_ids.append(str(s))
                for sid in target_sids:
                    db.execute(delete(AiMessage).where(AiMessage.session_id.in_(
                        select(AiSession.id).where(AiSession.student_id == sid))))
                    db.execute(delete(AiSession).where(AiSession.student_id == sid))
                    db.execute(delete(Mistake).where(Mistake.student_id == sid))
                    db.execute(delete(Attempt).where(Attempt.student_id == sid))
                db.execute(delete(Student).where(Student.id.in_(target_sids)))
                db.execute(delete(ActivationCode).where(ActivationCode.user_id.in_(target_uids)))
                db.execute(delete(User).where(User.id.in_(target_uids)))
                print(f"  已清理 user {len(target_uids)} 个 / student {len(target_sids)} 个")

                # ---- 第三步：兜底清子表 ----
                # 上面按 ORM 模型删了核心表，但 learning_tasks / review_cards /
                # review_logs / student_learning_events / student_learning_profiles /
                # student_knowledge_mastery / practice_progress / guardian_consents
                # 这些表有的没被上面覆盖、有的（review_cards/review_logs）
                # 根本无 ORM 模型（由 FSRS 模块 raw SQL 维护），
                # 漏删会在生产库留孤儿行，累积后污染统计口径。
                #
                # 不能「执行失败就 rollback」——那会把同一事务里已经删掉的
                # 行一起回滚掉。正确做法是**先探测表是否存在**，只对存在的表发 DELETE。
                from sqlalchemy import inspect as sa_inspect

                inspector = sa_inspect(db.get_bind())
                existing = set(inspector.get_table_names())
                plan = [
                    ("guardian_consents", f"delete from guardian_consents where user_id in ({uids})"),
                    ("learning_tasks", f"delete from learning_tasks where student_id in {sids_in}"),
                    ("review_logs", f"delete from review_logs where student_id in {sids_in}"),
                    ("review_cards", f"delete from review_cards where student_id in {sids_in}"),
                    ("student_knowledge_mastery",
                     f"delete from student_knowledge_mastery where student_id in {sids_in}"),
                    ("student_learning_events",
                     f"delete from student_learning_events where student_id in {sids_in}"),
                    ("student_learning_profiles",
                     f"delete from student_learning_profiles where student_id in {sids_in}"),
                    ("practice_progress",
                     f"delete from practice_progress where student_id in {sids_in}"),
                    ("subscriptions", f"delete from subscriptions where user_id in ({uids})"),
                    ("payment_transactions",
                     f"delete from payment_transactions where user_id in ({uids})"),
                ]
                hit = 0
                for table, sql in plan:
                    if table not in existing:
                        continue
                    db.execute(text(sql))
                    hit += 1
                # 兜底清 ai_messages：按**收集到的 session_id 列表**删。
                # ⚠️ 不能用全局 `not in (select id from ai_sessions)`——
                #    那是清理脚本 cleanup_prod_data.py 的活，
                #    在冒烟里做会误删同库其他用户的数据。
                if "ai_messages" in existing and target_session_ids:
                    sess_in = ",".join(f"'{s}'" for s in target_session_ids)
                    db.execute(text(
                        f"delete from ai_messages where session_id in ({sess_in})"
                    ))
                db.commit()
                print(f"  已清理关联子表 {hit} 张（consent/task/review/learning/subscription）")

            # 兜底：激活失败时那枚临时码仍是「未绑定」状态，上面按 user_id
            # 删不到，会在生产库留垃圾——按 id 显式删一次。
            if created_code_id is not None:
                db.execute(delete(ActivationCode).where(ActivationCode.id == created_code_id))
                print(f"  已清理临时激活码 {created_code_id}")
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
