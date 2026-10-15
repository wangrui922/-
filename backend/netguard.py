"""测试卫生守卫：**任何用例都不许发起真实外部 HTTP 请求**。

为什么需要它
------------
报错错题教练的教学效果依赖大模型（方舟 / DeepSeek）。测试里理应用
``FakeArk``（见 ``tests/t03_helpers.py``）把模型调用全部打桩。**只要有一处引用点
没打全**（Python 的 ``from app.ai.ark_client import ark_respond`` 会在 import 时把
函数对象复制进调用方命名空间，``patch`` 打不到），测试就会悄悄打真实 API：

* 有 Key + 有网：测试**通过**（但慢、烧钱、结果不受控）；
* 无 Key / 断网：**同一套代码同一个人，换个环境就红**。

这正是「同一份代码不同人跑出不同结果」的典型来源。本插件在传输层装一道单向阀门，
让这种问题**第一次发生就立刻红在当前文件上**，而不是留到 CI 上让人猜。

放行规则
--------
``TestClient``（``httpx`` + ASGI transport）请求本地 ``http://testserver`` /
``localhost`` / ``127.0.0.1`` 一律放行，只对**外部域名**开火。

临时放行 / 排障
---------------
确有个别用例需要访问外部服务时，用 ``allow_external_http`` marker 显式豁免，
或设环境变量 ``NETGUARD_DISABLED=1`` 整体关掉（仅限本机排障，CI 不应设置）。
"""

from __future__ import annotations

import os
from typing import Any, Iterator, Sequence

import httpx
import pytest

#: 放行的本地 host 片段（ASGI TestClient 的伪 host 也算）
_LOCAL_HOST_FRAGMENTS: Sequence[str] = ("testserver", "localhost", "127.0.0.1", "[::1]")

#: 整体开关（本机排障用；CI 不得设置）
_DISABLE_ENV = "NETGUARD_DISABLED"


def _request_url(args: Sequence[Any]) -> str:
    """从 ``httpx`` 的 send/post 入参里抠出 URL 字符串。"""
    for item in args:
        url = getattr(item, "url", None)
        if url is not None:
            return str(url)
    return ""


def _is_local(url: str) -> bool:
    """URL 是否指向本机测试服务。"""
    if not url:
        # 取不到 URL 时不做拦截，避免误伤
        return True
    return any(host in url for host in _LOCAL_HOST_FRAGMENTS)


def _wrap(original: Any) -> Any:
    """包装 ``httpx`` 的发送方法，只拦外部域名。"""

    def _guarded(*args: Any, **kwargs: Any) -> Any:
        url = _request_url(args)
        if not _is_local(url):
            raise AssertionError(
                "测试期间发起了真实外部 HTTP 请求，说明模型调用没有被完全打桩。\n"
                f"    目标 URL：{url}\n"
                "    处理办法：① 补 patch（注意 'patch where it's used'，调用方命名空间也要打）；\n"
                "              ② 确有必要访问外部服务时，给该用例加 "
                "@pytest.mark.allow_external_http；\n"
                f"              ③ 本机排障可临时设环境变量 {_DISABLE_ENV}=1。"
            )
        return original(*args, **kwargs)

    return _guarded


@pytest.fixture(autouse=True)
def _no_external_http(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """会话内每个用例默认禁止外部 HTTP；``allow_external_http`` marker 可豁免。"""
    if os.environ.get(_DISABLE_ENV):
        yield
        return
    if request.node.get_closest_marker("allow_external_http") is not None:
        yield
        return

    monkeypatch.setattr(httpx.Client, "send", _wrap(httpx.Client.send))
    monkeypatch.setattr(httpx.AsyncClient, "send", _wrap(httpx.AsyncClient.send))
    yield
