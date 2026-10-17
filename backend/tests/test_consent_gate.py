"""V6.5 监护人同意门控测试（服务端强制 · T13 留痕后的配套锁）。

覆盖：
    * 配置默认值：``CONSENT_GATE_ENABLED`` 默认关、``CONSENT_VERSION`` 默认 ``v1``；
    * 开关关闭：无同意记录的用户全部放行（代表性 POST 路由不被拦）；
    * 开关开启 + 无同意记录：代表性 POST 一律 403，msg 固定为
      「使用练习功能需要监护人同意」（前端按此识别）；
    * 开关开启 + 当前版本（v1）同意：正常业务响应（200）；
    * 开关开启 + 只有旧版本（v0）同意：403（版本耦合）；
    * 他人同意不顶用：门控按 ``user_id`` 逐账号校验；
    * 读操作不挂门控：错题本列表 / 今日复习清单 / 游戏历史保持可浏览；
    * 依赖链顺序：未登录 401 先行；门控放行后 ``require_student_id`` 的
      400 行为不被破坏；
    * 跨端契约：前端 ``CONSENT_GATE_HINT`` / ``CONSENT_VERSION`` 常量与
      后端话术 / 配置默认值逐字对齐（防前端兜底静默失效）。

测试铁律：**绝不设置 DATABASE_URL 环境变量**（conftest 默认 :memory:，
设了会触发文件库闸门报错退出）。门控开关用 monkeypatch 替换
``system_config._cache`` 实现——不写 DB、用例结束自动还原、零跨用例污染。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict

import pytest

from app.core.system_config import DEFAULTS
from app.db import SessionLocal
from app.models.content import Answer, KnowledgePoint, Question
from t03_helpers import assert_fail, assert_ok

#: 门控拒绝话术（与 ``app.core.deps.CONSENT_GATE_DENIED_MSG`` 一致，前端按此识别）
CONSENT_DENIED_MSG = "使用练习功能需要监护人同意"

#: 门控当前版本（与 ``system_config.DEFAULTS`` 的 ``CONSENT_VERSION`` 对齐）
GATE_VERSION = "v1"

#: 旧版本同意（版本耦合反例：规则修订后旧同意一律失效）
STALE_VERSION = "v0"

#: 开关开启 + 无同意时的代表性 403 覆盖（路由标识, 方法+路径, 请求体）
BLOCKED_POSTS = [
    ("practice_session", "/api/practice/session", {}),
    ("exercise_judge", "/api/exercise/judge", {}),
    ("game_speed_answer", "/api/game/speed/answer", {}),
    ("review_submit", "/api/review/submit", {}),
    ("tasks_exercise_gen", "/api/tasks/async/exercise-gen", {}),
    ("mistake_create", "/api/mistake", {}),
]


# ====================================================================  fixtures
@pytest.fixture
def consent_gate(monkeypatch):
    """监护人同意门控开关夹具。

    直接替换 ``app.core.system_config._cache``（进程内配置缓存）：
    ``get_config`` 命中缓存后既不查库，也不会把门控值漏进真实缓存；
    monkeypatch 在用例结束后自动还原，零跨用例污染。
    """

    def _apply(*, enabled: bool, version: str = GATE_VERSION) -> None:
        from app.core import system_config

        monkeypatch.setattr(
            system_config,
            "_cache",
            {"CONSENT_GATE_ENABLED": enabled, "CONSENT_VERSION": version},
        )

    yield _apply


# ====================================================================  helpers
def _login(client, phone: str) -> Dict[str, Any]:
    """发码 → 取随机码 → 登录（首次登录自动建 user + student），返回 data 字典。"""
    r = client.post("/api/auth/code", json={"phone": phone})
    assert r.status_code == 200, r.text
    dev_code = r.json()["data"]["devCode"]
    r = client.post("/api/auth/login", json={"phone": phone, "code": dev_code})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _auth(token: str) -> Dict[str, str]:
    """带 Authorization 头的 dict。"""
    return {"Authorization": f"Bearer {token}"}


def _give_consent(client, token: str, *, version: str = GATE_VERSION) -> None:
    """通过合规接口给当前账号记录一条指定版本的监护人同意。"""
    r = client.post(
        "/api/compliance/guardian-consent",
        json={
            "guardianPhone": "13911112222",
            "consentVersion": version,
            "agreed": True,
        },
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text


def _make_kp(name: str) -> str:
    """插一个知识点，返回 ID 字符串。"""
    with SessionLocal() as db:
        kp = KnowledgePoint(name=name)
        db.add(kp)
        db.commit()
        return str(kp.id)


def _make_bank_question(
    *, kp_id: str, grade: int = 4, edition: str, section: str = "门控"
) -> str:
    """插一道合规题库选择题（含标准答案），返回题目 ID 字符串。

    与 ``test_practice.make_bank_question`` 同口径：只有 选择/判断 + 有答案 +
    active + 有选项的题能进抽题池，这里保证门控放行后能拿到真实 200。
    """
    with SessionLocal() as db:
        question = Question(
            content="门控测试题：3 + 4 = ?",
            subject="数学",
            grade=grade,
            difficulty=3,
            type_="选择",
            options={"A": "6", "B": "7", "C": "8", "D": "9"},
            kp_id=uuid.UUID(kp_id),
            kp_verified=True,
            edition=edition,
            section=section,
            status="active",
        )
        db.add(question)
        db.flush()
        db.add(
            Answer(
                question_id=question.id,
                correct_answer="B",
                analysis="先算加法。",
                distractor_analysis={},
            )
        )
        db.commit()
        return str(question.id)


# ====================================================================  配置默认值
def test_config_defaults_are_off_and_v1() -> None:
    """DEFAULTS 必须带上门控两键：默认关 + 版本 v1，且 cast 行为正确。"""
    assert "CONSENT_GATE_ENABLED" in DEFAULTS, "缺门控总开关配置"
    assert "CONSENT_VERSION" in DEFAULTS, "缺门控版本配置"

    default_str, cast = DEFAULTS["CONSENT_GATE_ENABLED"]
    assert default_str == "false", "门控默认必须为关（灰度放量）"
    # bool("false") 是 True—— cast 必须是真的布尔解析，不能是内置 bool
    assert cast("false") is False
    assert cast("true") is True
    assert cast("1") is True
    assert cast("0") is False

    assert DEFAULTS["CONSENT_VERSION"][0] == "v1"


def test_get_config_falls_back_to_defaults() -> None:
    """库无记录时 ``get_config`` 回退默认值（关 + v1）。"""
    from app.core import system_config

    system_config.reload_config()
    assert system_config.get_config("CONSENT_GATE_ENABLED") is False
    assert system_config.get_config("CONSENT_VERSION") == "v1"


# ====================================================================  开关关闭
def test_gate_disabled_allows_user_without_consent(
    client, consent_gate, new_phone
) -> None:
    """开关关闭：无同意记录的用户全部放行（含代表性 POST 路由）。"""
    consent_gate(enabled=False)
    student = _login(client, new_phone())
    headers = _auth(student["access"])

    # 代表路由：门控不拦（业务层各自 400/404，但绝不是同意门控的 403）
    for label, path, body in BLOCKED_POSTS:
        r = client.post(path, json=body, headers=headers)
        assert r.status_code != 403, f"{label} 不应被同意门控拦截: {r.text}"

    # 真实业务成功路径（防"放过但业务坏"）：抽题正常返回
    kp_id = _make_kp("门控-放行")
    _make_bank_question(kp_id=kp_id, edition="门控放行版")
    data = assert_ok(
        client.post(
            "/api/practice/session",
            json={"grade": 4, "edition": "门控放行版", "count": 5},
            headers=headers,
        )
    )
    assert data["session_id"]


# ====================================================================  开关开启·拦截
@pytest.mark.parametrize(
    ("label", "path", "body"),
    BLOCKED_POSTS,
    ids=[item[0] for item in BLOCKED_POSTS],
)
def test_gate_enabled_blocks_posts_without_consent(
    client, consent_gate, new_phone, label: str, path: str, body: Dict[str, Any]
) -> None:
    """开关开启 + 无同意记录：代表性 POST 一律 403，msg 固定。"""
    consent_gate(enabled=True)
    student = _login(client, new_phone())
    headers = _auth(student["access"])

    r = client.post(path, json=body, headers=headers)
    assert_fail(r, 403)
    assert r.json()["msg"] == CONSENT_DENIED_MSG


def test_gate_enabled_blocks_stale_version_consent(
    client, consent_gate, new_phone
) -> None:
    """开关开启 + 只有旧版本（v0）同意：403（版本耦合验证）。"""
    consent_gate(enabled=True, version=GATE_VERSION)
    student = _login(client, new_phone())
    headers = _auth(student["access"])
    _give_consent(client, student["access"], version=STALE_VERSION)

    r = client.post("/api/practice/session", json={}, headers=headers)
    assert_fail(r, 403)
    assert r.json()["msg"] == CONSENT_DENIED_MSG


def test_gate_enabled_other_users_consent_does_not_help(
    client, consent_gate, new_phone
) -> None:
    """门控按 user_id 逐账号校验：别人的同意不顶用。"""
    consent_gate(enabled=True)
    consented = _login(client, new_phone())
    _give_consent(client, consented["access"], version=GATE_VERSION)

    other = _login(client, new_phone())
    r = client.post("/api/practice/session", json={}, headers=_auth(other["access"]))
    assert_fail(r, 403)
    assert r.json()["msg"] == CONSENT_DENIED_MSG


# ====================================================================  开关开启·放行
def test_gate_enabled_allows_with_current_consent(
    client, consent_gate, new_phone
) -> None:
    """开关开启 + 当前版本（v1）同意：正常业务响应。"""
    consent_gate(enabled=True)
    student = _login(client, new_phone())
    headers = _auth(student["access"])
    _give_consent(client, student["access"], version=GATE_VERSION)

    # 练习闭环入口：抽题正常返回
    kp_id = _make_kp("门控-同意")
    _make_bank_question(kp_id=kp_id, edition="门控同意版")
    data = assert_ok(
        client.post(
            "/api/practice/session",
            json={"grade": 4, "edition": "门控同意版", "count": 5},
            headers=headers,
        )
    )
    assert data["session_id"]

    # 错题建档：正常返回（门控放行后业务链路完整）
    qid = _make_bank_question(kp_id=kp_id, edition="门控同意版")
    data2 = assert_ok(
        client.post("/api/mistake", json={"questionId": qid}, headers=headers)
    )
    assert data2["id"]


# ====================================================================  读操作不锁
def test_gate_enabled_leaves_reads_open(client, consent_gate, new_phone) -> None:
    """读操作一律不挂门控：错题本列表 / 今日复习清单 / 游戏历史保持可浏览。"""
    consent_gate(enabled=True)
    student = _login(client, new_phone())
    headers = _auth(student["access"])

    data = assert_ok(client.get("/api/mistake/list", headers=headers))
    assert data["items"] == []

    data = assert_ok(client.get("/api/practice/review/today", headers=headers))
    assert data["items"] == []

    # 游戏历史：data 是 list（assert_ok 只收 dict），手工校验 200 + code 0
    r = client.get("/api/game/history", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["code"] == 0
    assert r.json()["data"] == []


# ====================================================================  依赖链顺序
def test_gate_preserves_dependency_chain(
    client, consent_gate, new_phone, make_token
) -> None:
    """依赖链顺序：401 先行；门控放行后 require_student_id 的 400 不坏。"""
    consent_gate(enabled=True)

    # ① 未登录：401（鉴权收口先行，门控还没轮到）
    assert_fail(client.post("/api/practice/session", json={}), 401)

    # ② 已同意 + 未绑学生：400（门控放行后，require_student_id 行为不被破坏）
    student = _login(client, new_phone())
    _give_consent(client, student["access"], version=GATE_VERSION)
    unbound = make_token(sub=student["user"]["userId"], role="student", sid=None)
    r = client.post("/api/practice/session", json={}, headers=_auth(unbound))
    assert_fail(r, 400)
    assert r.json()["msg"] == "未绑定学生"

    # ③ 开关关闭 + 未绑学生：400（与现状完全一致）
    consent_gate(enabled=False)
    r = client.post("/api/practice/session", json={}, headers=_auth(unbound))
    assert_fail(r, 400)
    assert r.json()["msg"] == "未绑定学生"


# ====================================================================  跨端契约
def test_frontend_backend_contract_strings() -> None:
    """跨端契约：前端兜底识别的关键词 / 版本号必须与后端常量一致。

    用途：前端 student-web 无单测框架、后端 pytest 也不校验前端文本，
    一旦后端改 ``CONSENT_GATE_DENIED_MSG`` 话术或 ``CONSENT_VERSION``
    默认值，前端 403 兜底弹窗 / 同意提交版本会**静默失效**——本测试把
    这条隐性契约变成显式断言，话术 / 版本漂移即红。

    校验（student-web 目录定位：``tests/`` 上两级即仓库根）：
        1. ``src/api/http.ts`` 的 ``CONSENT_GATE_HINT`` 常量必须是后端
           ``app.core.deps.CONSENT_GATE_DENIED_MSG`` 的子串（前端按
           ``msg.includes(关键词)`` 识别，是子串才命中）；
        2. ``src/api/compliance.ts`` 的 ``CONSENT_VERSION`` 常量必须等于
           ``system_config.DEFAULTS["CONSENT_VERSION"][0]``（前端提交 /
           预查用的就是它，漂移会导致同意记录对不上门控版本）；
        3. ``src/stores/consent.ts`` 必须从 ``@/api/compliance`` 引入
           ``CONSENT_VERSION``（不得在 store 里另存一份会漂移的副本）。

    文件读取失败一律 FAIL 并给出清晰原因（**不 skip**）：文件缺失 /
    常量消失本身就是契约破坏。
    """
    import re

    from app.core import deps

    repo_root = Path(__file__).resolve().parents[2]
    student_web = repo_root / "student-web"

    def _read(rel: str) -> str:
        """读 student-web 下的源文件文本；缺失 / 读失败即 FAIL（不 skip）。"""
        path = student_web / rel
        assert path.is_file(), f"跨端契约：找不到前端文件 {path}，契约无从校验"
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise AssertionError(f"跨端契约：读取 {path} 失败：{exc}") from exc

    def _extract_const(source: str, name: str, path: Path) -> str:
        """从 TS 源里正则提取 ``const NAME = '...'`` 的字符串值。"""
        match = re.search(
            rf"{re.escape(name)}\s*=\s*['\"]([^'\"]+)['\"]", source
        )
        assert match, f"跨端契约：{path} 中未找到常量 {name} 的字符串定义"
        return match.group(1)

    # ① 前端兜底关键词 ⊆ 后端门控话术（后端改话术不含该词即红）
    http_ts = student_web / "src" / "api" / "http.ts"
    hint = _extract_const(_read("src/api/http.ts"), "CONSENT_GATE_HINT", http_ts)
    assert hint in deps.CONSENT_GATE_DENIED_MSG, (
        f"前端 CONSENT_GATE_HINT={hint!r} 不再是后端 "
        f"CONSENT_GATE_DENIED_MSG={deps.CONSENT_GATE_DENIED_MSG!r} 的子串，"
        "403 兜底弹窗将静默失效——请同步两端话术"
    )

    # ② 前端同意版本号 == 后端配置默认值（常量定义在 api/compliance.ts）
    compliance_ts = student_web / "src" / "api" / "compliance.ts"
    fe_version = _extract_const(
        _read("src/api/compliance.ts"), "CONSENT_VERSION", compliance_ts
    )
    be_version = DEFAULTS["CONSENT_VERSION"][0]
    assert fe_version == be_version, (
        f"前端 CONSENT_VERSION={fe_version!r} 与后端 "
        f"DEFAULTS['CONSENT_VERSION']={be_version!r} 不一致，"
        "同意提交 / 门控校验将版本错配"
    )

    # ③ store 复用 api/compliance.ts 的版本常量，不得自带副本
    consent_ts = student_web / "src" / "stores" / "consent.ts"
    store_src = _read("src/stores/consent.ts")
    assert re.search(
        r"import\s*\{[^}]*\bCONSENT_VERSION\b[^}]*\}\s*from\s*'@/api/compliance'",
        store_src,
    ), f"跨端契约：{consent_ts} 未从 @/api/compliance 引入 CONSENT_VERSION"
