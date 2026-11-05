"""语音合成（TTS）Provider 抽象层。

为什么要有这一层
----------------
读题 / 讲题用的 ``edge-tts`` 是**逆向微软 Edge 在线接口**的产物：免费、免 Key、
国内可达，但**没有官方 SLA，商用存在停服与合规风险**。

- **内测期（60 人免费）**：继续用 ``edge``，零成本、零接入门槛；
- **备案 / 商用阶段**：把 ``TTS_PROVIDER`` 改成 ``volc`` 即可切到火山引擎
  语音技术（有合同、有 SLA），**业务层与前端零改动**；
- **断网 / 内网部署**：``piper`` 是预留的本地离线兜底位，尚未实现。

三个 Provider
-------------
* ``edge``  —— edge-tts，免 Key，**内测期默认，也是最终兜底**；
* ``volc``  —— 火山引擎语音技术 TTS。走 **HTTP 接口 + httpx**（httpx 已是
  本项目的硬依赖），不引入任何需要本地编译的 SDK，Windows 友好；
  SDK（``volcengine-python-sdk``）只是**可选项**，装不装都不影响本模块工作；
  默认 ``v3``（豆包语音合成模型 2.0，流式 NDJSON），可切 ``v1`` 回退经典小模型；
* ``piper`` —— 本地离线兜底，**仅声明接口与配置位**，调用即抛
  ``NotImplementedError``，由 ``synthesize`` 自动降级到 ``edge``。

三条铁律
--------
1. **依赖全部 lazy import**：``edge_tts`` / ``httpx`` 等第三方 import 一律写在
   函数内部；缺依赖只影响对应 provider，**绝不让应用启动失败**；
2. **失败可降级**：``volc`` / ``piper`` 不可用时，只要 ``tts_fallback_enabled``
   为真就自动回落到 ``edge``；``edge`` 也失败才抛 ``TtsError`` 给上层；
3. **密钥零硬编码**：app_id / access_token 一律读 config（环境变量）。
   ★ 语音应用的 Access Token 与方舟 ``ARK_API_KEY`` **不是一套凭据**，
   绝不复用——用错会鉴权失败后静默降级 edge，表现为「以为接了火山其实没接」。

使用示例::

    from app.services import tts_service

    audio, mime = await tts_service.synthesize("这道题怎么做？", voice="xiaoxiao")
"""

from __future__ import annotations

import importlib.util
import io
import json
import logging
import re
import uuid
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger("app.services.tts")

# ------------------------------------------------------------------ Provider 常量
PROVIDER_EDGE: str = "edge"
PROVIDER_VOLC: str = "volc"
PROVIDER_PIPER: str = "piper"

#: 全部受支持的 provider 名（小写）
SUPPORTED_PROVIDERS: Tuple[str, ...] = (PROVIDER_EDGE, PROVIDER_VOLC, PROVIDER_PIPER)

#: 兜底 provider：任何 provider 失败都回落到它
FALLBACK_PROVIDER: str = PROVIDER_EDGE

# ------------------------------------------------------------------ 嗓音常量
#: edge-tts 默认嗓音：甜美「小云朵」女声，最适合低年级小朋友
DEFAULT_VOICE: str = "zh-CN-XiaoxiaoNeural"

#: 前端默认音色别名：``DEFAULT_VOICE`` 对应的简短写法。
#: 供 ``GET /api/tts/providers`` 下发与前端回落使用——前端拿它回传
#: ``POST /api/tts`` 时，各 provider 的 ``resolve_voice`` 都能正确解析。
DEFAULT_VOICE_ALIAS: str = "xiaoxiao"

#: 前端可传的简短别名 -> edge-tts 完整嗓音名（保持与原实现一致）
VOICE_ALIASES: Dict[str, str] = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",  # 甜美小云朵（默认）
    "yunxi": "zh-CN-YunxiNeural",  # 阳光少年男声
    "xiaoyi": "zh-CN-XiaoyiNeural",  # 温柔女声
}

#: 前端别名 -> 火山音色 ID。
#: ★ 豆包语音合成模型 2.0 目前只实测通过一款通用音色
#:   ``zh_female_vv_uranus_bigtts``（2026-11-05 用户试听确认采用），
#:   故三个别名统一指向它；后续拿到更多 2.0 音色再在此扩展。
VOLC_VOICE_ALIASES: Dict[str, str] = {
    "xiaoxiao": "zh_female_vv_uranus_bigtts",
    "xiaoyi": "zh_female_vv_uranus_bigtts",
    "yunxi": "zh_female_vv_uranus_bigtts",
}

#: 火山 TTS 默认音色（别名未命中且未传原生音色 ID 时使用）
VOLC_DEFAULT_VOICE_TYPE: str = "zh_female_vv_uranus_bigtts"

# ------------------------------------------------------------------ 其它常量
#: 单次合成的文本上限（超出截断，避免单请求过大）
MAX_TEXT_LEN: int = 2000

MIME_MP3: str = "audio/mpeg"
MIME_WAV: str = "audio/wav"

#: v1 返回体成功码（3000 = 官方 Success；0 也按成功处理）
VOLC_SUCCESS_CODES: frozenset = frozenset({0, 3000})

#: v3 流式响应码：分片行 code=0；终止行 code=20000000（"OK"）
VOLC_V3_CHUNK_CODE: int = 0
VOLC_V3_DONE_CODE: int = 20000000

#: 火山接入点。v3 = 豆包语音合成模型 2.0 单向流式；v1 = 经典小模型（官方已标不推荐）
VOLC_V3_DEFAULT_BASE_URL: str = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
VOLC_V1_DEFAULT_BASE_URL: str = "https://openspeech.bytedance.com/api/v1/tts"
#: 兼容旧引用（tts_smoke.py 等）：默认指向 v3
VOLC_DEFAULT_BASE_URL: str = VOLC_V3_DEFAULT_BASE_URL
VOLC_DEFAULT_CLUSTER: str = "volcano_tts"  # 仅 v1 使用
#: ★ v3 资源 ID，必须与音色所属资源匹配；2.0 音色固定 seed-tts-2.0
#:   （2026-11-05 实测：10 个 1.0 bigtts 音色配它全报 55000000，只有 2.0 音色出声）
VOLC_DEFAULT_RESOURCE_ID: str = "seed-tts-2.0"

#: 火山 encoding -> 响应 mime
_ENCODING_MIME: Dict[str, str] = {
    "mp3": MIME_MP3,
    "wav": MIME_WAV,
    "ogg": "audio/ogg",
    "pcm": "audio/L16",
}

#: 语速解析：支持 "+10%" / "-10%" / "10%" / "10"
_RATE_PATTERN = re.compile(r"^([+-]?)(\d+(?:\.\d+)?)(%?)$")


class TtsError(Exception):
    """TTS 合成失败（所有 provider 与兜底均不可用时抛出）。"""


class TtsProviderUnavailable(TtsError):
    """某个 provider 当前不可用（缺依赖 / 缺配置）。

    抛出后上层会尝试降级到 ``FALLBACK_PROVIDER``，而不是直接失败。
    """


# ------------------------------------------------------------------ Provider 抽象
class TtsProvider(ABC):
    """语音合成通道抽象。

    子类只需实现 ``synthesize``；**第三方依赖必须在函数内部 import**，
    保证「缺 SDK 也能启动应用、只是该通道不可用」。
    """

    name: str = "base"

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
    ) -> Tuple[bytes, str]:
        """合成语音。

        Args:
            text: 待朗读文本（已做过 strip / 长度截断）。
            voice: 嗓音标识，可为前端别名或 provider 原生音色名；为空用默认。
            rate: 语速，形如 ``"+10%"`` / ``"-10%"``；为空用默认语速。

        Returns:
            ``(音频字节, mime)`` 二元组。

        Raises:
            TtsProviderUnavailable: 依赖缺失或凭据未配置。
            TtsError: 上游调用失败或返回空音频。
            NotImplementedError: provider 尚未实现（如 piper）。
        """
        raise NotImplementedError


class EdgeTtsProvider(TtsProvider):
    """微软 Edge TTS（``edge-tts``）：免 Key、国内可达，内测期默认与最终兜底。"""

    name = PROVIDER_EDGE

    async def synthesize(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
    ) -> Tuple[bytes, str]:
        # lazy import：缺 edge-tts 只影响本通道，不影响应用启动
        try:
            import edge_tts  # type: ignore[import-not-found]
        except ImportError as exc:
            raise TtsProviderUnavailable("edge-tts 未安装（pip install edge-tts）") from exc

        resolved_voice = resolve_voice(PROVIDER_EDGE, voice)
        kwargs: Dict[str, Any] = {}
        edge_rate = normalize_rate(rate)
        if edge_rate:
            kwargs["rate"] = edge_rate

        try:
            communicate = edge_tts.Communicate(text, resolved_voice, **kwargs)
            buf = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    buf.write(chunk["data"])
            audio = buf.getvalue()
        except TtsError:
            raise
        except Exception as exc:  # 上游抖动 / 网络异常，交给上层降级
            raise TtsError(f"edge-tts 合成失败: {exc}") from exc

        if not audio:
            raise TtsError("edge-tts 返回空音频")
        return audio, MIME_MP3


class VolcTtsProvider(TtsProvider):
    """火山引擎语音技术 TTS（HTTP 接口 + httpx）。

    两套协议（``TTS_VOLC_API_VERSION`` 切换，默认 ``v3``）：

    * ``v3`` —— **豆包语音合成模型 2.0**，``/api/v3/tts/unidirectional``，
      ``X-Api-*`` 系列 header，返回 **流式 NDJSON**（逐行一个小 JSON，
      ``data`` 为 base64 MP3 分片，需逐行解码后按顺序拼字节），
      末行 ``code=20000000`` 为正常终止；
    * ``v1`` —— 经典小模型，``/api/v1/tts``，单 JSON + ``data`` base64，
      官方已标「不推荐」，仅作回退保留。

    实测（2026-11-05，真实凭据穷举 2 资源 ID × 10 音色）：
        只有 ``resource-id=seed-tts-2.0`` + ``speaker=zh_female_vv_uranus_bigtts``
        能出声；1.0 的 ``*_moon_bigtts`` / ``*_mars_bigtts`` 音色配它一律报
        ``55000000 resource ID is mismatched with speaker related resource``。
        **resource-id 与音色必须同属一套，切勿混用。**
    """

    name = PROVIDER_VOLC

    # ---------------------------------------------------------------- 配置读取
    def _api_version(self) -> str:
        """规范化协议版本：只认 ``v1``，其余一律按 ``v3`` 处理。"""
        raw = (settings.tts_volc_api_version or "v3").strip().lower()
        return "v1" if raw == "v1" else "v3"

    def _base_url(self, version: str) -> str:
        configured = (settings.tts_volc_base_url or "").strip()
        if configured:
            return configured
        return VOLC_V1_DEFAULT_BASE_URL if version == "v1" else VOLC_V3_DEFAULT_BASE_URL

    def _credentials(self) -> Tuple[str, str]:
        """读取火山凭据：``(app_id, access_token)``。

        ★ 语音应用的 Access Token 与方舟 ``ARK_API_KEY`` **不是一套凭据**，
        绝不做复用——用错会鉴权失败后静默降级 edge，表现为
        「以为接了火山、其实一直在用 edge」，极难排查。

        Raises:
            TtsProviderUnavailable: app_id 或 token 未配置。
        """
        app_id = (settings.tts_volc_app_id or "").strip()
        token = (settings.tts_volc_access_token or "").strip()
        if not app_id:
            raise TtsProviderUnavailable("TTS_VOLC_APP_ID 未配置，火山 TTS 不可用")
        if not token:
            raise TtsProviderUnavailable(
                "TTS_VOLC_ACCESS_TOKEN 未配置（语音应用独立 Token，不可用 ARK_API_KEY 代替），火山 TTS 不可用"
            )
        return app_id, token

    # ---------------------------------------------------------------- 请求构造
    def _build_v1_payload(
        self, app_id: str, token: str, text: str, voice: str, rate: Optional[str]
    ) -> Dict[str, Any]:
        """构造 v1（经典小模型）请求体。"""
        encoding = (settings.tts_volc_encoding or "mp3").strip().lower() or "mp3"
        return {
            "app": {
                "appid": app_id,
                "token": token,
                "cluster": (settings.tts_volc_cluster or VOLC_DEFAULT_CLUSTER).strip(),
            },
            "user": {"uid": f"ai-math-coach-{app_id[-6:] or 'default'}"},
            "audio": {
                "voice_type": voice,
                "encoding": encoding,
                "speed_ratio": _rate_to_speed_ratio(rate),
            },
            "request": {
                "reqid": str(uuid.uuid4()),
                "text": text,
                "text_type": "plain",
                "operation": "query",
            },
        }

    def _build_v3_payload(
        self, app_id: str, text: str, voice: str, rate: Optional[str]
    ) -> Dict[str, Any]:
        """构造 v3（豆包语音合成模型 2.0）请求体。

        ``speech_rate`` 仅在显式传了 rate 时下发（实测 -10/0/10 均被接受）；
        默认不下发该字段，与 2026-11-05 出声探针保持一致。
        """
        encoding = (settings.tts_volc_encoding or "mp3").strip().lower() or "mp3"
        audio_params: Dict[str, Any] = {"format": encoding, "sample_rate": 24000}
        speech_rate = _rate_to_speech_rate(rate)
        if speech_rate is not None:
            audio_params["speech_rate"] = speech_rate
        return {
            "user": {"uid": f"ai-math-coach-{app_id[-6:] or 'default'}"},
            "req_params": {
                "text": text,
                "speaker": voice,
                "audio_params": audio_params,
            },
        }

    def _build_headers(self, app_id: str, token: str, version: str) -> Dict[str, str]:
        """v3 用 ``X-Api-*`` 系列；v1 用 ``Authorization: Bearer;``。"""
        if version == "v1":
            return {
                "Content-Type": "application/json",
                "Authorization": f"Bearer;{token}",
            }
        resource_id = (settings.tts_volc_resource_id or "").strip() or VOLC_DEFAULT_RESOURCE_ID
        return {
            "Content-Type": "application/json",
            "X-Api-App-Id": app_id,
            "X-Api-Access-Key": token,
            "X-Api-Resource-Id": resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }

    # ---------------------------------------------------------------- 响应解析
    @staticmethod
    def _parse_v1_response(data: Any) -> bytes:
        """v1：单 JSON 对象，``data`` 为完整音频的 base64。"""
        import base64

        if not isinstance(data, dict):
            raise TtsError("火山 TTS 返回结构异常：顶层不是 JSON 对象")
        raw_code = data.get("code")
        audio_b64 = data.get("data")
        message = str(data.get("message") or "")
        code_ok = raw_code is None or (isinstance(raw_code, int) and raw_code in VOLC_SUCCESS_CODES)
        if not audio_b64 or not isinstance(audio_b64, str):
            raise TtsError(f"火山 TTS 合成失败：code={raw_code} message={message or '空音频'}")
        if not code_ok:
            raise TtsError(f"火山 TTS 合成失败：code={raw_code} message={message}")
        try:
            audio = base64.b64decode(audio_b64)
        except Exception as exc:
            raise TtsError(f"火山 TTS 音频解码失败: {exc}") from exc
        if not audio:
            raise TtsError("火山 TTS 返回空音频")
        return audio

    @staticmethod
    def _parse_v3_response(body: str) -> bytes:
        """v3：流式 NDJSON，逐行取 ``data`` 分片、**逐行解码后按序拼字节**。

        ★ 必须 ``b"".join(base64.b64decode(each) for each in chunks)``。
          分片各自是独立编码的，若先把 base64 字符串拼起来再解码会解出乱码。

        行形态（2026-11-05 实测，普通句 12 行 / 长句 22 行）：
            ``{"code":0,"message":"","data":"<base64 分片>"}``   × N
            ``{"code":20000000,"message":"OK","data":null}``      ← 正常终止
        """
        import base64

        chunks: List[str] = []
        saw_done = False
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                logger.warning("火山 v3 返回无法解析的行，已跳过: %.80s", line)
                continue
            if not isinstance(event, dict):
                continue
            code = event.get("code")
            if code == VOLC_V3_DONE_CODE:
                saw_done = True
                continue
            if code not in (None, VOLC_V3_CHUNK_CODE):
                raise TtsError(
                    f"火山 TTS 合成失败：code={code} message={event.get('message') or ''}"
                )
            data = event.get("data")
            if isinstance(data, str) and data:
                chunks.append(data)

        if not chunks:
            raise TtsError("火山 TTS v3 未返回任何音频分片（检查 resource-id 与音色是否匹配）")
        try:
            audio = b"".join(base64.b64decode(chunk) for chunk in chunks)
        except Exception as exc:
            raise TtsError(f"火山 TTS v3 音频解码失败: {exc}") from exc
        if not audio:
            raise TtsError("火山 TTS v3 返回空音频")
        if not saw_done:
            logger.warning("火山 v3 响应缺少 code=%s 终止行，音频可能不完整", VOLC_V3_DONE_CODE)
        return audio

    # ---------------------------------------------------------------- 合成入口
    async def synthesize(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
    ) -> Tuple[bytes, str]:
        # 先查凭据：配置缺失是最常见也最容易修的问题，先暴露它（比依赖缺失更明确）
        app_id, token = self._credentials()

        # lazy import：与 volc 相关的第三方依赖一律函数内 import，
        # 缺失只影响本通道，绝不影响应用启动
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError as exc:
            raise TtsProviderUnavailable("httpx 未安装，火山 TTS 不可用") from exc

        version = self._api_version()
        resolved_voice = resolve_voice(PROVIDER_VOLC, voice)
        base_url = self._base_url(version)
        timeout = float(settings.tts_volc_timeout_sec or 15)
        payload = (
            self._build_v1_payload(app_id, token, text, resolved_voice, rate)
            if version == "v1"
            else self._build_v3_payload(app_id, text, resolved_voice, rate)
        )
        headers = self._build_headers(app_id, token, version)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(base_url, json=payload, headers=headers)
        except Exception as exc:  # 网络 / 超时 / DNS
            raise TtsError(f"火山 TTS 请求失败: {exc}") from exc

        if response.status_code >= 400:
            snippet = (response.text or "")[:200]
            raise TtsError(f"火山 TTS 返回 HTTP {response.status_code}: {snippet}")

        if version == "v3":
            audio = self._parse_v3_response(response.text or "")
        else:
            try:
                data = response.json()
            except ValueError as exc:
                raise TtsError(f"火山 TTS 返回非 JSON 内容: {exc}") from exc
            audio = self._parse_v1_response(data)

        encoding = (settings.tts_volc_encoding or "mp3").strip().lower()
        return audio, _ENCODING_MIME.get(encoding, MIME_MP3)


class PiperTtsProvider(TtsProvider):
    """本地离线兜底（piper）：**仅声明接口与配置位，尚未实现**。

    落地时需要：
        1. 本机安装 piper 可执行文件（``TTS_PIPER_EXECUTABLE``）；
        2. 下载中文语音模型（``TTS_PIPER_MODEL_PATH``）；
        3. 在本类的 ``synthesize`` 里改成子进程调用并返回 wav 字节。
    在此之前调用会抛 ``NotImplementedError``，由 ``synthesize`` 自动降级到 edge。
    """

    name = PROVIDER_PIPER

    async def synthesize(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
    ) -> Tuple[bytes, str]:
        raise NotImplementedError(
            "piper 本地 TTS 尚未实现：需先配置 TTS_PIPER_EXECUTABLE 与 "
            "TTS_PIPER_MODEL_PATH 并实现子进程调用；当前请改用 TTS_PROVIDER=edge 或 volc"
        )


#: provider 名 -> 构造工厂（每次调用新建实例，避免测试 monkeypatch 配置后拿到旧单例）
_PROVIDER_FACTORY: Dict[str, Callable[[], TtsProvider]] = {
    PROVIDER_EDGE: EdgeTtsProvider,
    PROVIDER_VOLC: VolcTtsProvider,
    PROVIDER_PIPER: PiperTtsProvider,
}


# ------------------------------------------------------------------ 工具函数
def resolve_provider_name(explicit: Optional[str] = None) -> str:
    """确定本次合成使用哪个 provider。

    Args:
        explicit: 显式指定的 provider（优先级最高），通常来自调用方参数。

    Returns:
        规范化后的 provider 名；非法值一律回落 ``edge``，保证「配置写错也能出声」。
    """
    name = (explicit or settings.tts_provider or PROVIDER_EDGE).strip().lower()
    return name if name in SUPPORTED_PROVIDERS else PROVIDER_EDGE


def resolve_voice(provider: str, voice: Optional[str]) -> str:
    """把「前端别名 / provider 原生音色名 / 空值」解析成该 provider 能用的音色。

    Args:
        provider: provider 名（已规范化）。
        voice: 前端传入的嗓音参数，可为别名（``xiaoxiao``）或原生音色名。

    Returns:
        该 provider 可直接使用的音色标识。
    """
    raw = (voice or "").strip()
    configured = (settings.tts_voice or "").strip()

    if provider == PROVIDER_VOLC:
        if raw.lower() in VOLC_VOICE_ALIASES:
            return VOLC_VOICE_ALIASES[raw.lower()]
        # 原生音色 ID 直通：v1 的 BV*、2.0 的 zh_*（实测音色即 zh_ 开头）
        if raw.upper().startswith("BV") or raw.lower().startswith("zh_"):
            return raw
        return (settings.tts_volc_voice_type or "").strip() or VOLC_DEFAULT_VOICE_TYPE

    # edge / piper：别名命中即用，未命中沿用「回落到默认嗓音」的既有行为
    if raw.lower() in VOICE_ALIASES:
        return VOICE_ALIASES[raw.lower()]
    if provider == PROVIDER_PIPER and not raw:
        return configured or DEFAULT_VOICE
    return configured or DEFAULT_VOICE


def default_voice(provider: Optional[str] = None) -> str:
    """当前生效 provider 的默认音色（**前端可直接回传**的形态）。

    与 ``resolve_voice`` 的分工：
        * ``resolve_voice`` 返回 provider **原生音色名**（如 edge 的
          ``zh-CN-XiaoxiaoNeural``），供合成链路使用；
        * 本函数返回「前端回传给 ``POST /api/tts`` 的 ``voice`` 也能被
          正确解析」的标识，供 ``GET /api/tts/providers`` 下发给前端：

          * ``edge`` / ``piper`` -> 简短别名 ``xiaoxiao``
            （与前端历史行为一致，零感知切换）；
          * ``volc`` -> ``settings.tts_volc_voice_type``
            （运营改配置即对前端默认路径生效，不再被别名写死）。

    Args:
        provider: provider 名；为空读 ``settings.tts_provider``。

    Returns:
        前端可安全回传的默认音色标识。
    """
    name = resolve_provider_name(provider)
    if name == PROVIDER_VOLC:
        return (settings.tts_volc_voice_type or "").strip() or VOLC_DEFAULT_VOICE_TYPE
    # edge（内测期默认 + 最终兜底）与 piper（未实现，选中会降级 edge）：
    # 沿用前端别名，保持默认路径与改造前完全一致
    return DEFAULT_VOICE_ALIAS


def normalize_rate(rate: Optional[str]) -> Optional[str]:
    """把语速参数规范成 edge-tts 认识的 ``"+10%"`` 形式。

    Args:
        rate: 形如 ``"+10%"`` / ``"-10%"`` / ``"10"`` 的字符串，可为空。

    Returns:
        规范化后的语速字符串；入参为空或无法识别时返回 ``None``（用默认语速）。
    """
    raw = (rate or "").strip()
    if not raw:
        return None
    matched = _RATE_PATTERN.match(raw)
    if not matched:
        logger.warning("无法识别的语速参数 %r，改用默认语速", rate)
        return None
    sign, value, _percent = matched.groups()
    return f"{sign or '+'}{value}%"


def _rate_to_speed_ratio(rate: Optional[str]) -> float:
    """把 ``"+10%"`` 形式的语速转成火山 ``speed_ratio``（1.0 = 正常）。

    Args:
        rate: 语速字符串，可为空。

    Returns:
        落在 ``[0.2, 3.0]`` 区间内的倍速值（火山接口允许范围）。
    """
    normalized = normalize_rate(rate)
    if not normalized:
        return 1.0
    percent = float(normalized[:-1])
    ratio = round(1.0 + percent / 100.0, 3)
    return min(3.0, max(0.2, ratio))


def _rate_to_speech_rate(rate: Optional[str]) -> Optional[int]:
    """把 ``"+10%"`` 转成 v3 ``speech_rate`` 整数（实测 -10/0/10 均被接受）。

    未传 rate 时返回 ``None``，调用方不下发该字段，与出声探针保持一致。
    """
    normalized = normalize_rate(rate)
    if not normalized:
        return None
    return max(-50, min(100, int(round(float(normalized[:-1])))))


def normalize_text(text: str) -> str:
    """清洗并截断待合成文本。

    Args:
        text: 原始文本。

    Returns:
        strip 后、长度不超过 ``MAX_TEXT_LEN`` 的文本。

    Raises:
        TtsError: 文本为空。
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise TtsError("text 为空，无法合成语音")
    if len(cleaned) > MAX_TEXT_LEN:
        cleaned = cleaned[:MAX_TEXT_LEN]
    return cleaned


def get_provider(name: Optional[str] = None) -> TtsProvider:
    """按名字取 provider 实例（未知名字回落 ``edge``）。

    说明：**不做单例缓存**——配置可能在运行期被改动（如测试 monkeypatch），
    每次新建实例可避免拿到带旧配置的单例。
    """
    resolved = resolve_provider_name(name)
    return _PROVIDER_FACTORY[resolved]()


# ------------------------------------------------------------------ 统一入口
async def synthesize(
    text: str,
    *,
    voice: Optional[str] = None,
    rate: Optional[str] = None,
    provider: Optional[str] = None,
) -> Tuple[bytes, str]:
    """合成语音（**唯一对外入口**），带自动降级。

    执行顺序：
        1. 清洗文本（空则抛 ``TtsError``，超长截断）；
        2. 用 ``provider``（缺省读 ``settings.tts_provider``）合成；
        3. 失败且 ``tts_fallback_enabled`` 为真、且当前不是 ``edge``
           -> 回落到 ``edge``；
        4. 全部失败抛 ``TtsError``。

    Args:
        text: 待朗读文本。
        voice: 嗓音别名或原生音色名，为空用默认。
        rate: 语速，形如 ``"+10%"``。
        provider: 强制指定 provider，为空读配置。

    Returns:
        ``(音频字节, mime)`` 二元组，mime 形如 ``audio/mpeg``。

    Raises:
        TtsError: 文本为空，或所有可用 provider 都失败。
    """
    cleaned = normalize_text(text)
    primary = resolve_provider_name(provider)

    chain: List[str] = [primary]
    if (
        bool(settings.tts_fallback_enabled)
        and primary != FALLBACK_PROVIDER
        and FALLBACK_PROVIDER not in chain
    ):
        chain.append(FALLBACK_PROVIDER)

    errors: List[str] = []
    for name in chain:
        instance = get_provider(name)
        try:
            return await instance.synthesize(cleaned, voice=voice, rate=rate)
        except NotImplementedError as exc:
            errors.append(f"{name}: {exc}")
            logger.warning("TTS provider %s 尚未实现，准备降级", name)
        except Exception as exc:  # 依赖缺失 / 凭据缺失 / 上游失败，统一降级
            errors.append(f"{name}: {exc}")
            logger.warning("TTS provider %s 合成失败，准备降级: %s", name, exc)

    raise TtsError("TTS 合成失败（已尝试 " + " | ".join(chain) + "）：" + " / ".join(errors))


# ------------------------------------------------------------------ 自检信息
def _module_installed(module_name: str) -> bool:
    """判断第三方模块是否已安装（只查元数据，不真正 import）。"""
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):  # 包元数据损坏时按「未安装」处理
        return False


def _edge_status() -> Dict[str, Any]:
    """edge 通道自检：只要 edge-tts 装了就可用。"""
    if not _module_installed("edge_tts"):
        return {"name": PROVIDER_EDGE, "available": False, "reason": "edge-tts 未安装"}
    return {"name": PROVIDER_EDGE, "available": True, "reason": "免 Key，内测期默认与最终兜底"}


def _volc_status() -> Dict[str, Any]:
    """volc 通道自检：需要 httpx + app_id + 语音应用专用 access_token。"""
    # 先报凭据问题（最常见、也最容易修），再报依赖问题
    if not (settings.tts_volc_app_id or "").strip():
        return {"name": PROVIDER_VOLC, "available": False, "reason": "TTS_VOLC_APP_ID 未配置"}
    if not (settings.tts_volc_access_token or "").strip():
        return {
            "name": PROVIDER_VOLC,
            "available": False,
            "reason": "TTS_VOLC_ACCESS_TOKEN 未配置（语音应用独立 Token，不可用 ARK_API_KEY 代替）",
        }
    if not _module_installed("httpx"):
        return {"name": PROVIDER_VOLC, "available": False, "reason": "httpx 未安装"}
    version = "v1" if (settings.tts_volc_api_version or "v3").strip().lower() == "v1" else "v3"
    return {"name": PROVIDER_VOLC, "available": True, "reason": f"火山引擎语音技术（{version} / HTTP 接口）"}


def _piper_status() -> Dict[str, Any]:
    """piper 通道自检：常量 False，明确提示尚未实现。"""
    return {
        "name": PROVIDER_PIPER,
        "available": False,
        "reason": "本地离线兜底尚未实现，选中会自动降级到 edge",
    }


_STATUS_BUILDERS: Dict[str, Callable[[], Dict[str, Any]]] = {
    PROVIDER_EDGE: _edge_status,
    PROVIDER_VOLC: _volc_status,
    PROVIDER_PIPER: _piper_status,
}


def provider_status() -> List[Dict[str, Any]]:
    """列出全部 provider 及其可用状态（不做任何网络请求）。

    Returns:
        形如 ``[{"name": "edge", "available": True, "reason": "..."}]`` 的列表，
        顺序与 ``SUPPORTED_PROVIDERS`` 一致。
    """
    return [_STATUS_BUILDERS[name]() for name in SUPPORTED_PROVIDERS]


def describe() -> Dict[str, Any]:
    """当前 TTS 配置快照，供 ``GET /api/tts/providers`` 自检使用（camelCase）。

    Returns:
        包含当前 provider、兜底开关、默认嗓音、文本上限与各通道状态的字典。
    """
    current = resolve_provider_name()
    volc_version = "v1" if (settings.tts_volc_api_version or "v3").strip().lower() == "v1" else "v3"
    return {
        "provider": current,
        "fallbackEnabled": bool(settings.tts_fallback_enabled),
        "fallbackProvider": FALLBACK_PROVIDER,
        "voice": default_voice(current),
        "maxTextLength": MAX_TEXT_LEN,
        "volcApiVersion": volc_version,
        "volcResourceId": (settings.tts_volc_resource_id or "").strip() or VOLC_DEFAULT_RESOURCE_ID,
        "providers": provider_status(),
    }


__all__ = [
    "TtsError",
    "TtsProviderUnavailable",
    "TtsProvider",
    "EdgeTtsProvider",
    "VolcTtsProvider",
    "PiperTtsProvider",
    "synthesize",
    "get_provider",
    "resolve_provider_name",
    "resolve_voice",
    "default_voice",
    "normalize_rate",
    "normalize_text",
    "provider_status",
    "describe",
    "PROVIDER_EDGE",
    "PROVIDER_VOLC",
    "PROVIDER_PIPER",
    "SUPPORTED_PROVIDERS",
    "FALLBACK_PROVIDER",
    "DEFAULT_VOICE",
    "DEFAULT_VOICE_ALIAS",
    "VOICE_ALIASES",
    "VOLC_VOICE_ALIASES",
    "VOLC_DEFAULT_VOICE_TYPE",
    "VOLC_DEFAULT_RESOURCE_ID",
    "VOLC_V3_DEFAULT_BASE_URL",
    "VOLC_V1_DEFAULT_BASE_URL",
    "MAX_TEXT_LEN",
    "MIME_MP3",
    "MIME_WAV",
]
