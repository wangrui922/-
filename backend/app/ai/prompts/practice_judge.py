"""相似题判题提示词（迁移自 index.html 的 ``PRACTICE_JUDGE_PROMPT``）。

用途：孩子确认"这是最终答案"后，判断整份作答是否正确并给出反馈。
模型返回 JSON：``{"correct": true/false, "feedback": "..."}``。

占位符
------
``{{QUESTION}}``：题面；``{{ANSWER}}``：标准答案（仅供老师内部核对）；
``{{WORK}}``：孩子的完整作答记录。调用示例::

    text = (
        PROMPT.replace("{{QUESTION}}", question)
        .replace("{{ANSWER}}", answer)
        .replace("{{WORK}}", work)
    )
"""

from __future__ import annotations

VERSION: str = "1.0.0"

#: 本模板需要注入的占位符清单（供调用方/测试自检）
PLACEHOLDERS: tuple[str, ...] = ("{{QUESTION}}", "{{ANSWER}}", "{{WORK}}")

PROMPT: str = """你是一位严谨但耐心的中小学数学老师。现在孩子确认“这是最终答案”，请判断他的完整作答是否正确。
题目：
{{QUESTION}}

标准答案（只作为老师内部核对依据，不要原样展示）：
{{ANSWER}}

孩子的完整作答记录：
{{WORK}}

判断时要看解题过程、公式、数量关系、证明逻辑和最终结论，不能只看最后一个数字。feedback 必须说明：哪些步骤是对的、第一处错误在哪里、为什么错、接下来该怎么想；不要直接替孩子写完整解法。只返回 JSON，不要代码块：
{"correct":true或false,"feedback":"给孩子看的简短反馈，不直接泄露完整解法"}"""

__all__ = ["PROMPT", "VERSION", "PLACEHOLDERS"]
