"""TTS provider 切换 / 降级 / 契约回归。

原则（与仓库既有测试一致）：
    * **全程 mock / 打桩**：不联网、不依赖真实 SDK、不依赖火山凭据；
    * 只用 ``monkeypatch`` 改配置与 provider 方法，跑完自动还原；
    * 覆盖三件事：provider 选择逻辑、失败降级路径、对外响应契约。

对应改造：``app/services/tts_service.py`` + ``app/api/tts.py``
（文档见 ``docs/tts-provider-switch.md``）。
"""

from __future__ import annotations

import builtins
from typing import Any, Dict, List

import pytest

from app.api import tts as tts_api
from app.core.config import settings
from app.services import tts_service

#: 兜底 provider 的桩音频（不是真实 mp3，只用于断言字节流原样透传）
FAKE_AUDIO: bytes = b"FAKE-AUDIO-BYTES"
FAKE_MIME: str = "audio/mpeg"


@pytest.fixture(autouse=True)
def _reset_tts(monkeypatch) -> None:
    """每个用例复位 TTS 配置并清空音频缓存，避免用例间相互污染。"""
    monkeypatch.setattr(settings, "tts_provider", tts_service.PROVIDER_EDGE)
    monkeypatch.setattr(settings, "tts_fallback_enabled", True)
    monkeypatch.setattr(settings, "tts_voice", "")
    monkeypatch.setattr(settings, "tts_volc_app_id", "")
    monkeypatch.setattr(settings, "tts_volc_access_token", "")
    tts_api._CACHE.clear()
    yield
    tts_api._CACHE.clear()


def _stub_provider(
    monkeypatch,
    provider_cls: type,
    *,
    audio: bytes = FAKE_AUDIO,
    mime: str = FAKE_MIME,
    error: BaseException | None = None,
) -> List[Dict[str, Any]]:
    """给指定 provider 的 ``synthesize`` 打桩，返回记录到的调用参数列表。

    Args:
        monkeypatch: pytest 的 monkeypatch 夹具。
        provider_cls: 要打桩的 provider 类（如 ``tts_service.VolcTtsProvider``）。
        audio: 桩返回的音频字节。
        mime: 桩返回的 mime。
        error: 非空时桩改为抛出该异常（用于验证降级）。

    Returns:
        调用记录列表，元素形如 ``{"text": ..., "voice": ..., "rate": ...}``。
    """
    calls: List[Dict[str, Any]] = []

    async def _fake(self, text: str, *, voice: str | None = None, rate: str | None = None):
        calls.append({"text": text, "voice": voice, "rate": rate})
        if error is not None:
            raise error
        return audio, mime

    monkeypatch.setattr(provider_cls, "synthesize", _fake)
    return calls


# ------------------------------------------------------------------ provider 选择
def test_resolve_provider_defaults_to_edge() -> None:
    """未配置时默认 edge（内测期零成本 + 最终兜底）。"""
    assert tts_service.resolve_provider_name() == tts_service.PROVIDER_EDGE
    assert tts_service.resolve_provider_name(None) == tts_service.PROVIDER_EDGE


def test_resolve_provider_normalizes_and_rejects_unknown(monkeypatch) -> None:
    """大小写/空格归一化；未知值一律回落 edge，保证「配错也能出声」。 """
    monkeypatch.setattr(settings, "tts_provider", "  VOLC ")
    assert tts_service.resolve_provider_name() == tts_service.PROVIDER_VOLC

    monkeypatch.setattr(settings, "tts_provider", " Piper")
    assert tts_service.resolve_provider_name() == tts_service.PROVIDER_PIPER

    monkeypatch.setattr(settings, "tts_provider", "not-a-provider")
    assert tts_service.resolve_provider_name() == tts_service.PROVIDER_EDGE

    # 显式入参优先级高于配置
    monkeypatch.setattr(settings, "tts_provider", "volc")
    assert tts_service.resolve_provider_name("edge") == tts_service.PROVIDER_EDGE


def test_get_provider_returns_matching_instance() -> None:
    """按名字拿到对应 provider 实例（未知名字给 edge）。"""
    assert isinstance(tts_service.get_provider("volc"), tts_service.VolcTtsProvider)
    assert isinstance(tts_service.get_provider("piper"), tts_service.PiperTtsProvider)
    assert isinstance(tts_service.get_provider("bogus"), tts_service.EdgeTtsProvider)


# ------------------------------------------------------------------ 嗓音 / 语速
def test_resolve_voice_edge_aliases() -> None:
    """edge 侧沿用原有别名表，未命中回落默认小云朵。"""
    assert tts_service.resolve_voice("edge", "xiaoxiao") == "zh-CN-XiaoxiaoNeural"
    assert tts_service.resolve_voice("edge", "YUNXI") == "zh-CN-YunxiNeural"
    assert tts_service.resolve_voice("edge", None) == tts_service.DEFAULT_VOICE
    assert tts_service.resolve_voice("edge", "unknown-voice") == tts_service.DEFAULT_VOICE


def test_resolve_voice_volc_and_override(monkeypatch) -> None:
    """volc 侧映射到火山音色 ID；BV* 直通；全局 ``tts_voice`` 可覆盖。"""
    assert tts_service.resolve_voice("volc", "xiaoxiao") == "BV001_streaming"
    assert tts_service.resolve_voice("volc", "BV700_streaming") == "BV700_streaming"
    assert tts_service.resolve_voice("volc", None) == tts_service.VOLC_DEFAULT_VOICE_TYPE

    monkeypatch.setattr(settings, "tts_voice", "zh-CN-YunxiNeural")
    assert tts_service.resolve_voice("edge", None) == "zh-CN-YunxiNeural"


def test_normalize_rate_and_speed_ratio() -> None:
    """语速归一化：百分比可带可不带符号；非法值忽略。"""
    assert tts_service.normalize_rate("+10%") == "+10%"
    assert tts_service.normalize_rate("10") == "+10%"
    assert tts_service.normalize_rate("-20%") == "-20%"
    assert tts_service.normalize_rate(None) is None
    assert tts_service.normalize_rate("很快一点") is None
    assert tts_service._rate_to_speed_ratio("+10%") == pytest.approx(1.1)
    assert tts_service._rate_to_speed_ratio(None) == pytest.approx(1.0)


# ------------------------------------------------------------------ 合成与降级
async def test_synthesize_uses_configured_provider(monkeypatch) -> None:
    """配置为 volc 且正常时，走 volc，不打扰 edge。"""
    monkeypatch.setattr(settings, "tts_provider", "volc")
    volc_calls = _stub_provider(monkeypatch, tts_service.VolcTtsProvider, audio=b"VOLC")
    edge_calls = _stub_provider(
        monkeypatch,
        tts_service.EdgeTtsProvider,
        error=AssertionError("volc 可用时不应回落 edge"),
    )

    audio, mime = await tts_service.synthesize("你好呀", voice="xiaoxiao", rate="+10%")

    assert audio == b"VOLC"
    assert mime == FAKE_MIME
    assert len(volc_calls) == 1
    assert edge_calls == []
    assert volc_calls[0]["text"] == "你好呀"
    assert volc_calls[0]["voice"] == "xiaoxiao"
    assert volc_calls[0]["rate"] == "+10%"


async def test_volc_missing_credentials_falls_back_to_edge(monkeypatch) -> None:
    """volc 凭据缺失 -> 抛 TtsProviderUnavailable -> 自动降级到 edge（不发网络请求）。"""
    monkeypatch.setattr(settings, "tts_provider", "volc")
    monkeypatch.setattr(settings, "tts_volc_app_id", "")  # 凭据未配置
    edge_calls = _stub_provider(monkeypatch, tts_service.EdgeTtsProvider)

    audio, mime = await tts_service.synthesize("这道题怎么做")

    assert audio == FAKE_AUDIO
    assert mime == FAKE_MIME
    assert len(edge_calls) == 1  # 确实回落到了 edge

    # 直接调用 volc：凭据缺失必须抛「不可用」而不是静默失败
    with pytest.raises(tts_service.TtsProviderUnavailable, match="TTS_VOLC_APP_ID"):
        await tts_service.VolcTtsProvider().synthesize("你好")


async def test_volc_falls_back_when_token_missing(monkeypatch) -> None:
    """app_id 有值但 token 与 ARK_API_KEY 都为空时，同样降级。"""
    monkeypatch.setattr(settings, "tts_provider", "volc")
    monkeypatch.setattr(settings, "tts_volc_app_id", "fake-app-id")
    monkeypatch.setattr(settings, "tts_volc_access_token", "")
    monkeypatch.setattr(settings, "ark_api_key", "")
    _stub_provider(monkeypatch, tts_service.EdgeTtsProvider)

    audio, _ = await tts_service.synthesize("你好")
    assert audio == FAKE_AUDIO


async def test_volc_reuses_ark_api_key(monkeypatch) -> None:
    """未单独配 TTS token 时复用方舟 Key（同一个火山账号）。"""
    monkeypatch.setattr(settings, "tts_volc_app_id", "app-1")
    monkeypatch.setattr(settings, "tts_volc_access_token", "")
    monkeypatch.setattr(settings, "ark_api_key", "ark-token-xxx")

    app_id, token = tts_service.VolcTtsProvider()._credentials()
    assert app_id == "app-1"
    assert token == "ark-token-xxx"


async def test_volc_dependency_missing_degrades_to_edge(monkeypatch) -> None:
    """模拟 httpx 未安装：volc 抛清晰异常，绝不拖垮进程，并降级到 edge。"""
    real_import = builtins.__import__

    def _fake_import(name: str, *args, **kwargs):
        if name == "httpx":
            raise ImportError("No module named 'httpx'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    # 凭据先配好，确保走到「依赖缺失」这一支
    monkeypatch.setattr(settings, "tts_volc_app_id", "fake-app-id")
    monkeypatch.setattr(settings, "tts_volc_access_token", "fake-token")

    with pytest.raises(tts_service.TtsProviderUnavailable, match="httpx"):
        await tts_service.VolcTtsProvider().synthesize("你好")

    # 走统一入口时降级到 edge
    monkeypatch.setattr(settings, "tts_provider", "volc")
    edge_calls = _stub_provider(monkeypatch, tts_service.EdgeTtsProvider)
    audio, _ = await tts_service.synthesize("你好")
    assert audio == FAKE_AUDIO
    assert len(edge_calls) == 1


async def test_edge_dependency_missing_raises_clear_error(monkeypatch) -> None:
    """edge-tts 未安装时给出明确提示（而不是 NoneType 之类的诡异报错）。"""
    real_import = builtins.__import__

    def _fake_import(name: str, *args, **kwargs):
        if name == "edge_tts":
            raise ImportError("No module named 'edge_tts'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(tts_service.TtsProviderUnavailable, match="edge-tts"):
        await tts_service.EdgeTtsProvider().synthesize("你好")


async def test_piper_not_implemented_falls_back_to_edge(monkeypatch) -> None:
    """piper 尚未实现：直接调用抛 NotImplementedError，统一入口降级到 edge。"""
    with pytest.raises(NotImplementedError, match="piper"):
        await tts_service.PiperTtsProvider().synthesize("你好")

    monkeypatch.setattr(settings, "tts_provider", "piper")
    edge_calls = _stub_provider(monkeypatch, tts_service.EdgeTtsProvider)
    audio, _ = await tts_service.synthesize("你好")
    assert audio == FAKE_AUDIO
    assert len(edge_calls) == 1


async def test_fallback_disabled_does_not_degrade(monkeypatch) -> None:
    """关掉兜底开关后，volc 失败应直接抛错（便于上线前暴露配置问题）。"""
    monkeypatch.setattr(settings, "tts_provider", "volc")
    monkeypatch.setattr(settings, "tts_fallback_enabled", False)
    _stub_provider(
        monkeypatch,
        tts_service.VolcTtsProvider,
        error=tts_service.TtsError("火山 TTS 返回 HTTP 500"),
    )
    edge_calls = _stub_provider(monkeypatch, tts_service.EdgeTtsProvider)

    with pytest.raises(tts_service.TtsError, match="火山 TTS"):
        await tts_service.synthesize("你好")
    assert edge_calls == []


async def test_empty_text_raises() -> None:
    """空文本直接报错，由 API 层转成 400。"""
    with pytest.raises(tts_service.TtsError, match="text 为空"):
        await tts_service.synthesize("   ")


async def test_text_is_truncated(monkeypatch) -> None:
    """超长文本按上限截断，避免单请求过大。"""
    calls = _stub_provider(monkeypatch, tts_service.EdgeTtsProvider)
    await tts_service.synthesize("啊" * (tts_service.MAX_TEXT_LEN + 500))
    assert len(calls) == 1
    assert len(calls[0]["text"]) == tts_service.MAX_TEXT_LEN


async def test_all_providers_fail_raises_tts_error(monkeypatch) -> None:
    """volc 与 edge 都失败 -> 抛 TtsError，错误信息带全部尝试过的通道。"""
    monkeypatch.setattr(settings, "tts_provider", "volc")
    _stub_provider(monkeypatch, tts_service.VolcTtsProvider, error=tts_service.TtsError("volc down"))
    _stub_provider(monkeypatch, tts_service.EdgeTtsProvider, error=tts_service.TtsError("edge down"))

    with pytest.raises(tts_service.TtsError) as exc_info:
        await tts_service.synthesize("你好")
    message = str(exc_info.value)
    assert "volc" in message and "edge" in message


# ------------------------------------------------------------------ 自检信息
def test_provider_status_and_describe(monkeypatch) -> None:
    """自检接口数据结构：三个通道齐备，volc 凭据缺失时明确不可用。"""
    monkeypatch.setattr(settings, "tts_provider", "volc")
    monkeypatch.setattr(settings, "tts_volc_app_id", "")

    status = tts_service.provider_status()
    assert [item["name"] for item in status] == ["edge", "volc", "piper"]
    volc_item = next(item for item in status if item["name"] == "volc")
    assert volc_item["available"] is False
    assert "TTS_VOLC_APP_ID" in volc_item["reason"]

    desc = tts_service.describe()
    assert desc["provider"] == "volc"
    assert desc["fallbackEnabled"] is True
    assert desc["fallbackProvider"] == tts_service.PROVIDER_EDGE
    assert desc["maxTextLength"] == tts_service.MAX_TEXT_LEN


def test_no_top_level_tts_sdk_imports() -> None:
    """静态守卫：TTS 依赖必须在函数内 lazy import，模块顶层不许出现。

    这条断言的意义：SDK / edge-tts 缺失时**应用仍可启动**，只影响对应通道。
    """
    for module in (tts_service, tts_api):
        source_lines = _read_source(module).splitlines()
        offenders = [
            line
            for line in source_lines
            if line[:1] not in ("", " ")
            and (
                line.startswith("import edge_tts")
                or line.startswith("from edge_tts")
                or line.startswith("import httpx")
                or line.startswith("from httpx")
                or line.startswith("import volcengine")
                or line.startswith("from volcengine")
            )
        ]
        assert offenders == [], f"{module.__name__} 顶层出现第三方 TTS 依赖导入: {offenders}"


def _read_source(module) -> str:
    """读取模块源码文本（供静态守卫用）。"""
    from pathlib import Path

    return Path(module.__file__).read_text(encoding="utf-8")


# ------------------------------------------------------------------ API 契约
def test_post_tts_contract(client, monkeypatch) -> None:
    """POST /api/tts 契约不变：200 + audio/mpeg 字节流，并命中缓存。"""
    calls = _stub_provider(monkeypatch, tts_service.EdgeTtsProvider)

    r = client.post("/api/tts", json={"text": "小朋友你好呀", "voice": "xiaoxiao"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert r.content == FAKE_AUDIO
    assert len(calls) == 1

    # 同一句话第二次走缓存，不重复合成
    r2 = client.post("/api/tts", json={"text": "小朋友你好呀", "voice": "xiaoxiao"})
    assert r2.status_code == 200
    assert r2.content == FAKE_AUDIO
    assert len(calls) == 1


def test_post_tts_degrades_volc_failure(client, monkeypatch) -> None:
    """volc 失败时接口仍返回 200（内部已降级到 edge），前端零感知。"""
    monkeypatch.setattr(settings, "tts_provider", "volc")
    monkeypatch.setattr(settings, "tts_volc_app_id", "app-1")
    monkeypatch.setattr(settings, "tts_volc_access_token", "token-1")
    _stub_provider(monkeypatch, tts_service.VolcTtsProvider, error=tts_service.TtsError("volc 500"))
    edge_calls = _stub_provider(monkeypatch, tts_service.EdgeTtsProvider, audio=b"EDGE-AUDIO")

    r = client.post("/api/tts", json={"text": "你好"})
    assert r.status_code == 200
    assert r.content == b"EDGE-AUDIO"
    assert len(edge_calls) == 1


def test_post_tts_empty_text_returns_400(client) -> None:
    """空文本：400 + text/plain，与原实现一致。"""
    r = client.post("/api/tts", json={"text": "   "})
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("text/plain")


def test_get_providers_contract(client) -> None:
    """GET /api/tts/providers：统一响应 + provider 清单（只读，不联网）。"""
    r = client.get("/api/tts/providers")
    assert r.status_code == 200

    body = r.json()
    assert body["code"] == 0
    assert body["msg"] == "ok"
    assert body["data"]["provider"] == "edge"
    assert body["data"]["fallbackEnabled"] is True
    assert [item["name"] for item in body["data"]["providers"]] == ["edge", "volc", "piper"]
    for item in body["data"]["providers"]:
        assert isinstance(item["available"], bool)
        assert isinstance(item["reason"], str)


def test_get_providers_voice_default_edge(client) -> None:
    """GET /api/tts/providers 的 voice 字段：edge 生效时下发前端别名 'xiaoxiao'。

    前端拿它回传 POST /api/tts 可被 resolve_voice 正确解析，且与改造前
    前端硬编码的值一致——默认路径零感知。
    """
    r = client.get("/api/tts/providers")
    assert r.status_code == 200
    assert r.json()["data"]["voice"] == "xiaoxiao"


def test_get_providers_voice_follows_volc_voice_type(client, monkeypatch) -> None:
    """volc 生效时 voice 跟随 TTS_VOLC_VOICE_TYPE：运营改配置即对前端默认路径生效。

    这条正是本次要修的陷阱的反面：前端不再硬编码 'xiaoxiao'（别名命中会
    优先映射到 BV001_streaming），而是用后端下发的配置值。
    """
    monkeypatch.setattr(settings, "tts_provider", "volc")
    monkeypatch.setattr(settings, "tts_volc_app_id", "app-1")
    monkeypatch.setattr(settings, "tts_volc_access_token", "token-1")
    monkeypatch.setattr(settings, "tts_volc_voice_type", "BV002_streaming")

    r = client.get("/api/tts/providers")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["provider"] == "volc"
    assert data["voice"] == "BV002_streaming"


__all__ = ["FAKE_AUDIO", "FAKE_MIME"]
