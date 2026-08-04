"""测试公共夹具（pytest fixtures）。

要点：
    * **必须在 import app 之前** 把 ``DATABASE_URL`` 设成内存 SQLite，
      因为 ``app.db`` 在导入时即按环境变量构建引擎（单例）。
    * 提供 ``client``：基于 ``fastapi.TestClient`` 的测试客户端。
    * 提供 ``make_token``：造一个合法 access JWT 的 helper。
    * 提供 ``new_phone``（T04 新增）：产出全局唯一手机号，用于隔离学生数据。
"""

from __future__ import annotations

import itertools
import os

# ⚠️ 必须最先执行：在 import app 任何模块前固定测试库为内存 SQLite
os.environ["DATABASE_URL"] = os.environ.get("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.security import (  # noqa: E402
    ROLE_STUDENT,
    create_access_token,
)
from app.main import app  # noqa: E402


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

    def _headers(sub: str = "1", role: str = ROLE_STUDENT, sid: str | None = "1") -> dict:
        return {"Authorization": f"Bearer {make_token(sub=sub, role=role, sid=sid)}"}

    return _headers


#: 全局自增序列，保证同一次 pytest 会话内手机号不重复
_PHONE_SEQ = itertools.count(1)


@pytest.fixture
def new_phone():
    """产出一个全局唯一的合法手机号（T04 新增）。

    为什么需要它：
        测试库是**会话级共享**的内存 SQLite，同一个手机号会命中同一个学生，
        导致 T04 的画像统计被其他用例的数据污染（``total_kp`` 之类的断言随
        执行顺序漂移）。每个用例取一个新手机号 = 一个干净的新学生。

    手机号格式必须匹配 ``app.services.sms.PHONE_PATTERN``（``^1[3-9]\\d{9}$``），
    这里用 ``139`` + 8 位序号拼出 11 位。

    Usage:
        phone = new_phone()
    """

    def _make() -> str:
        return f"139{next(_PHONE_SEQ):08d}"

    return _make


__all__ = ["client", "make_token", "auth_headers", "new_phone", "_create_schema"]
