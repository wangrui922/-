"""AI 返回文本的稳健 JSON 解析（4 个在线 Agent 共用）。

前提：**不要相信 AI 一定返回纯 JSON**。实际返回常见三种脏形态：
    1. 用 ```json ... ``` 代码块包裹；
    2. JSON 前后带一段自然语言说明；
    3. 两者兼有。

解析顺序：去围栏 → ``json.loads`` → 贪婪切片（首个 ``{``/``[`` 到末个 ``}``/``]``）
→ 仍失败则抛 ``ArkError``。各 Agent 内以私有别名 ``_extract_json`` 引用本函数。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Union

from app.ai.ark_client import ArkError

#: 匹配 ```json ... ``` / ``` ... ``` 代码块围栏
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")

JsonValue = Union[Dict[str, Any], List[Any]]


def _strip_fence(text: str) -> str:
    """去掉首尾的 Markdown 代码块围栏。"""
    cleaned = (text or "").strip()
    cleaned = _FENCE_RE.sub("", cleaned)
    return cleaned.strip()


def _greedy_slice(text: str) -> str | None:
    """贪婪截取最外层的 JSON 片段：优先对象 ``{...}``，其次数组 ``[...]``。"""
    candidates = []
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start >= 0 and end > start:
            candidates.append((start, text[start : end + 1]))
    if not candidates:
        return None
    # 取出现位置最靠前的那一段（AI 常把说明写在 JSON 之后）
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def extract_json(text: str) -> JsonValue:
    """从模型输出中抽取 JSON 对象或数组。

    Args:
        text: 模型返回的原始文本。

    Returns:
        解析出的 ``dict`` 或 ``list``。

    Raises:
        ArkError: 三种策略都失败时抛出，消息固定为 ``AI 返回无法解析为JSON``。
    """
    cleaned = _strip_fence(text)

    if cleaned:
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, (dict, list)):
                return parsed
        except (TypeError, ValueError):
            pass

        sliced = _greedy_slice(cleaned)
        if sliced:
            try:
                parsed = json.loads(sliced)
                if isinstance(parsed, (dict, list)):
                    return parsed
            except (TypeError, ValueError):
                pass

    raise ArkError("AI 返回无法解析为JSON")


__all__ = ["extract_json", "JsonValue"]
