"""云端语音合成接口（TTS）。

给不会看题/不会打字的小朋友用「更甜」的云端嗓音读题、讲题。

选型：微软 Edge TTS（``edge-tts``）——**免 API Key、国内可达、不用翻墙**，
默认 ``zh-CN-XiaoxiaoNeural``（甜美小云朵女声，最贴小朋友）。
前端 ``speak()`` 会云端优先、失败自动回退到浏览器自带嗓音，所以本接口挂了也不影响使用。

设计：
    * POST /api/tts  body={text, voice?}  ->  audio/mpeg（mp3 字节流）；
    * 服务端按 ``(voice, text)`` 做内存缓存，同一句话反复读不重复联网；
    * 文本上限 2000 字，超长截断（避免单请求过大）。
"""
from __future__ import annotations

import io

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel

import edge_tts

router = APIRouter(prefix="/tts", tags=["tts"])

#: 小朋友友好嗓音清单（前端可传 key 切换，默认甜美小云朵）。
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
VOICE_ALIASES = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",  # 甜美小云朵（默认，最适合低年级）
    "yunxi": "zh-CN-YunxiNeural",  # 阳光少年男声
    "xiaoyi": "zh-CN-XiaoyiNeural",  # 温柔女声
}

_CACHE: dict[str, bytes] = {}
_CACHE_MAX = 200
_MAX_TEXT = 2000


class TtsRequest(BaseModel):
    text: str
    voice: str | None = None


@router.post("")
async def tts(req: TtsRequest) -> Response:
    text = (req.text or "").strip()
    if not text:
        return Response(b"", status_code=400, media_type="text/plain")
    if len(text) > _MAX_TEXT:
        text = text[:_MAX_TEXT]

    voice = VOICE_ALIASES.get((req.voice or "").lower(), DEFAULT_VOICE)
    cache_key = f"{voice}:{text}"
    if cache_key in _CACHE:
        return Response(_CACHE[cache_key], media_type="audio/mpeg")

    try:
        communicate = edge_tts.Communicate(text, voice)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                buf.write(chunk["data"])
        audio = buf.getvalue()
    except Exception as exc:  # 云端偶发抖动 -> 前端会自动回退浏览器嗓音
        return Response(f"TTS 合成失败: {exc}".encode("utf-8"), status_code=502, media_type="text/plain")

    if not audio:
        return Response(b"", status_code=502, media_type="text/plain")

    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[cache_key] = audio
    return Response(audio, media_type="audio/mpeg")
