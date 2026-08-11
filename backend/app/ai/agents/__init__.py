"""在线 Agent 子包（docs §C.1 的四个 Agent）。

四个 Agent 的分工：
    * ``question``：读题 —— OCR + 知识点/难度/题型分析；
    * ``diagnose``：判错 —— 错误类型 / 具体原因 / 能力缺口（只给系统与老师看）；
    * ``tutor``：引导 —— 苏格拉底式追问，产出"下一句对孩子说什么"；
    * ``exercise``：出题 —— 一道修复练习 + 一道迁移练习。

Epic6 追加第五个 Agent：
    * ``report``：家长周报 —— 把一周快照写成三段家长话，**AI 失败自动降级、永不抛异常**。

统一约定：所有 Agent 都是**同步函数**，由 API 层用 ``run_in_threadpool`` 包裹；
模型返回文本的 JSON 解析一律走 ``_json_utils.extract_json``。

Example:
    >>> from app.ai.agents import question, diagnose, tutor, exercise  # doctest: +SKIP
"""

from __future__ import annotations

from app.ai.agents import diagnose, exercise, question, report, tutor

__all__ = ["question", "diagnose", "tutor", "exercise", "report"]
