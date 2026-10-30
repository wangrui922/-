# -*- coding: utf-8 -*-
"""接地气素材测试的共享工具：全局唯一中文标记 + 造数行清理。

为什么需要共享
--------------
测试库是**会话级共享**的内存 SQLite（conftest 的 ``db`` fixture 正常跑完就
``commit``），所以有三个坑：

1. **各测试文件共用同一个库** —— 一个文件造的题会被另一个文件看见；
2. **各测试文件必须共用同一个标记序列** —— 否则两个文件各自从 1 开始造标记，
   会造出**一模一样的标记** → 一个文件的用例命中另一个文件造的题 →
   断言随执行顺序漂移（本分支实测踩过：``test_grounding_service`` 与
   ``test_grounding_injection`` 的标记撞车，2 条用例变红）；
3. **造出来的行必须清掉** —— 否则会污染后续模块的断言（典型：扫全库
   ``active`` 题的不变式用例）。

本模块只放**测试用**的小工具，不含任何业务逻辑。
"""

from __future__ import annotations

import itertools
from typing import Any, List

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.content import KnowledgePoint, Question
from app.models.system_config import SystemConfig

# =============================================================================
# 全局唯一标记
# =============================================================================

#: 生僻字池（刻意避开小学数学题面里会出现的字，保证不与真实题面撞）。
_RARE_CHARS = (
    "鼬鼯貔貘鬣麟麒犊狍麂狈獾鼍蟾蝾蚰蜈蚺蟒鸢鸶鹱鸻鹞鹧鹣鹦鹕鹩鹫鹇鹈鹉鹌鹊鹃鹑鹒鹓鹬鹭鹰鹳鹾麸黉齑龋龌龑龚龛"
)

#: 全局计数器（**跨测试文件共享** —— 这是本模块存在的头号理由）。
_SEQ = itertools.count(1)

#: 本次会话造出来的行（清理用）。
_CREATED_QUESTIONS: List[Any] = []
_CREATED_KPS: List[Any] = []
_CREATED_CONFIG_KEYS: List[str] = []


def new_tag() -> str:
    """产出一个全局唯一的「两个生僻字」标记。

    为什么必须是**两个**字：检索层的相关性下限是「与目标串**至少有一个
    共同的 2 字词**」。所以标记必须**不与库里任何其它文本共享 2 字词**，
    否则断言会命中别的模块造的题。

    唯一性怎么保证：下标 ``idx`` 唯一确定有序对 ``(idx // 64, idx % 64)``，
    而标记本身就是这个 2 字词 → 不同下标必得不同 2 字词 → 永不互相命中。
    """
    index = next(_SEQ)
    size = len(_RARE_CHARS)
    return f"{_RARE_CHARS[(index // size) % size]}{_RARE_CHARS[index % size]}"


def track_question(question: Any) -> Any:
    """登记一条新建的题（供清理）。"""
    _CREATED_QUESTIONS.append(question.id)
    return question


def track_kp(kp: Any) -> Any:
    """登记一条新建的知识点（供清理）。"""
    _CREATED_KPS.append(kp.id)
    return kp


def track_config_key(key: str) -> str:
    """登记一个被改写的配置键（供清理）。"""
    if key not in _CREATED_CONFIG_KEYS:
        _CREATED_CONFIG_KEYS.append(key)
    return key


@pytest.fixture
def rag_cleanup(db: Session):
    """清掉本模块造的题 / 知识点 / 配置行。

    本 fixture **依赖** ``db``：``db`` 先建立、``rag_cleanup`` 后建立，
    析构时**先**执行本 fixture（删除行）、**后**由 ``db`` 统一 commit ——
    于是「插入 + 删除」在同一个事务里提交，净效果 = 库里什么都没多。

    不清理会怎样：``RAG_GROUNDING_ENABLED=true`` 的配置行会被 commit 出去，
    后续模块读到「开关已开」→ 行为漂移（本分支实测踩过）。
    """
    yield
    if _CREATED_QUESTIONS:
        db.execute(
            delete(Question).where(Question.id.in_(list(_CREATED_QUESTIONS))),
            execution_options={"synchronize_session": False},
        )
        _CREATED_QUESTIONS.clear()
    if _CREATED_KPS:
        db.execute(
            delete(KnowledgePoint).where(
                KnowledgePoint.id.in_(list(_CREATED_KPS))
            ),
            execution_options={"synchronize_session": False},
        )
        _CREATED_KPS.clear()
    if _CREATED_CONFIG_KEYS:
        db.execute(
            delete(SystemConfig).where(
                SystemConfig.key.in_(list(_CREATED_CONFIG_KEYS))
            ),
            execution_options={"synchronize_session": False},
        )
        _CREATED_CONFIG_KEYS.clear()


__all__ = [
    "new_tag",
    "track_question",
    "track_kp",
    "track_config_key",
    "rag_cleanup",
]
