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
* ``piper`` —— 本地离线兜底，**仅声明接口与配置位**，调用即抛
  ``NotImplementedError``，由 ``synthesize`` 自动降级到 ``edge``。

三条铁律
--------
1. **依赖全部 lazy import**：``edge_tts`` / ``httpx`` 等第三方 import 一律写在
   函数内部；缺依赖只影响对应 provider，**绝不让应用启动失败**；
2. **失败可降级**：``volc`` / ``piper`` 不可用时，只要 ``tts_fallback_enabled``
   为真就自动回落到 ``edge``；``edge`` 也失败才抛 ``TtsError`` 给上层；
3. **密钥零硬编码**：app_id / access_token 一律读 config（环境变量），
   火山 TTS 未单独配置 token 时复用方舟的 ``ARK_API_KEY``。

使用示例::

    from app.services import tts_service

    audio, mime = await tts_service.synthesize("这道题怎么做？", voice="xiaoxiao")
"""

from __future__ import annotations

import importlib.util
import io
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

#: 前端可传的简短别名 -> edge-tts 完整嗓音名（保持与原实现一致）
VOICE_ALIASES: Dict[str, str] = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",  # 甜美小云朵（默认）
    "yunxi": "zh-CN-YunxiNeural",  # 阳光少年男声
    "xiaoyi": "zh-CN-XiaoyiNeural",  # 温柔女声
}

#: 前端别名 -> 火山语音音色 ID（BV 开头为平台音色标识，以控制台清单为准）
VOLC_VOICE_ALIASES: Dict[str, str] = {
    "xiaoxiao": "BV001_streaming",  # 通用女声（甜美，贴近小云朵）
    "xiaoyi": "BV001_streaming",  # 通用女声
    "yunxi": "BV002_streaming",  # 通用男声
}

#: 火山 TTS 默认音色（别名未命中且未传 BV* 音色时使用）
VOLC_DEFAULT_VOICE_TYPE: str = "BV001_streaming"

# ------------------------------------------------------------------ 其它常量
#: 单次合成的文本上限（超出截断，避免单请求过大）
MAX_TEXT_LEN: int = 2000

MIME_MP3: str = "audio/mpeg"
MIME_WAV: str = "audio/wav"

#: 火山 TTS 返回体中的成功码（3000 = 官方 Success；0 也按成功处理，兼容 v3）
VOLC_SUCCESS_CODES: frozenset = frozenset({0, 3000})

#: 火山 TTS 默认接入点（可在环境变量里换成 v3 / 内网网关）
VOLC_DEFAULT_BASE_URL: str = "https://openspeech.bytedance.com/api/v1/tts"
VOLC_DEFAULT_CLUSTER: str = "volcano_tts"

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

    为什么用 HTTP 而不是 SDK：
        SDK（``volcengine-python-sdk``）体积大、版本迭代快，HTTP 接口只用
        项目已有的 ``httpx``，免编译、Windows 友好，且凭据/路径全走配置。

    接入参数（均为配置位，见 ``app.core.config.Settings``）：
        ``TTS_VOLC_APP_ID`` / ``TTS_VOLC_ACCESS_TOKEN``（留空可复用 ``ARK_API_KEY``）
        / ``TTS_VOLC_CLUSTER`` / ``TTS_VOLC_BASE_URL`` / ``TTS_VOLC_VOICE_TYPE``。
    """

    name = PROVIDER_VOLC

    def _credentials(self) -> Tuple[str, str]:
        """读取火山凭据：``(app_id, access_token)``。

        Returns:
            ``(app_id, access_token)``，token 缺省时复用方舟的 ``ARK_API_KEY``。

        Raises:
            TtsProviderUnavailable: app_id 或 token 均未配置。
        """
        app_id = (settings.tts_volc_app_id or "").strip()
        token = (settings.tts_volc_access_token or "").strip()
        if not token:
            # 同一个火山账号下，方舟 Key 也可作为语音接口的 access token
            token = (settings.ark_api_key or "").strip()

        if not app_id:
            raise TtsProviderUnavailable("TTS_VOLC_APP_ID 未配置，火山 TTS 不可用")
        if not token:
            raise TtsProviderUnavailable(
                "TTS_VOLC_ACCESS_TOKEN 未配置（也未设置 ARK_API_KEY），火山 TTS 不可用"
            )
        return app_id, token

    def _build_payload(self, app_id: str, token: str, text: str, voice: str, rate: Optional[str]) -> Dict[str, Any]:
        """构造火山 TTS 请求体。"""
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

    async def synthesize(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
    ) -> Tuple[bytes, str]:
        # 先查凭据：配置缺失是最常见也最容易修的问题，先暴露它（比依赖缺失更明确）
        app_id, token = self._credentials()

        # lazy import：httpx 是硬依赖，但同样不在模块顶层 import（与 volc 相关的
        # 任何依赖缺失都只允许影响本通道，绝不影响应用启动）
        try:
            import base64

            import httpx  # type: ignore[import-not-found]
        except ImportError as exc:
            raise TtsProviderUnavailable("httpx 未安装，火山 TTS 不可用") from exc

        resolved_voice = resolve_voice(PROVIDER_VOLC, voice)
        base_url = (settings.tts_volc_base_url or VOLC_DEFAULT_BASE_URL).strip()
        timeout = float(settings.tts_volc_timeout_sec or 15)
        payload = self._build_payload(app_id, token, text, resolved_voice, rate)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer;{token}",
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(base_url, json=payload, headers=headers)
        except Exception as exc:  # 网络 / 超时 / DNS
            raise TtsError(f"火山 TTS 请求失败: {exc}") from exc

        if response.status_code >= 400:
            snippet = (response.text or "")[:200]
            raise TtsError(f"火山 TTS 返回 HTTP {response.status_code}: {snippet}")

        try:
            data = response.json()
        except ValueError as exc:
            raise TtsError(f"火山 TTS 返回非 JSON 内容: {exc}") from exc

        if not isinstance(data, dict):
            raise TtsError("火山 TTS 返回结构异常：顶层不是 JSON 对象")

        raw_code = data.get("code")
        audio_b64 = data.get("data")
        message = str(data.get("message") or "")
        # code 缺失但带 data 时按成功处理，兼容不同网关版本
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
        if raw.upper().startswith("BV"):  # 火山音色 ID 直通
            return raw
        return (settings.tts_volc_voice_type or "").strip() or VOLC_DEFAULT_VOICE_TYPE

    # edge / piper：别名命中即用，未命中沿用「回落到默认嗓音」的既有行为
    if raw.lower() in VOICE_ALIASES:
        return VOICE_ALIASES[raw.lower()]
    if provider == PROVIDER_PIPER and not raw:
        return configured or DEFAULT_VOICE
    return configured or DEFAULT_VOICE


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
    """volc 通道自检：需要 httpx + app_id + token（token 可复用 ARK_API_KEY）。"""
    # 先报凭据问题（最常见、也最容易修），再报依赖问题
    if not (settings.tts_volc_app_id or "").strip():
        return {"name": PROVIDER_VOLC, "available": False, "reason": "TTS_VOLC_APP_ID 未配置"}
    token = (settings.tts_volc_access_token or "").strip() or (settings.ark_api_key or "").strip()
    if not token:
        return {
            "name": PROVIDER_VOLC,
            "available": False,
            "reason": "TTS_VOLC_ACCESS_TOKEN 未配置（也未设置 ARK_API_KEY）",
        }
    if not _module_installed("httpx"):
        return {"name": PROVIDER_VOLC, "available": False, "reason": "httpx 未安装"}
    return {"name": PROVIDER_VOLC, "available": True, "reason": "火山引擎语音技术（HTTP 接口）"}


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
    return {
        "provider": current,
        "fallbackEnabled": bool(settings.tts_fallback_enabled),
        "fallbackProvider": FALLBACK_PROVIDER,
        "voice": resolve_voice(current, None),
        "maxTextLength": MAX_TEXT_LEN,
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
    "VOICE_ALIASES",
    "VOLC_VOICE_ALIASES",
    "MAX_TEXT_LEN",
    "MIME_MP3",
    "MIME_WAV",
]
