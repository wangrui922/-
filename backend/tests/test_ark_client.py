"""方舟客户端的超时与重试边界回归。

⏱ **重试契约的唯一真相源是设计文档**，改这里的断言前请先查：

* ``docs/v41-task-breakdown.md`` §3.2：方舟客户端「超时 60s + **重试 1 次**」；
* ``docs/epic6-design.md``：``ark_respond`` 「失败抛 ``ArkError``，已内置 1 次重试」。

两条口径一致：``MAX_RETRY = 1`` → **总尝试次数 = 2**；且 4xx（鉴权/参数类，重试
无意义）**立即失败、一次都不重试**，只有 5xx / 429 才消耗重试配额。本文件把这两
条焊死。

历史坑（2026-10 基线清零时发现）：旧用例叫 ``test_timeout_is_not_retried``，断言
超时「不重试」（``calls == 1``），与上述设计文档**正好相反**，所以在任何实现正确的
环境里都是红的。该断言不是「实现没跟上」，而是「用例自己写反了」。
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Union

import httpx
import pytest

from app.ai import ark_client


class _FlakyClient:
    """按调用次序返回预设结果的假 ``httpx.Client``。

    Args:
        outcomes: 每次 ``post`` 的结果列表。元素为 ``Exception`` 实例表示抛异常，
            整数则视为 HTTP 状态码。列表用完后按最后一项重复。
    """

    def __init__(self, outcomes: Sequence[Union[int, BaseException]]) -> None:
        self.outcomes: List[Union[int, BaseException]] = list(outcomes)
        self.payloads: List[Any] = []

    def __enter__(self) -> "_FlakyClient":
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def post(self, *_args: Any, **kwargs: Any) -> "_FakeResponse":
        """记录本次请求体，然后按剧本返回/抛错。"""
        self.payloads.append(kwargs.get("json", _args))
        index = min(len(self.payloads) - 1, len(self.outcomes) - 1)
        outcome = self.outcomes[index]
        if isinstance(outcome, BaseException):
            raise outcome
        return _FakeResponse(int(outcome))


class _FakeResponse:
    """最小够用的假响应：只提供 ``status_code`` / ``text`` / ``json()``。"""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = f"upstream said {status_code}"

    def json(self) -> Dict[str, Any]:
        return {"output_text": ""}


@pytest.fixture
def inject_ark_client(monkeypatch: pytest.MonkeyPatch):
    """打上方舟配置，并返回「注入假 httpx.Client」的钩子。"""

    monkeypatch.setattr(ark_client.settings, "ark_api_key", "test-key")
    monkeypatch.setattr(ark_client.settings, "ark_model", "test-model")

    def _inject(fake: _FlakyClient) -> None:
        monkeypatch.setattr(ark_client.httpx, "Client", lambda *a, **kw: fake)

    return _inject


def test_timeout_consumes_single_retry_then_fails(inject_ark_client) -> None:
    """超时按「重试 1 次」配额走完：总尝试 2 次，随后抛 ``ArkError('…超时…')``。"""
    fake = _FlakyClient([httpx.ReadTimeout("upstream slow")])
    inject_ark_client(fake)

    with pytest.raises(ark_client.ArkError, match="超时"):
        ark_client.ark_respond(messages=[{"role": "user", "content": "测试"}])

    assert len(fake.payloads) == 2, "超时应用完 1 次重试配额后停止：总尝试恰好 2 次"


def test_client_error_is_not_retried(inject_ark_client) -> None:
    """4xx（鉴权/参数类）重试无意义 → 只发一次、立即失败（不消耗重试配额）。"""
    fake = _FlakyClient([401])
    inject_ark_client(fake)

    with pytest.raises(ark_client.ArkError, match="方舟返回 401"):
        ark_client.ark_respond(messages=[{"role": "user", "content": "测试"}])

    assert len(fake.payloads) == 1, "4xx 不应重试：实际尝试次数应恰好为 1"
