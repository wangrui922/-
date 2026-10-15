"""云端语音合成接口（TTS）——可切换 provider 版。

给不会看题/不会打字的小朋友用「更甜」的嗓音读题、讲题。

对外契约（**与改造前完全一致**，前端零改动）：
    * ``POST /api/tts``  body={text, voice?, rate?}  ->  ``audio/mpeg``（音频字节流）；
    * 失败返回 ``text/plain`` + 5xx（前端会自动回退浏览器自带嗓音）；
    * 文本上限 2000 字，超长截断；按 ``(provider, voice, rate, text)`` 内存缓存。

新增自检接口（统一响应 ``{"code":0,"msg":"ok","data":{}}``）：
    * ``GET /api/tts/providers`` -> 当前 provider、兜底开关与三个通道的可用状态。

嗓音由 ``app.services.tts_service`` 统一解析：
    * 默认 ``TTS_PROVIDER=edge``（免 Key，内测期）；
    * 备案 / 商用切 ``TTS_PROVIDER=volc``（火山引擎），详见 ``docs/tts-provider-switch.md``；
    * 任何 provider 失败且 ``tts_fallback_enabled`` 为真时，自动降级到 edge。
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel

from app.core.response import ok
from app.services import tts_service

router = APIRouter(prefix="/tts", tags=["tts"])

#: 音频缓存：key -> (音频字节, mime)。切 provider 后 key 变化，不会命中旧音频。
_CACHE: dict[str, tuple[bytes, str]] = {}
_CACHE_MAX = 200


class TtsRequest(BaseModel):
    """TTS 请求体（camelCase 出入参；``rate`` 为本次新增的可选项）。"""

    text: str
    voice: str | None = None
    rate: str | None = None


@router.post("")
async def tts(req: TtsRequest) -> Response:
    """合成语音，返回音频字节流。

    Args:
        req: 请求体，``text`` 必填（上限 2000 字，超出截断），
            ``voice`` 为嗓音别名（xiaoxiao / yunxi / xiaoyi）或原生音色名，
            ``rate`` 为语速（形如 ``"+10%"``），两者均可省略。

    Returns:
        成功：``audio/mpeg``（或 provider 实际编码对应的 mime）音频字节流；
        空文本：400；合成失败：500（前端自动回退浏览器嗓音）。
    """
    text = (req.text or "").strip()
    if not text:
        return Response(b"", status_code=400, media_type="text/plain")
    if len(text) > tts_service.MAX_TEXT_LEN:
        text = text[: tts_service.MAX_TEXT_LEN]

    # 缓存 key 带上 provider / voice / rate，切换 provider 后不会命中旧音频
    provider = tts_service.resolve_provider_name()
    voice = tts_service.resolve_voice(provider, req.voice)
    rate = tts_service.normalize_rate(req.rate) or ""
    cache_key = f"{provider}:{voice}:{rate}:{text}"

    cached = _CACHE.get(cache_key)
    if cached is not None:
        audio, mime = cached
        return Response(audio, media_type=mime)

    try:
        audio, mime = await tts_service.synthesize(text, voice=req.voice, rate=req.rate)
    except Exception as exc:  # 所有 provider 均失败 -> 前端自动回退浏览器嗓音
        return Response(
            f"TTS 合成失败: {exc}".encode("utf-8"),
            status_code=500,
            media_type="text/plain",
        )

    if not audio:
        return Response(b"", status_code=500, media_type="text/plain")

    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[cache_key] = (audio, mime)
    return Response(audio, media_type=mime)


@router.get("/providers")
async def tts_providers() -> dict:
    """TTS 通道自检（只读，不发起任何网络请求）。

    Returns:
        统一响应 ``{"code":0,"msg":"ok","data":{...}}``，``data`` 含当前
        provider、兜底开关、默认嗓音与三个通道的可用状态，便于前端与运维排查。
    """
    return ok(tts_service.describe())
