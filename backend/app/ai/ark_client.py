"""火山方舟 Responses API 客户端（服务端持 Key）。

与 V3 单页产品的差别
--------------------
V3 由前端持 Key、经 ``server.js`` / ``cloud-functions/api/chat.js`` 原样透传；
V4.1 起 **Key 只留在服务端**（``settings.ark_api_key``），前端不再接触任何密钥。
除此之外，请求体结构与响应解析逻辑与 V3 完全对齐，保证教学效果不漂移：

- 端点：``https://ark.cn-beijing.volces.com/api/v3/responses``
- 请求体：``{"model": ..., "input": [{"role": ..., "content": [...]}]}``
- 图片：``{"type": "input_image", "image_url": "data:image/jpeg;base64,..."}``，
  且图片块排在同一条消息的文本块之前（与 index.html ``arkCall`` 一致）。
- 响应：优先取 ``output_text``，否则拼接 ``output[*].content[*].text``。

使用示例::

    from app.ai import ark_respond
    from app.ai.prompts import tutor

    reply = ark_respond(
        messages=[{"role": "user", "content": "这道题我不会"}],
        system=tutor.PROMPT,
    )
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

#: 方舟 Responses API 端点（与 server.js / chat.js 保持一致）
ARK_ENDPOINT: str = "https://ark.cn-beijing.volces.com/api/v3/responses"

#: 默认超时（秒）
DEFAULT_TIMEOUT: float = 60.0

#: 失败重试次数（总尝试次数 = MAX_RETRY + 1 = 2）
MAX_RETRY: int = 1

#: 允许出现在 input 中的角色
_ALLOWED_ROLES = frozenset({"system", "user", "assistant"})


class ArkError(Exception):
    """方舟调用异常。

    覆盖：Key 未配置、参数非法、网络/超时失败、上游非 2xx、响应结构异常。
    上层（service / router）捕获后统一转成对用户友好的提示。
    """


def _as_data_url(image_b64: str) -> str:
    """把 base64 字符串规范成 data URL。

    Args:
        image_b64: 纯 base64 字符串，或已经是 ``data:image/...;base64,`` 前缀的完整串。

    Returns:
        可直接放进 ``image_url`` 字段的 data URL。
    """
    raw = image_b64.strip()
    if raw.startswith("data:"):
        return raw
    # 去掉可能存在的换行（部分客户端会对长 base64 折行）
    raw = "".join(raw.split())
    return f"data:image/jpeg;base64,{raw}"


#: 角色标签：把多轮历史压成单条 user 消息时，用标签区分说话人
_ROLE_LABELS = {
    "assistant": "老师",
    "system": "（内部提示）",
    "user": "学生",
}


def _flatten_messages_to_transcript(messages: List[Dict[str, Any]]) -> str:
    """把多轮历史压成一段带角色标签的文本。

    用于规避本模型对方舟 Responses API 多轮 ``input`` 的限制（见 ``_build_input``）。

    Args:
        messages: 原始消息列表，元素形如 ``{"role": "user", "content": "..."}``。

    Returns:
        形如 ``"学生：...\\n老师：...\\n学生：..."`` 的拼接文本；无有效内容返回空串。
    """
    lines: List[str] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user").strip().lower()
        content = message.get("content", "")
        if content is None or not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        label = _ROLE_LABELS.get(role, "学生")
        lines.append(f"{label}：{content}")
    return "\n".join(lines)


def _build_input(
    messages: List[Dict[str, Any]],
    system: Optional[str],
    image_b64: Optional[str],
) -> List[Dict[str, Any]]:
    """构造 Responses API 的 ``input`` 列表。

    规则：
    1. ``system`` 非空时作为首条 ``role="system"`` 消息。
    2. **单条 user 消息**（最常见路径，含单轮问答 / 带图 OCR）保持原样，以确保图片挂在
       该 user 消息上、行为向后兼容。
    3. **多轮历史**（含 ``assistant`` 角色）压平成**一条带角色标签的 user 消息**——
       本模型（``ep-m-20260728224052-bb4vl``）对「多轮 input」会强制要求 ``partial``
       参数，而该字段又被接口判定为 ``unknown field``，无法合法传入；实测单条 user 消息
       （即使内含完整历史文本）完全正常。故用「单条带历史文本」等价替代多轮，既保留
       苏格拉底上下文，又规避 partial 限制。
    4. ``image_b64`` 非空时，图片块挂在**最后一条 user 消息**上并排在文本块之前；
       若消息里没有任何 user 消息，则追加一条只含图片 + 空文本的 user 消息。

    Args:
        messages: 原始消息列表，元素形如 ``{"role": "user", "content": "..."}``。
        system: 系统提示词，可为空。
        image_b64: 图片 base64（或 data URL），可为空。

    Returns:
        方舟 ``input`` 字段的值。

    Raises:
        ArkError: 消息结构非法（非 dict、角色不合法、内容非字符串）时抛出。
    """
    items: List[Dict[str, Any]] = []

    if system and system.strip():
        items.append(
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system}],
            }
        )

    msgs: List[Dict[str, Any]] = list(messages or [])

    # 判断是否「恰好一条 user 消息」：是则保持原样；否则压平成单条 user 消息
    single_user = (
        len(msgs) == 1
        and isinstance(msgs[0], dict)
        and str(msgs[0].get("role") or "user").strip().lower() == "user"
    )
    if not single_user and msgs:
        transcript = _flatten_messages_to_transcript(msgs)
        if transcript:
            msgs = [{"role": "user", "content": transcript}]

    for index, message in enumerate(msgs):
        if not isinstance(message, dict):
            raise ArkError(f"messages[{index}] 必须是 dict，实际为 {type(message).__name__}")

        role = str(message.get("role") or "user").strip().lower()
        if role not in _ALLOWED_ROLES:
            raise ArkError(f"messages[{index}] 的 role 非法：{role}")

        content = message.get("content", "")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise ArkError(f"messages[{index}] 的 content 必须是字符串")

        items.append(
            {
                "role": role,
                "content": [{"type": "input_text", "text": content}],
            }
        )

    if image_b64 and image_b64.strip():
        image_block = {"type": "input_image", "image_url": _as_data_url(image_b64)}
        target_index = -1
        for i in range(len(items) - 1, -1, -1):
            if items[i]["role"] == "user":
                target_index = i
                break

        if target_index >= 0:
            # 图片在前、文本在后（与 index.html arkCall 的拼法一致）
            items[target_index]["content"] = [image_block] + items[target_index]["content"]
        else:
            items.append(
                {
                    "role": "user",
                    "content": [image_block, {"type": "input_text", "text": ""}],
                }
            )

    if not items:
        raise ArkError("messages 为空且未提供 system / 图片，无法构造请求")

    return items


def _build_payload(
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    system: Optional[str],
    image_b64: Optional[str],
) -> tuple[Dict[str, Any], Dict[str, str]]:
    """构造请求体与请求头（同步 / 异步两条调用链共用，保证行为不漂移）。

    Args:
        api_key: 方舟 API Key（已校验非空）。
        model: 模型名（已校验非空）。
        messages: 对话消息列表。
        system: 系统提示词，可为空。
        image_b64: 图片 base64，可为空。

    Returns:
        ``(payload, headers)`` 二元组。
    """
    payload: Dict[str, Any] = {
        "model": model,
        "input": _build_input(messages, system, image_b64),
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    return payload, headers


async def ark_respond_async(
    *,
    messages: List[Dict[str, Any]],
    system: Optional[str] = None,
    image_b64: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """调用方舟 Responses API（**异步版**，基于 ``httpx.AsyncClient``）。

    与 ``ark_respond`` 完全等价：同样的端点、请求体、超时、重试与错误语义，
    只是不阻塞事件循环——API 层 async 路由直接 ``await`` 即可，无需再套
    ``run_in_threadpool``。

    Args:
        messages: 对话消息列表，元素形如 ``{"role": "user"|"assistant", "content": "文本"}``。
        system: 系统提示词（通常来自 ``app.ai.prompts.*`` 的 ``PROMPT``），可为空。
        image_b64: 图片 base64 或 data URL，非空时挂到最后一条 user 消息上，可为空。
        timeout: 单次请求超时（秒），默认 60。

    Returns:
        模型输出文本（已 strip）。

    Raises:
        ArkError: Key 未配置、参数非法、网络/超时失败、上游非 2xx、响应结构异常。
    """
    api_key = (settings.ark_api_key or "").strip()
    if not api_key:
        raise ArkError("ARK_API_KEY 未配置")

    # 模型名一律读配置（环境变量 ARK_MODEL），代码里不留硬编码
    model = (settings.ark_model or "").strip()
    if not model:
        raise ArkError("ARK_MODEL 未配置")

    payload, headers = _build_payload(api_key, model, messages, system, image_b64)

    last_error: str = "未知错误"
    total_attempts = MAX_RETRY + 1

    for attempt in range(1, total_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(ARK_ENDPOINT, json=payload, headers=headers)

            if response.status_code >= 400:
                snippet = response.text[:200] if response.text else ""
                last_error = f"方舟返回 {response.status_code} {snippet}"
                # 4xx 属于请求本身的问题（鉴权/参数），重试无意义，直接失败
                if response.status_code < 500 and response.status_code != 429:
                    raise ArkError(last_error)
                logger.warning("方舟调用失败（第 %s/%s 次）：%s", attempt, total_attempts, last_error)
            else:
                try:
                    data = response.json()
                except ValueError as exc:  # 非 JSON 响应
                    raise ArkError(f"方舟返回非 JSON 内容：{exc}") from exc
                return _extract_text(data)

        except httpx.TimeoutException as exc:
            last_error = f"方舟请求超时（{timeout:.0f}s）：{exc}"
            logger.warning("方舟调用超时（第 %s/%s 次）", attempt, total_attempts)
        except httpx.HTTPError as exc:
            last_error = f"方舟连接失败：{exc}"
            logger.warning("方舟连接失败（第 %s/%s 次）：%s", attempt, total_attempts, exc)

    raise ArkError(last_error)


def _extract_text(data: Any) -> str:
    """从 Responses API 返回体中抽取纯文本。

    解析顺序与 index.html 的 ``responseText`` 完全一致：
    先看 ``output_text``，否则遍历 ``output[*].content[*].text`` 拼接。

    Args:
        data: 方舟返回的 JSON（已 ``json.loads``）。

    Returns:
        去掉首尾空白的文本。

    Raises:
        ArkError: 结构不是 dict、或没有解析出任何文本时抛出。
    """
    if not isinstance(data, dict):
        raise ArkError("方舟返回结构异常：顶层不是 JSON 对象")

    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    outputs = data.get("output")
    if not isinstance(outputs, list):
        raise ArkError("方舟返回结构异常：缺少 output 字段")

    parts: List[str] = []
    for item in outputs:
        if not isinstance(item, dict):
            continue
        contents = item.get("content")
        if not isinstance(contents, list):
            continue
        for part in contents:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])

    text = "".join(parts).strip()
    if not text:
        raise ArkError("方舟返回空内容")
    return text


def ark_respond(
    *,
    messages: List[Dict[str, Any]],
    system: Optional[str] = None,
    image_b64: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """调用方舟 Responses API，返回模型输出的纯文本。

    Args:
        messages: 对话消息列表，元素形如 ``{"role": "user"|"assistant", "content": "文本"}``。
        system: 系统提示词（通常来自 ``app.ai.prompts.*`` 的 ``PROMPT``），可为空。
        image_b64: 图片 base64 或 data URL，非空时挂到最后一条 user 消息上，可为空。
        timeout: 单次请求超时（秒），默认 60。

    Returns:
        模型输出文本（已 strip）。

    Raises:
        ArkError: Key 未配置、参数非法、网络/超时失败、上游非 2xx、响应结构异常。
    """
    api_key = (settings.ark_api_key or "").strip()
    if not api_key:
        raise ArkError("ARK_API_KEY 未配置")

    # 模型名一律读配置（环境变量 ARK_MODEL），代码里不留硬编码
    model = (settings.ark_model or "").strip()
    if not model:
        raise ArkError("ARK_MODEL 未配置")

    payload, headers = _build_payload(api_key, model, messages, system, image_b64)

    last_error: str = "未知错误"
    total_attempts = MAX_RETRY + 1

    for attempt in range(1, total_attempts + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(ARK_ENDPOINT, json=payload, headers=headers)

            if response.status_code >= 400:
                snippet = response.text[:200] if response.text else ""
                last_error = f"方舟返回 {response.status_code} {snippet}"
                # 4xx 属于请求本身的问题（鉴权/参数），重试无意义，直接失败
                if response.status_code < 500 and response.status_code != 429:
                    raise ArkError(last_error)
                logger.warning("方舟调用失败（第 %s/%s 次）：%s", attempt, total_attempts, last_error)
            else:
                try:
                    data = response.json()
                except ValueError as exc:  # 非 JSON 响应
                    raise ArkError(f"方舟返回非 JSON 内容：{exc}") from exc
                return _extract_text(data)

        except httpx.TimeoutException as exc:
            last_error = f"方舟请求超时（{timeout:.0f}s）：{exc}"
            logger.warning("方舟调用超时（第 %s/%s 次）", attempt, total_attempts)
        except httpx.HTTPError as exc:
            last_error = f"方舟连接失败：{exc}"
            logger.warning("方舟连接失败（第 %s/%s 次）：%s", attempt, total_attempts, exc)

    raise ArkError(last_error)


__all__ = [
    "ark_respond",
    "ark_respond_async",
    "ArkError",
    "ARK_ENDPOINT",
    "DEFAULT_TIMEOUT",
    "MAX_RETRY",
]
