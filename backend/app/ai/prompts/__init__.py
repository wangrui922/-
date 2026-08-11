"""教学提示词资产包。

每个子模块导出两个常量：

- ``PROMPT``：提示词全文（从 V3 产品 ``index.html`` 逐字迁移，禁止随意改写）。
- ``VERSION``：语义化版本号，提示词内容变更时必须同步升版，便于线上回溯。

占位符约定
----------
含占位符的模板（``practice_guide`` / ``practice_judge``）一律保留 ``{{XXX}}``
双大括号原始写法，调用方用字符串替换注入，**不要**改成 ``str.format`` 风格
（提示词内含 JSON 示例，单大括号会与 format 语法冲突）::

    from app.ai.prompts import practice_guide

    text = practice_guide.PROMPT.replace("{{QUESTION}}", q).replace("{{WORK}}", w)
"""

from __future__ import annotations

from app.ai.prompts import (
    exercise,
    ocr,
    practice_guide,
    practice_judge,
    report,
    summary,
    tutor,
)

__all__ = [
    "ocr",
    "tutor",
    "exercise",
    "summary",
    "practice_guide",
    "practice_judge",
    "report",
]
