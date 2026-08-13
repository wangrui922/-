"""T03 集成测试共用工具：方舟客户端假实现 + 统一响应断言 + 落库查询。

为什么要同时打多个 patch 点？
    ``app/ai/agents/*.py`` 与 ``app/api/file.py`` 都是用
    ``from app.ai.ark_client import ark_respond`` 导入的，函数对象在 import
    时就已经复制进了各自模块的命名空间。只替换 ``app.ai.ark_client.ark_respond``
    对这些**已导入**的模块不生效（Python 经典的 "patch where it's used" 问题），
    所以 ``patch_ark`` 把全部引用点一起换掉。

本模块不以 ``test_`` 开头，pytest 不会收集它。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from app.ai.prompts import exercise as exercise_prompt
from app.ai.prompts import ocr as ocr_prompt
from app.models.ai import AiMessage, AiSession

#: ``ark_respond`` 的全部引用点，一个都不能漏
ARK_PATCH_TARGETS = (
    "app.ai.ark_client.ark_respond",  # 定义处（任务书指定）
    "app.ai.ark_respond",  # 包级 re-export
    "app.ai.agents.question.ark_respond",
    "app.ai.agents.exercise.ark_respond",
    "app.ai.agents.diagnose.ark_respond",
    "app.ai.answer_guard.ark_respond",  # T04B 输出检查器（tutor 经此调用方舟）
    "app.api.file.ark_respond",
    "app.services.explanation_quality.ark_respond",  # #67 讲解质量评测裁判
)

#: 假 AI 默认给出的两道练习题
DEFAULT_EXERCISES: List[Dict[str, Any]] = [
    {
        "q": "解方程：3x - 5 = 7",
        "a": "x = 4",
        "type": "计算",
        "role": "fix",
        "tag": "修复练习",
    },
    {
        "q": "小明买 3 支笔花了 15 元，每支笔多少元？列方程求解。",
        "a": "3x = 15，x = 5",
        "type": "应用",
        "role": "transfer",
        "tag": "迁移练习",
    },
]


class FakeArk:
    """``ark_respond`` 的假实现：按调用参数判定 Agent 类型并返回对应内容。

    判定依据（与真实调用一一对应）：
        * ``system`` 是 OCR 提示词            -> ``ocr``      返回题面文字
        * ``system`` 是出题提示词             -> ``exercise`` 返回两题 JSON 数组
        * 用户消息里出现 ``"knowledge"``      -> ``analyze``  返回分析 JSON（带代码块围栏）
        * 用户消息里出现 ``"error"``          -> ``diagnose`` 返回诊断 JSON
        * 其余（辅导提示词 + 会话历史）       -> ``tutor``    返回一句引导语

    Attributes:
        calls: 每次调用的记录，元素含 ``kind`` / ``system`` / ``messages`` / ``image_b64``。
    """

    def __init__(
        self,
        *,
        knowledge: str = "一元一次方程·移项",
        difficulty: int = 3,
        qtype: str = "计算",
        ocr_text: str = "解方程：2x + 3 = 7",
        exercises: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.knowledge = knowledge
        self.difficulty = difficulty
        self.qtype = qtype
        self.ocr_text = ocr_text
        self.exercises = DEFAULT_EXERCISES if exercises is None else exercises
        self.calls: List[Dict[str, Any]] = []

    # ---------------------------------------------------------------- 调用
    def __call__(
        self,
        *,
        messages: List[Dict[str, Any]],
        system: Optional[str] = None,
        image_b64: Optional[str] = None,
        timeout: float = 60.0,
    ) -> str:
        kind = self._classify(messages, system)
        self.calls.append(
            {"kind": kind, "system": system, "messages": messages, "image_b64": image_b64}
        )
        return self._reply(kind)

    # ---------------------------------------------------------------- 内部
    @staticmethod
    def _last_content(messages: List[Dict[str, Any]]) -> str:
        for item in reversed(messages or []):
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if content:
                return content
        return ""

    def _classify(self, messages: List[Dict[str, Any]], system: Optional[str]) -> str:
        if system == ocr_prompt.PROMPT:
            return "ocr"
        if system == exercise_prompt.PROMPT:
            return "exercise"
        content = self._last_content(messages)
        if '"knowledge"' in content:
            return "analyze"
        if '"error"' in content:
            return "diagnose"
        return "tutor"

    def _reply(self, kind: str) -> str:
        if kind == "ocr":
            return self.ocr_text
        if kind == "exercise":
            return json.dumps(self.exercises, ensure_ascii=False)
        if kind == "analyze":
            payload = json.dumps(
                {"knowledge": self.knowledge, "difficulty": self.difficulty, "type": self.qtype},
                ensure_ascii=False,
            )
            # 故意套上代码块围栏，顺带验证 _json_utils 的脏格式兜底
            return f"```json\n{payload}\n```"
        if kind == "diagnose":
            return json.dumps(
                {"error": "方法错", "reason": "移项时忘了变号", "gap": "等式性质"},
                ensure_ascii=False,
            )
        return f"第 {self.count('tutor')} 问：你先说说，这一步为什么要这样移项？"

    # ---------------------------------------------------------------- 断言辅助
    def kinds(self) -> List[str]:
        """按调用顺序返回类型列表。"""
        return [call["kind"] for call in self.calls]

    def count(self, kind: str) -> int:
        """某类调用发生了几次。"""
        return sum(1 for call in self.calls if call["kind"] == kind)


def patch_ark(monkeypatch, fake: Optional[FakeArk] = None) -> FakeArk:
    """把所有 ``ark_respond`` 引用点替换成假实现，测试结束由 monkeypatch 自动还原。

    Args:
        monkeypatch: pytest 的 monkeypatch 夹具。
        fake: 自定义假实现；缺省用默认 ``FakeArk()``。

    Returns:
        实际生效的 ``FakeArk`` 实例（可用于断言调用次数与参数）。
    """
    ark = fake or FakeArk()
    for target in ARK_PATCH_TARGETS:
        monkeypatch.setattr(target, ark)
    return ark


# ---------------------------------------------------------------- 响应断言
def assert_ok(response) -> Dict[str, Any]:
    """校验统一成功响应（HTTP 200 与 ``code==0`` 必须同步），返回 ``data``。"""
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 0, body
    assert body["msg"] == "ok", body
    assert isinstance(body["data"], dict), body
    return body["data"]


def assert_fail(response, code: int) -> Dict[str, Any]:
    """校验统一失败响应（HTTP 状态码与业务 code 同步），返回整个响应体。"""
    assert response.status_code == code, response.text
    body = response.json()
    assert body["code"] == code, body
    return body


# ---------------------------------------------------------------- 落库查询
def count_sessions(db, student_id: uuid.UUID) -> int:
    """该学生的 ``ai_sessions`` 条数（先结束旧事务，确保读到接口刚提交的数据）。"""
    db.rollback()
    return int(
        db.execute(
            select(func.count()).select_from(AiSession).where(AiSession.student_id == student_id)
        ).scalar_one()
    )


def load_messages(db, session_id: Any) -> List[AiMessage]:
    """按时间升序读出该会话的全部 ``ai_messages``。"""
    db.rollback()
    sid = session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
    return list(
        db.execute(
            select(AiMessage).where(AiMessage.session_id == sid).order_by(AiMessage.created_at.asc())
        ).scalars()
    )


__all__ = [
    "FakeArk",
    "patch_ark",
    "assert_ok",
    "assert_fail",
    "count_sessions",
    "load_messages",
    "ARK_PATCH_TARGETS",
    "DEFAULT_EXERCISES",
]
