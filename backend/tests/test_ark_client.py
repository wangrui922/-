"""方舟客户端的超时与重试边界回归。"""

from __future__ import annotations

import httpx
import pytest

from app.ai import ark_client


def test_timeout_is_not_retried(monkeypatch) -> None:
    """一次慢请求超时后应立即返回，不能再发第二次相同请求。"""
    calls = 0

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("upstream slow")

    monkeypatch.setattr(ark_client.settings, "ark_api_key", "test-key")
    monkeypatch.setattr(ark_client.settings, "ark_model", "test-model")
    monkeypatch.setattr(ark_client, "DEFAULT_TIMEOUT", 7.0)
    monkeypatch.setattr(ark_client.httpx, "Client", FakeClient)

    with pytest.raises(ark_client.ArkError, match="超时"):
        ark_client.ark_respond(messages=[{"role": "user", "content": "测试"}])

    assert calls == 1
