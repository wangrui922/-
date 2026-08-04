"""Epic6 家长周报 · QA 独立验收测试（Q1~Q12）。

本文件由 QA（严过关）独立编写，**不与 ``test_epic6_report.py`` 重复**，
专门补齐工程师自测未覆盖、但属于验收硬约束的路径：

    Q1  公开分享路由函数签名**不含**任何鉴权依赖（静态自省，防回归）
    Q2  无任何鉴权头 GET 分享接口 → 200；generate / history → 401
    Q3  **过期 token** → 404（工程师用例只测了「不存在」，未测「已过期」）
    Q4  同一自然周重复生成：token 不变 + DB 只有 1 行 + snapshot 被刷新
    Q5  view_count 连续打开 N 次精确累加到 N（直接查库核对，不经接口）
    Q6  **AI 抛异常兜底**：HTTP 200 不 500，source=="fallback"，
        文案取自 ``get_recommendations().reason``（工程师只测了 AI 成功路径）
    Q7  错因分布快照与 ``/api/profile/error-patterns`` **结构同构且恒 5 类**
    Q8  两份 migration 均含 ``weekly_reports`` 与 ``view_count``；
        SQLite 下建表 + CRUD 全通
    Q9  跨周边界：北京时间周日 23:59 与周一 00:01（UTC 仅差 2 分钟）分属两周
    Q10 单周内重复生成仍为 first_report（无对比）
    Q11 分享载荷不泄露 student_id / user_id / 手机号（深度扫描）
    Q12 AI 返回脏 JSON / 缺字段时能收敛，不崩

测试库是会话级共享内存 SQLite，每个用例用独立手机号造独立学生，避免互相污染。
"""

from __future__ import annotations

import inspect
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.ai.ark_client import ArkError
from app.api import report as report_api
from app.core.error_categories import ERROR_CATEGORIES
from app.db import SessionLocal
from app.models.weekly_report import WeeklyReport
from app.services import report_service

from e6_helpers import login, seed_report_week
from t03_helpers import assert_fail, assert_ok

#: 仓库内 migrations 目录（tests/ 的上一级是 backend/）
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


# ==================================================================== 工具
def _new_student(client, new_phone):
    """造一个独立学生，返回 (请求头, 学生ID)。"""
    return login(client, new_phone())


def _gen(client, headers, *, force: bool = False) -> dict:
    """调生成接口并断言成功。"""
    return assert_ok(
        client.post("/api/student/report/generate", json={"force": force}, headers=headers)
    )


def _fetch_record(token: str) -> WeeklyReport:
    """按 token 从库里读回记录（用于绕过接口直接核对落库结果）。"""
    with SessionLocal() as db:
        return db.execute(
            select(WeeklyReport).where(WeeklyReport.token == token)
        ).scalar_one()


# ============================================== Q1 公开路由签名无鉴权依赖（静态）
def test_share_route_signature_has_no_auth_dependency():
    """公开分享路由函数签名里绝不能出现 get_current_user / CurrentUser（K1 / S14）。

    这是最高优先级约束：一旦有人手滑加上鉴权依赖，家长会直接吃 401。
    用静态自省锁死，比跑一次请求更能防回归。
    """
    sig = inspect.signature(report_api.get_shared_report)

    for name, param in sig.parameters.items():
        default_repr = repr(param.default)
        assert "get_current_user" not in default_repr, f"参数 {name} 挂了鉴权依赖：{default_repr}"
        assert "CurrentUser" not in str(param.annotation), f"参数 {name} 标注了 CurrentUser"

    # 参数只应有 token 与 db
    assert set(sig.parameters) == {"token", "db"}, f"公开路由参数异常：{list(sig.parameters)}"

    # 函数源码里也不得调用 _require_student
    source = inspect.getsource(report_api.get_shared_report)
    assert "_require_student" not in source, "公开路由内部调用了 _require_student"


# ====================================== Q2 无鉴权头：分享 200，其余接口 401
def test_share_public_no_auth_header_returns_200(client, new_phone):
    """不发任何鉴权头直接 GET 分享接口 → 200 且数据完整（家长真实场景）。"""
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=3, wrong=1)
    token = _gen(client, headers)["share_token"]

    # ★ 关键：完全不传 headers，模拟家长在微信里点开
    resp = client.get(f"/api/report/share/{token}")
    assert resp.status_code == 200, resp.text
    payload = assert_ok(resp)
    assert payload["engagement"]["attempt_count"] == 4
    assert payload["narrative"]["headline"]


def test_authed_endpoints_reject_anonymous(client):
    """对照组：generate 与 history 在无登录时必须被拦（401）。"""
    assert_fail(client.post("/api/student/report/generate", json={"force": False}), 401)
    assert_fail(client.get("/api/student/report/history"), 401)


# ==================================================================== Q3 过期 token
def test_share_expired_token_returns_404(client, new_phone):
    """把 expires_at 改到过去 → 404 统一文案（不是 500、不是 200 空数据）。

    工程师用例只覆盖了「token 不存在」，未覆盖「token 存在但已过期」这条分支，
    而后者恰恰是 30 天有效期的核心路径。
    """
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=2, wrong=1)
    token = _gen(client, headers)["share_token"]

    # 有效期内先确认能打开
    assert_ok(client.get(f"/api/report/share/{token}"))

    # 手动置为已过期
    with SessionLocal() as db:
        record = db.execute(
            select(WeeklyReport).where(WeeklyReport.token == token)
        ).scalar_one()
        record.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()

    resp = client.get(f"/api/report/share/{token}")
    body = assert_fail(resp, 404)
    assert body["msg"] == "这个链接已失效，请让孩子重新生成一份"
    assert body["data"] == {}
    # 不得回显 token 本身（防探测）
    assert token not in resp.text


def test_share_random_and_missing_token_404(client):
    """随机不存在 token / 缺 token → 404，绝不 500，也绝不 200 空数据。"""
    for bad in ("zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", "../../etc/passwd", "%20", "null"):
        resp = client.get(f"/api/report/share/{bad}")
        assert resp.status_code == 404, f"token={bad} 返回了 {resp.status_code}：{resp.text}"

    # 缺 token（路径末尾为空）
    assert client.get("/api/report/share/").status_code == 404


# ============================== Q4 重复生成：token 不变 + 单行 + snapshot 刷新
def test_regenerate_same_week_keeps_token_single_row_and_refreshes(client, new_phone):
    """同一自然周两次 generate：token 相同、DB 恒 1 行、force 时 snapshot 被刷新。"""
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=2, wrong=0)

    first = _gen(client, headers)
    token_1 = first["share_token"]

    # 再造 3 道题，force 重算，快照数字必须跟着变
    seed_report_week(sid, correct=3, wrong=0)
    second = _gen(client, headers, force=True)

    assert second["share_token"] == token_1, "重新生成换了 token，家长手上的链接会失效"
    assert second["report_id"] == first["report_id"]

    with SessionLocal() as db:
        rows = list(
            db.execute(select(WeeklyReport).where(WeeklyReport.student_id == sid)).scalars()
        )
    assert len(rows) == 1, f"同一学生同一周应只有 1 行，实际 {len(rows)} 行"

    # snapshot 已刷新：题数从 2 涨到 5
    assert rows[0].snapshot_json["engagement"]["attempt_count"] == 5


# ==================================================================== Q5 view_count
def test_view_count_accumulates_exactly_n_times(client, new_phone):
    """连续打开 N 次分享页，DB 里的 view_count 必须精确等于 N。"""
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=2, wrong=0)
    token = _gen(client, headers)["share_token"]

    assert _fetch_record(token).view_count == 0, "刚生成时 view_count 应为 0"

    times = 5
    for index in range(1, times + 1):
        assert_ok(client.get(f"/api/report/share/{token}"))
        assert _fetch_record(token).view_count == index, f"第 {index} 次打开后计数不对"

    assert _fetch_record(token).view_count == times


# ============================================== Q6 AI 兜底：抛异常仍 200 + fallback
def test_generate_falls_back_when_ark_raises(client, new_phone, monkeypatch):
    """方舟抛 ArkError → 生成仍成功（HTTP 200 不 500），source == "fallback"。

    这是 PRD 验收项 V4「AI 挂了也必须出报告」的核心路径，
    工程师用例只测了 AI 成功分支（source=="ai"），未测失败分支。
    """
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=1, wrong=4)  # 造出薄弱知识点，保证有 recommendations

    def _boom(**kwargs):
        raise ArkError("模拟方舟不可用")

    monkeypatch.setattr("app.ai.agents.report.ark_respond", _boom)

    resp = client.post("/api/student/report/generate", json={"force": True}, headers=headers)
    assert resp.status_code == 200, f"AI 失败不该给 500：{resp.text}"
    data = assert_ok(resp)
    assert data["ai_source"] == "fallback"

    payload = assert_ok(client.get(f"/api/report/share/{data['share_token']}"))
    narrative = payload["narrative"]
    assert narrative["source"] == "fallback"
    # 三段文案与建议都不能空——家长视角必须完全无感知
    assert narrative["headline"]
    assert narrative["overview_text"]
    assert narrative["progress_text"]
    assert 2 <= len(narrative["advice_items"]) <= 3
    assert all(item.strip() for item in narrative["advice_items"])
    # 绝不能把降级信息暴露给家长
    assert "AI" not in narrative["headline"]
    assert "失败" not in narrative["overview_text"]


def test_fallback_advice_comes_from_recommendation_reason(client, new_phone):
    """兜底建议文案确实取自 ``get_recommendations().reason``，而非凭空模板。"""
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=1, wrong=5)

    with SessionLocal() as db:
        snapshot = report_service.build_weekly_snapshot(db, sid)
        comparison = report_service.diff_snapshots(snapshot, None)

        from app.ai.agents import report as report_agent

        fallback = report_agent.build_fallback_report(snapshot, comparison, "小明")

    assert fallback["source"] == "fallback"
    reasons = [str(item.get("reason") or "") for item in snapshot.recommendations]
    reasons = [item for item in reasons if item]

    if reasons:
        # 建议条目应是某条 reason 的（可能被截断到 60 字的）前缀
        matched = any(
            any(reason.startswith(advice[:20]) for reason in reasons)
            for advice in fallback["advice_items"]
        )
        assert matched, f"兜底建议未取自 recommendations.reason：{fallback['advice_items']}"


# ====================================== Q7 错因快照与 profile 接口结构同构（5 类）
def test_error_patterns_snapshot_matches_profile_api_shape(client, new_phone):
    """分享接口的 error_patterns 必须与 /api/profile/error-patterns 同构且恒 5 类。

    前端 ErrorPatternChart.vue 的 props 就是这个完整结构，
    结构一旦漂移，组件「零改造复用」的前提就没了。
    """
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=2, wrong=3)

    token = _gen(client, headers)["share_token"]
    shared = assert_ok(client.get(f"/api/report/share/{token}"))["error_patterns"]
    profile = assert_ok(client.get("/api/profile/error-patterns", headers=headers))

    # ① 顶层键集合完全一致
    assert set(shared) == set(profile), (
        f"结构漂移：周报多出 {set(shared) - set(profile)}，缺少 {set(profile) - set(shared)}"
    )

    # ② 恒 5 类，且类别集合与唯一真相源一致
    assert len(shared["error_patterns"]) == 5, f"应恒为 5 类，实际 {len(shared['error_patterns'])}"
    assert [row["category"] for row in shared["error_patterns"]] == [
        row["category"] for row in profile["error_patterns"]
    ]
    assert set(row["category"] for row in shared["error_patterns"]) == set(ERROR_CATEGORIES)

    # ③ 每一项的字段集合一致（category/label/count/ratio/sample_hits）
    for shared_row, profile_row in zip(shared["error_patterns"], profile["error_patterns"]):
        assert set(shared_row) == set(profile_row), f"明细字段不一致：{set(shared_row) ^ set(profile_row)}"
        assert isinstance(shared_row["count"], int)
        assert isinstance(shared_row["ratio"], (int, float))
        assert isinstance(shared_row["sample_hits"], list)


# ============================================ Q8 双方言建表 + SQLite CRUD 实证
def test_both_migrations_declare_table_and_view_count():
    """PG 与 SQLite 两份 migration 都必须含 weekly_reports 表与 view_count 列。"""
    for filename in ("003_weekly_reports.sql", "003_weekly_reports_sqlite.sql"):
        path = MIGRATIONS_DIR / filename
        assert path.exists(), f"缺失 migration：{filename}"
        sql = path.read_text(encoding="utf-8").lower()
        assert "create table" in sql and "weekly_reports" in sql, f"{filename} 未建 weekly_reports 表"
        assert "view_count" in sql, f"{filename} 缺 view_count 列"
        for column in ("student_id", "period_start", "period_end", "token", "expires_at",
                       "snapshot_json", "report_text"):
            assert column in sql, f"{filename} 缺列 {column}"


def test_sqlite_migration_actually_creates_table_and_crud_works():
    """把 SQLite 版 DDL 真跑一遍：能建表、能增删改查，不报语法错。

    只验证本表的 DDL 与 CRUD，因此先建一张最小的 students 桩表满足外键引用。
    """
    ddl = (MIGRATIONS_DIR / "003_weekly_reports_sqlite.sql").read_text(encoding="utf-8")

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE students (id TEXT PRIMARY KEY)")
        conn.executescript(ddl)  # ← DDL 有语法错这里就会炸

        columns = {row[1] for row in conn.execute("PRAGMA table_info(weekly_reports)")}
        assert "view_count" in columns, f"建出来的表没有 view_count：{columns}"

        sid = str(uuid.uuid4())
        conn.execute("INSERT INTO students (id) VALUES (?)", (sid,))

        # C：插入
        conn.execute(
            "INSERT INTO weekly_reports "
            "(id, student_id, period_start, period_end, token, expires_at, "
            " view_count, snapshot_json, report_text) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), sid, "2026-01-05T00:00:00", "2026-01-11T23:59:59",
             "qa-token-001", "2026-02-04T00:00:00", 0, "{}", "{}"),
        )
        # R
        row = conn.execute(
            "SELECT view_count FROM weekly_reports WHERE token=?", ("qa-token-001",)
        ).fetchone()
        assert row[0] == 0
        # U：模拟 view_count 累加
        conn.execute("UPDATE weekly_reports SET view_count = view_count + 1 WHERE token=?",
                     ("qa-token-001",))
        assert conn.execute(
            "SELECT view_count FROM weekly_reports WHERE token=?", ("qa-token-001",)
        ).fetchone()[0] == 1
        # D
        conn.execute("DELETE FROM weekly_reports WHERE token=?", ("qa-token-001",))
        assert conn.execute("SELECT COUNT(*) FROM weekly_reports").fetchone()[0] == 0

        # 唯一约束真实生效
        conn.execute(
            "INSERT INTO weekly_reports "
            "(id, student_id, period_start, period_end, token, expires_at, "
            " view_count, snapshot_json, report_text) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), sid, "2026-01-05T00:00:00", "2026-01-11T23:59:59",
             "qa-token-002", "2026-02-04T00:00:00", 0, "{}", "{}"),
        )
        try:
            conn.execute(
                "INSERT INTO weekly_reports "
                "(id, student_id, period_start, period_end, token, expires_at, "
                " view_count, snapshot_json, report_text) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), sid, "2026-01-05T00:00:00", "2026-01-11T23:59:59",
                 "qa-token-003", "2026-02-04T00:00:00", 0, "{}", "{}"),
            )
            raise AssertionError("(student_id, period_start) 唯一约束未生效")
        except sqlite3.IntegrityError:
            pass
    finally:
        conn.close()


# ==================================================== Q9 跨周边界（北京时间）
def test_week_boundary_sunday_2359_vs_monday_0001():
    """北京时间周日 23:59 与周一 00:01（UTC 仅差 2 分钟）必须分属两个自然周。

    这是时区处理最容易翻车的地方：若直接按 UTC 切周，
    周日晚上生成的报告会把周日整天算进下周，家长一眼就能看出数字不对。
    """
    sunday_2359 = datetime(2024, 8, 11, 15, 59, tzinfo=timezone.utc)   # 北京 08-11 23:59 周日
    monday_0001 = datetime(2024, 8, 11, 16, 1, tzinfo=timezone.utc)    # 北京 08-12 00:01 周一

    first = report_service.resolve_period(now=sunday_2359)
    second = report_service.resolve_period(now=monday_0001)

    assert first.start_local != second.start_local, "跨周边界被算进了同一周"
    assert first.start_local.weekday() == 0 and second.start_local.weekday() == 0
    assert first.start_local == second.start_local - timedelta(days=7)

    # 周日 23:59 那一刻仍归属「本周」
    assert first.start_local <= sunday_2359.astimezone(report_service.LOCAL_TZ).date() <= first.end_local
    # is_partial 的权威语义是「本周还没结束」（设计文档 §4.4），按时刻粒度判定：
    # 周日 23:59:00 距周日 23:59:59.999999 还差 1 分钟，仍属未结束 → True。
    # （§7.3 的口语化写法「今天 < 周日」是天粒度近似，仅在本周最后 1 分钟与此不同，
    #   以 §4.4 的接口契约为准。）
    assert first.is_partial is True
    assert second.is_partial is True, "周一 00:01 时本周刚开始，应为 partial"


def test_past_week_report_is_not_partial(client, new_phone):
    """真正有意义的 is_partial：查看「已经过完的那一周」的报告 → is_partial=False。

    resolve_period 永远返回 now 所在的当前周，故其 is_partial 恒为 True；
    对家长真正可见的是 build_report_payload 里按「当前时刻 vs 周期终点」重算的值。
    """
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=2, wrong=1)

    past = datetime(2024, 8, 7, 4, 0, tzinfo=timezone.utc)  # 北京 2024-08-07 周三，早已过完
    with SessionLocal() as db:
        record, _ = report_service.generate_weekly_report(db, sid, now=past)
        payload = report_service.build_report_payload(db, record)

    assert payload["period"]["is_partial"] is False, "已过完的周不应再标记为进行中"
    assert payload["period"]["start"] == "2024-08-05"
    assert payload["period"]["end"] == "2024-08-11"


def test_attempts_are_bucketed_into_correct_week(client, new_phone):
    """落在不同自然周的作答，只能被各自那一周的快照统计到。"""
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=1, wrong=0)  # 建 kp/question，附带 1 条本周作答

    from app.models.learning import Attempt

    def _add(when: datetime) -> None:
        with SessionLocal() as db:
            question_id = db.execute(select(Attempt.question_id).where(
                Attempt.student_id == sid)).scalars().first()
            db.add(Attempt(student_id=sid, question_id=question_id, is_correct=True,
                           created_at=when, client_event_id=f"qa-{uuid.uuid4().hex}"))
            db.commit()

    # 北京 2024-08-11 23:59（周日，属 08-05 那周）与 08-12 00:01（周一，属 08-12 那周）
    _add(datetime(2024, 8, 11, 15, 59, tzinfo=timezone.utc))
    _add(datetime(2024, 8, 11, 16, 1, tzinfo=timezone.utc))

    with SessionLocal() as db:
        week_a = report_service.resolve_period(now=datetime(2024, 8, 11, 15, 59, tzinfo=timezone.utc))
        week_b = report_service.resolve_period(now=datetime(2024, 8, 11, 16, 1, tzinfo=timezone.utc))
        stat_a = report_service.count_engagement(db, sid, week_a)
        stat_b = report_service.count_engagement(db, sid, week_b)

    assert stat_a.attempt_count == 1, f"周日那条应只落在前一周，实际 {stat_a.attempt_count}"
    assert stat_b.attempt_count == 1, f"周一那条应只落在后一周，实际 {stat_b.attempt_count}"
    assert stat_a.study_days == 1 and stat_b.study_days == 1


# ============================================ Q10 单周重复生成仍是 first_report
def test_same_week_repeat_generate_stays_first_report(client, new_phone):
    """单周内反复生成不会凭空造出「上周对比」，始终 first_report。"""
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=3, wrong=1)

    token = _gen(client, headers)["share_token"]
    first = assert_ok(client.get(f"/api/report/share/{token}"))
    assert first["comparison"]["available"] is False
    assert first["comparison"]["reason"] == "first_report"

    _gen(client, headers, force=True)
    again = assert_ok(client.get(f"/api/report/share/{token}"))
    assert again["comparison"]["available"] is False, "同一周重复生成不该出现环比"
    assert again["comparison"]["reason"] == "first_report"
    assert again["comparison"]["prev_period_start"] is None


# ==================================================================== Q11 隐私
def test_share_payload_leaks_no_identifiers(client, new_phone):
    """分享载荷深度扫描：不得出现 student_id / user_id / 手机号（S17）。"""
    phone = new_phone()
    headers, sid = login(client, phone)
    seed_report_week(sid, correct=2, wrong=1)

    token = _gen(client, headers)["share_token"]
    resp = client.get(f"/api/report/share/{token}")
    raw = resp.text

    assert phone not in raw, "载荷里出现了手机号"
    assert not re.search(r"\b1[3-9]\d{9}\b", raw), "载荷里出现了疑似手机号"
    assert '"user_id"' not in raw, "载荷里出现了 user_id"

    payload = assert_ok(resp)
    assert set(payload["student"]) == {"display_name", "grade"}, (
        f"student 段只应有 display_name/grade，实际 {set(payload['student'])}"
    )


# ============================================ Q12 脏 JSON / 缺字段收敛不崩
def test_dirty_ai_output_is_normalized(client, new_phone, monkeypatch):
    """模型返回带 ```json 包裹 / 缺字段的脏输出时能收敛，不抛异常。"""
    headers, sid = _new_student(client, new_phone)
    seed_report_week(sid, correct=2, wrong=2)

    def _dirty(**kwargs):
        # 带 markdown 包裹、缺 progress_text、advice_items 只有 1 条
        return (
            '```json\n'
            '{"headline": "本周练了 4 道题", '
            '"overview_text": "这周做了 4 道题，做对 2 道。", '
            '"advice_items": ["每天 10 分钟口算"]}\n'
            '```'
        )

    monkeypatch.setattr("app.ai.agents.report.ark_respond", _dirty)

    resp = client.post("/api/student/report/generate", json={"force": True}, headers=headers)
    assert resp.status_code == 200, resp.text
    data = assert_ok(resp)

    payload = assert_ok(client.get(f"/api/report/share/{data['share_token']}"))
    narrative = payload["narrative"]
    # 缺失的 progress_text 必须被兜底补齐，advice 必须补到 >= 2 条
    assert narrative["headline"]
    assert narrative["overview_text"]
    assert narrative["progress_text"], "缺失字段未被兜底补齐"
    assert 2 <= len(narrative["advice_items"]) <= 3
