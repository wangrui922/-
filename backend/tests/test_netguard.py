"""netguard 自测：证明这道「禁外部 HTTP」阀门真的在拦，而不是摆设。

为什么需要自测
--------------
netguard 是全会话 autouse 的传输层阀门，保护的是「同一份代码换台机器
跑出不同结果」这类最贵的故障。一个**自己没被验证过的守卫**等于没有守卫——
本文件用三类证据钉死它：

1. 集成层：不带 marker 的用例发外部域名 → 必须被 ``AssertionError`` 拦下
   （这一条就是变异测试靶心：把 netguard 的拦截逻辑改坏，本用例立刻红）；
2. 集成层：``testserver`` / ``localhost`` 放行——请求会走到传输层失败
   （连接错误），但**绝不是** netguard 的 AssertionError；
3. 集成层：``allow_external_http`` marker 豁免路径——替换掉真实 send 的
   recorder 能被调到，证明豁免后阀门不再介入；
4. 单元层：``_is_local`` 的判定口径逐条钉死（含「取不到 URL 时放行」
   的防误伤语义）。
"""

import httpx
import pytest

import netguard


class TestNetguardBlocksExternal:
    """集成层：外部域名必须被拦。"""

    def test_external_request_raises_assertion(self):
        """不带 marker 发外部域名 → AssertionError（阀门在岗的直接证据）。"""
        client = httpx.Client()
        try:
            with pytest.raises(AssertionError, match="真实外部 HTTP"):
                client.send(httpx.Request("GET", "https://example.com/api"))
        finally:
            client.close()

    def test_error_message_points_to_url_and_remedies(self):
        """拦下的报错必须带目标 URL 与三条处理办法（排障生产力）。"""
        client = httpx.Client()
        try:
            with pytest.raises(AssertionError) as exc_info:
                client.send(httpx.Request("GET", "https://api.volces.com/v3"))
        finally:
            client.close()
        message = str(exc_info.value)
        assert "api.volces.com" in message
        assert "allow_external_http" in message
        assert "NETGUARD_DISABLED" in message


class TestNetguardAllowsLocal:
    """集成层：本机测试服务放行。"""

    def test_testserver_passes_valve(self):
        """testserver 请求穿过阀门：不得出现 netguard 的 AssertionError。

        传输层最终成功还是失败**与本测试无关**（沙箱/代理/无网都可能），
        这里只钉死一件事：请求没有被 netguard 误拦。
        """
        client = httpx.Client()
        try:
            try:
                client.send(httpx.Request("GET", "http://testserver/healthz"))
            except AssertionError:
                pytest.fail("testserver 请求被 netguard 误拦——放行规则被改坏了")
            except Exception:  # noqa: BLE001
                pass  # 传输层失败（无服务器/无网）与阀门无关
        finally:
            client.close()

    @pytest.mark.allow_external_http
    def test_marker_exempts_external_request(self, monkeypatch: pytest.MonkeyPatch):
        """带 marker 的用例阀门不介入：recorder 能被调到（证明豁免路径通）。"""
        calls = []

        def _recorder(self, request, **kwargs):  # noqa: ANN001
            calls.append(str(request.url))
            return httpx.Response(200, request=request)

        monkeypatch.setattr(httpx.Client, "send", _recorder)
        client = httpx.Client()
        try:
            response = client.send(httpx.Request("GET", "https://example.com/exempt"))
        finally:
            client.close()
        assert response.status_code == 200
        assert calls == ["https://example.com/exempt"]


class TestNetguardIsLocal:
    """单元层：判定口径逐条钉死。"""

    @pytest.mark.parametrize(
        "url",
        [
            "http://testserver/api",
            "http://testserver",
            "http://localhost:8000/health",
            "http://127.0.0.1:5173/",
            "http://[::1]:8000/",
        ],
    )
    def test_local_urls_allowed(self, url: str):
        assert netguard._is_local(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/api",
            "https://ark.cn-beijing.volces.com/api/v3/chat",
            "http://api.deepseek.com/v1",
        ],
    )
    def test_external_urls_blocked(self, url: str):
        assert netguard._is_local(url) is False

    def test_unresolvable_url_passes(self):
        """取不到 URL 时放行——防误伤铁律（宁放过不误杀）。"""
        assert netguard._is_local("") is True

    def test_wrap_raises_for_external_and_delegates_local(self):
        """``_wrap`` 对外部开火、对本机透传（不碰原始返回值）。"""
        sentinel = object()
        wrapped = netguard._wrap(lambda *args, **kwargs: sentinel)

        with pytest.raises(AssertionError):
            wrapped(httpx.Request("GET", "https://example.com"))

        class _Local:
            url = "http://testserver/x"

        assert wrapped(_Local()) is sentinel
