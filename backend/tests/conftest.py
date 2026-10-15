"""测试公共夹具（pytest fixtures）。

要点：
    * **必须在 import app 之前** 把 ``DATABASE_URL`` 设成内存 SQLite，
      因为 ``app.db`` 在导入时即按环境变量构建引擎（单例）。
    * 提供 ``client``：基于 ``fastapi.TestClient`` 的测试客户端。
    * 提供 ``db``：会话级**共享**（``StaticPool`` 内存 SQLite）的数据库会话。
    * 提供 ``make_token``：造一个合法 access JWT 的 helper。
    * 提供 ``new_phone``（T04 新增）：产出全局唯一手机号，用于隔离学生数据。
"""

from __future__ import annotations

import itertools
import os

# ⚠️ 必须最先执行：在 import app 任何模块前固定测试库为内存 SQLite
os.environ["DATABASE_URL"] = os.environ.get("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod")
os.environ.setdefault("APP_ENV", "test")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# FSRS 方向三已落地（真实算法见 app/services/fsrs_scheduler.py，SM-2 核心 +
# FSRS 字段适配）。此处直接依赖真实模块；若其缺失，收集阶段即失败，
# 不允许再以占位实现静默兜底。
from app.services import fsrs_scheduler  # noqa: F401,E402

from app.core.security import (  # noqa: E402
    ROLE_STUDENT,
    create_access_token,
)
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """每个测试前清空限流器和验证码存储，避免跨用例相互限流。"""
    from app.core.ratelimit import code_send_limiter, login_attempt_limiter
    from app.services.sms import MockSmsProvider

    code_send_limiter.clear_all()
    login_attempt_limiter.clear_all()
    MockSmsProvider._reset()
    yield
    # 测试结束后也清一次，防止状态泄漏
    code_send_limiter.clear_all()
    login_attempt_limiter.clear_all()
    MockSmsProvider._reset()


@pytest.fixture(autouse=True, scope="session")
def _create_schema():
    """测试会话开始时建好全部表。

    * 必须在 ``import app.main`` 之后才能 lazy import ``Base``/``engine``，
      因为 ``app.main`` 会 import 全部 router，从而把全部 ORM 模型注册到
      ``Base.metadata``，``create_all`` 才能覆盖全部表。
    * 对内存库（``sqlite:///:memory:``）已用 ``StaticPool`` 共享连接，
      ``create_all`` 后表对后续请求可见；对文件库（如 dev.db）``create_all``
      是幂等的，重复执行无害。
    """

    from app.db import engine
    from app.models.base import Base

    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db():
    """返回一个**函数级**数据库会话，语义与 ``app.db.get_db`` 完全一致。

    为什么既要有这个 fixture、又有大量用例直接 ``with SessionLocal() as s:``：
        两者同根——都走 ``app.db.SessionLocal``，指向会话级共享的内存 SQLite
        （``StaticPool`` 单连接）。本 fixture 只是把这套约定包装一层，方便用例
        以「参数注入」而不是「自己 with」的方式拿会话。

    方言差异**一律**由 ``app/db.py`` 屏蔽；这里不出现任何 SQLite / PG 分支，
    也不做任何 commit 时机上的方言假设。

    提交语义（与 ``get_db`` 对齐）：用例正常跑完即 commit，让后续用
    ``SessionLocal()`` 独立开的会话也能读到；用例抛异常则 rollback。

    Usage:
        def test_something(db):
            db.add(...)
            db.flush()
    """
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture
def client() -> TestClient:
    """返回应用测试客户端。"""
    return TestClient(app)


@pytest.fixture
def make_token():
    """造 JWT 的 helper。

    Usage:
        token = make_token(sub="1", role="student", sid="1")
    """

    def _make(
        sub: str = "1",
        role: str = ROLE_STUDENT,
        sid: str | None = "1",
        **kwargs,
    ) -> str:
        return create_access_token(sub=sub, role=role, sid=sid, **kwargs)

    return _make


@pytest.fixture
def auth_headers(make_token):
    """带 Authorization 头的 dict，方便直接塞进 client 请求。"""

    def _headers(
        sub: str = "1", role: str = ROLE_STUDENT, sid: str | None = "1"
    ) -> dict:
        return {"Authorization": f"Bearer {make_token(sub=sub, role=role, sid=sid)}"}

    return _headers


#: 全局自增序列，保证同一次 pytest 会话内手机号不重复
_PHONE_SEQ = itertools.count(1)

#: new_phone() 的号段起点（8 位序号部分）。
#:
#: 为什么不是 0：库里有若干**硬编码**手机号已经占了低段——
#: ``test_backfill_kp.py`` 用 ``13900000001``、``test_review_service.py`` 用
#: ``13900000001`` ~ ``13900000005``，它们与本 fixture 落在**同一个会话级共享
#: 内存库**里。序号若从 0 开始递增，跑全量时前者先到先用低段互不干扰，但一旦
#: 只跑子集（``-k`` / 单文件 / 调整执行顺序），``new_phone()`` 就会撞上这些写死的
#: 学生，登录时拿到别人的档案，画像类断言随即漂移——同一套代码在不同人机器上
#: 跑出不同结果，正是这个坑。把号段整体抬到 50000000 以上即可彻底避开。
_PHONE_BASE: int = 50_000_000


@pytest.fixture
def new_phone():
    """产出一个全局唯一的合法手机号（T04 新增）。

    为什么需要它：
        测试库是**会话级共享**的内存 SQLite，同一个手机号会命中同一个学生，
        导致 T04 的画像统计被其他用例的数据污染（``total_kp`` 之类的断言随
        执行顺序漂移）。每个用例取一个新手机号 = 一个干净的新学生。

    手机号格式必须匹配 ``app.services.sms.PHONE_PATTERN``（``^1[3-9]\\d{9}$``），
    这里用 ``139`` + 8 位序号拼出 11 位；号段起点 ``_PHONE_BASE`` 保证不与他人
    写死的测试手机号相撞。

    Usage:
        phone = new_phone()
    """

    def _make() -> str:
        return f"139{_PHONE_BASE + next(_PHONE_SEQ):08d}"

    return _make


__all__ = [
    "client",
    "db",
    "make_token",
    "auth_headers",
    "new_phone",
    "_create_schema",
    "login_with_code",
]


def login_with_code(client: TestClient, phone: str = "13800000000") -> dict:
    """共享登录 helper：发码 → 取随机码 → 登录，返回 ``data`` 字典。

    替代旧版直接用 ``000000`` 万能码登录的方式。
    需在 ``_reset_rate_limiters`` autouse fixture 作用下使用（每个测试自动清限流器）。
    """
    # 1) 发码（dev 环境回带随机验证码）
    r = client.post("/api/auth/code", json={"phone": phone})
    assert r.status_code == 200, f"发码失败: {r.text}"
    dev_code = r.json()["data"].get("devCode")
    assert dev_code, f"dev 环境应回带验证码: {r.text}"

    # 2) 用随机码登录
    r = client.post("/api/auth/login", json={"phone": phone, "code": dev_code})
    assert r.status_code == 200, f"登录失败: {r.text}"
    return r.json()["data"]
