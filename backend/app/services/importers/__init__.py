"""docx 结构化导入子包（T05 · 题库/出题模块 Exercise Agent）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 子包定位 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
本子包负责把「教辅 docx 原件」解析成结构化题目并落库，是题库的**离线扩容管道**，
不参与任何在线请求链路（API / Agent 均不 import 本包）。

分层（自底向上，禁止反向依赖）::

    schema.py        数据契约：ParsedQuestion / ParseOutcome / LoadStats / 异常
    docx_reader.py   最底层 IO：zipfile + xml.etree 读 word/document.xml
    text_clean.py    文本清洗：去噪 / 中文数字 / 题型识别 / 选项抽取
    blocks.py        题块切分：两级（大题·小题）顺序期望状态机
    schema_guard.py  运行期表结构自愈（V7 保险②）
    manifest.py      清单：15 份 docx 的硬登记（含苏教 A 组 pair_key）
    qingdao.py       青岛版解析器（单文件正文+答案）
    sujiao.py        苏教版解析器（A 组配对三闸门 / B 组单文件）
    loader.py        落库：uuid5 幂等 upsert（questions + answers）
    report.py        导入汇总报告（JSON + Markdown）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 三条硬约束 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
U1 年级语义
    ``questions.grade`` 存**升入年级**（一升二 = 2，四下 = 4）。
    ⛔ 严禁在本子包任何位置出现 ``grade - 1`` 之类的换算。
    册内的「温故 / 预习」差异一律落在 ``section``，不拆年级。

U2 新题状态
    本子包写入的题目一律 ``status='archived'`` + ``kp_verified=False``，
    人工抽检通过后再用 ``backend/scripts/promote_bank.py`` 批量上架。

零新增依赖
    仅使用标准库（zipfile / xml.etree / re / dataclasses）+ 项目已有的 SQLAlchemy，
    **不引入 python-docx**，``requirements.txt`` 保持不变。
"""

from __future__ import annotations

from app.services.importers.schema import (
    ImporterError,
    LoadStats,
    PairingError,
    ParsedQuestion,
    ParseOutcome,
    SchemaGuardError,
    SkippedPaper,
    build_stable_key,
)
from app.services.importers.schema_guard import ensure_bank_columns

__all__ = [
    "ImporterError",
    "PairingError",
    "SchemaGuardError",
    "ParsedQuestion",
    "ParseOutcome",
    "SkippedPaper",
    "LoadStats",
    "build_stable_key",
    "ensure_bank_columns",
]
