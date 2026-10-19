"""腾讯云短信 Provider（TencentSmsProvider）合并回归。

来源：服务器快照对账（#42）→ 生产部署前置（#48）。服务器在跑的代码含
``TencentSmsProvider``（纯标准库 TC3-HMAC-SHA256 签名，无 SDK 依赖），
仓库此前只有 Mock + NotImplemented 两通道，``SMS_PROVIDER=tencent`` 会落到
NotImplemented——本文件把该缺口焊死。

纪律（与仓库 N1 一致）：
    * 全程 mock：``urllib.request.urlopen`` 打桩，禁止真连腾讯云；
    * golden 断言：签名/请求体用预先独立计算的基准值锁定，实现被改坏即红；
    * mock 通道行为不回退（旧契约逐字保持）。

对应实现：``app/services/sms.py`` + ``app/core/config.py`` +
``deploy/.env.production.example`` + ``app/services/auth_service.py``（生产守卫）。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, List

import pytest

from app.core.config import settings
from app.services import auth_service, sms


# ------------------------------------------------------------------ 夹具
@pytest.fixture(autouse=True)
def _reset_sms(monkeypatch) -> None:
    """每个用例复位 SMS 配置、单例与验证码存储，避免相互污染。"""
    monkeypatch.setattr(settings, "sms_provider", "mock")
    monkeypatch.setattr(settings, "sms_tencent_secret_id", "AKIDgoldentest")
    monkeypatch.setattr(settings, "sms_tencent_secret_key", "SECRETgoldentest")
    monkeypatch.setattr(settings, "sms_tencent_sdk_app_id", "1400000000")
    monkeypatch.setattr(settings, "sms_tencent_sign_name", "数学小老师")
    monkeypatch.setattr(settings, "sms_tencent_template_id", "1000000")
    monkeypatch.setattr(settings, "sms_tencent_endpoint", "sms.tencentcloudapi.com")
    monkeypatch.setattr(settings, "sms_tencent_region", "ap-guangzhou")
    monkeypatch.setattr(settings, "app_env", "test")
    sms._provider_instance = None
    sms.MockSmsProvider._reset()
    sms.TencentSmsProvider._codes.clear()
    yield
    sms._provider_instance = None
    sms.MockSmsProvider._reset()
    sms.TencentSmsProvider._codes.clear()


class _FakeResponse:
    """假 urlopen 响应（支持 with 语法 + read）。"""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def _stub_urlopen(
    monkeypatch,
    *,
    body: bytes = b'{"Response":{}}',
    error: BaseException | None = None,
) -> List[urllib.request.Request]:
    """把 ``urllib.request.urlopen`` 换成桩，返回捕获到的 Request 列表。"""
    captured: List[urllib.request.Request] = []

    def fake_urlopen(req, timeout=None):  # noqa: ANN001
        captured.append(req)
        if error is not None:
            raise error
        return _FakeResponse(body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def _headers_lower(req: urllib.request.Request) -> dict:
    return {k.lower(): v for k, v in req.headers.items()}


# ------------------------------------------------------------------ 工厂选择
def test_factory_selects_tencent_provider(monkeypatch):
    monkeypatch.setattr(settings, "sms_provider", "tencent")
    provider = sms.get_provider()
    assert isinstance(provider, sms.TencentSmsProvider)
    assert provider.name == sms.PROVIDER_TENCENT == "tencent"


def test_factory_mock_is_default(monkeypatch):
    assert isinstance(sms.get_provider(), sms.MockSmsProvider)


def test_factory_unknown_provider_falls_back_to_not_implemented(monkeypatch):
    monkeypatch.setattr(settings, "sms_provider", "aliyun")
    provider = sms.get_provider()
    assert isinstance(provider, sms.NotImplementedSmsProvider)
    with pytest.raises(NotImplementedError):
        provider.send_code("13800138000")


# ------------------------------------------------------------------ TC3 签名 golden
def test_send_sms_request_matches_golden(monkeypatch):
    """固定时间/验证码下，请求体与 Authorization 必须等于独立预计算的基准值。

    golden 值由同一 TC3 算法在固定输入下独立算出，任何人改错
    canonical request / 密钥派生 / 签名头，本条即红。
    """
    monkeypatch.setattr(time, "time", lambda: 1700000000.0)
    monkeypatch.setattr(sms.random, "randint", lambda a, b: 123456)
    captured = _stub_urlopen(monkeypatch)

    provider = sms.TencentSmsProvider()
    assert provider.send_code("13800138000") is True

    assert len(captured) == 1
    req = captured[0]
    assert req.full_url == "https://sms.tencentcloudapi.com/"
    assert req.get_method() == "POST"
    assert req.data.decode("utf-8") == (
        '{"PhoneNumberSet":["+8613800138000"],"SmsSdkAppId":"1400000000",'
        '"SignName":"数学小老师","TemplateId":"1000000","TemplateParamSet":["123456"]}'
    )
    headers = _headers_lower(req)
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert headers["x-tc-action"] == "SendSms"
    assert headers["x-tc-version"] == "2021-01-11"
    assert headers["x-tc-region"] == "ap-guangzhou"
    assert headers["x-tc-timestamp"] == "1700000000"
    assert headers["authorization"] == (
        "TC3-HMAC-SHA256 Credential=AKIDgoldentest/2023-11-14/sms/tc3_request, "
        "SignedHeaders=content-type;host;x-tc-action, "
        "Signature=b22342fbc02b76b40281cb76d75101535cd2a00932ca5855b0b03f1d60ff013f"
    )


def test_endpoint_and_region_overridable(monkeypatch):
    monkeypatch.setattr(settings, "sms_tencent_endpoint", "sms.custom.example.com")
    monkeypatch.setattr(settings, "sms_tencent_region", "ap-shanghai")
    captured = _stub_urlopen(monkeypatch)

    sms.TencentSmsProvider().send_code("13800138000")

    assert captured[0].full_url == "https://sms.custom.example.com/"
    assert _headers_lower(captured[0])["x-tc-region"] == "ap-shanghai"


# ------------------------------------------------------------------ 校验/错误路径
def test_missing_config_raises_with_key_names_and_no_ghost_code(monkeypatch):
    monkeypatch.setattr(settings, "sms_tencent_secret_id", "")
    monkeypatch.setattr(settings, "sms_tencent_template_id", "  ")
    provider = sms.TencentSmsProvider()

    with pytest.raises(RuntimeError, match=r"SMS_TENCENT_SECRET_ID.*SMS_TENCENT_TEMPLATE_ID"):
        provider.send_code("13800138000")

    # 缺配置时验证码不得残留（幽灵码）
    assert "13800138000" not in provider._codes


def test_api_error_response_raises(monkeypatch):
    _stub_urlopen(monkeypatch, body=json.dumps(
        {"Response": {"Error": {"Code": "AuthFailure.SignatureFailure",
                                "Message": "signature is incorrect"}}},
        ensure_ascii=False,
    ).encode("utf-8"))
    provider = sms.TencentSmsProvider()

    with pytest.raises(RuntimeError, match="AuthFailure.SignatureFailure"):
        provider.send_code("13800138000")
    assert "13800138000" not in provider._codes


def test_network_error_raises_and_cleans_ghost_code(monkeypatch):
    _stub_urlopen(monkeypatch, error=urllib.error.URLError("connection refused"))
    provider = sms.TencentSmsProvider()

    with pytest.raises(RuntimeError, match="网络错误"):
        provider.send_code("13800138000")
    assert "13800138000" not in provider._codes
    assert provider.verify_code("13800138000", "000000") is False


def test_non_json_response_raises(monkeypatch):
    _stub_urlopen(monkeypatch, body=b"<html>502 Bad Gateway</html>")
    provider = sms.TencentSmsProvider()

    with pytest.raises(RuntimeError, match="返回异常"):
        provider.send_code("13800138000")


# ------------------------------------------------------------------ 通道行为
def test_tencent_verify_flow_is_one_time(monkeypatch):
    _stub_urlopen(monkeypatch)
    provider = sms.TencentSmsProvider()
    provider.send_code("13800138000")

    code = provider._codes["13800138000"][0]
    assert len(code) == sms.CODE_LENGTH
    assert provider.verify_code("13800138000", code) is True
    assert provider.verify_code("13800138000", code) is False  # 一次性，防重放


def test_mock_provider_behavior_unchanged(monkeypatch):
    """mock 通道旧契约不回退：随机码 + 内存存储 + 一次性 + dev 回传。"""
    provider = sms.get_provider()
    assert provider.send_code("13800138000") is True
    code = provider.get_last_sent_code("13800138000")
    assert code is not None and len(code) == sms.CODE_LENGTH
    assert provider.verify_code("13800138000", code) is True
    assert provider.verify_code("13800138000", code) is False
    assert provider.verify_code("13800138000", "000000") is False


# ------------------------------------------------------------------ 生产守卫（auth_service 配套）
def test_production_mock_guard_blocks_silent_fake_send(monkeypatch):
    """生产环境 mock 通道必须显式报错，不能「发码成功但用户收不到」。"""
    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "sms_provider", "mock")
    sms._provider_instance = None

    with pytest.raises(auth_service.ApiError) as exc_info:
        auth_service.send_login_code("13700137000")
    assert "尚未接入" in str(exc_info.value)


def test_production_tencent_never_returns_dev_code(monkeypatch):
    """生产 + tencent：正常下发，且验证码一律不回传前端。"""
    monkeypatch.setattr(settings, "app_env", "prod")
    monkeypatch.setattr(settings, "sms_provider", "tencent")
    sms._provider_instance = None
    _stub_urlopen(monkeypatch)

    result = auth_service.send_login_code("13600136000")
    assert result["sent"] is True
    assert result["devCode"] is None


# ------------------------------------------------------------------ 配置注入
def test_settings_reads_tencent_env_vars(monkeypatch):
    """字段名 ↔ 环境变量名映射（pydantic-settings 默认大小写不敏感）。"""
    from app.core.config import Settings

    monkeypatch.setenv("SMS_TENCENT_SECRET_ID", "AKIDfromenv")
    monkeypatch.setenv("SMS_TENCENT_SECRET_KEY", "KEYfromenv")
    monkeypatch.setenv("SMS_TENCENT_SDK_APP_ID", "1400000001")
    s = Settings()
    assert s.sms_tencent_secret_id == "AKIDfromenv"
    assert s.sms_tencent_secret_key == "KEYfromenv"
    assert s.sms_tencent_sdk_app_id == "1400000001"
    # 默认值锚点
    assert s.sms_tencent_endpoint == "sms.tencentcloudapi.com"
    assert s.sms_tencent_region == "ap-guangzhou"
