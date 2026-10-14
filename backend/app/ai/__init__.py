"""AI 能力子包。

包含两部分：
- ``prompts``：教学提示词资产（从 V3 单页产品 ``index.html`` 原样迁移）。
- ``ark_client``：火山方舟 Responses API 客户端（服务端持 Key）。
"""

from __future__ import annotations

from app.ai import prompts
from app.ai.ark_client import (
    ARK_ENDPOINT,
    ArkError,
    ark_respond,
    ark_respond_async,
)

__all__ = ["prompts", "ark_respond", "ark_respond_async", "ArkError", "ARK_ENDPOINT"]
