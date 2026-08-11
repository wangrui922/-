"""题库练习接口的请求 / 响应模型（题库消费层 · B4）。

⚠️ 命名风格（硬约束 C10）：本文件直接继承 ``pydantic.BaseModel``，
**禁止**继承 ``app/schemas/common.py::BaseSchema`` —— 后者的 alias_generator
会把 ``kp_id`` 自动变成 ``kpId``、``client_event_id`` 变成 ``clientEventId``，
与题库消费层「一律 snake_case」的接口契约冲突。写法对齐 ``schemas/profile.py``。

响应模型主要服务于 ``/docs`` 自动文档与类型自检；路由实际返回的是
``app.core.response.ok(...)`` 包裹的普通 dict，字段与这里一一对应。
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class PracticeBase(BaseModel):
    """练习域基类：保持 snake_case，忽略多余字段。"""

    model_config = ConfigDict(populate_by_name=True, extra="ignore", from_attributes=True)


# ==================================================================== 请求体
class DrawRequest(PracticeBase):
    """``POST /api/practice/session`` 请求体。

    全部字段可选：不传任何筛选条件时，从全部可归因 L1 安全池里随机抽 10 道。
    """

    grade: Optional[int] = Field(None, ge=1, le=9, description="年级（升入年级口径）")
    edition: Optional[str] = Field(None, max_length=16, description="教材版本：青岛版/苏教版")
    section: Optional[str] = Field(None, max_length=32, description="册内分区：温故/预习/提优/摸底/专项/自测")
    kp_id: Optional[str] = Field(None, description="指定知识点 ID")
    difficulty: Optional[int] = Field(None, description="难度 1/3/5")
    count: Optional[int] = Field(10, ge=1, le=30, description="题量，默认 10，上限 30")


class SubmitRequest(PracticeBase):
    """``POST /api/practice/answer`` 请求体。"""

    session_id: str = Field(..., description="练习会话 ID")
    question_id: str = Field(..., description="题目 ID")
    selected_answer: Optional[str] = Field(None, max_length=2000, description="学生作答")
    duration_sec: Optional[int] = Field(None, ge=0, description="单题耗时（秒），后端按 min(d,600) 兜底")
    client_event_id: Optional[str] = Field(None, max_length=64, description="客户端幂等键")


class SessionRequest(PracticeBase):
    """``POST /api/practice/finish`` 请求体。"""

    session_id: str = Field(..., description="练习会话 ID")


class QuestionRefRequest(PracticeBase):
    """``POST /api/practice/reveal`` 请求体。"""

    session_id: str = Field(..., description="练习会话 ID")
    question_id: str = Field(..., description="题目 ID")


class GuideRequest(PracticeBase):
    """``POST /api/practice/guide`` 请求体。"""

    session_id: str = Field(..., description="练习会话 ID")
    question_id: str = Field(..., description="题目 ID")
    work: Optional[str] = Field(None, max_length=4000, description="孩子当前的作答过程")


# ==================================================================== 响应体
class OptionOut(PracticeBase):
    """归一化后的选项。"""

    key: str = Field(..., description="键位 A/B/C/D")
    text: str = Field("", description="选项正文")


class DrawQuestionOut(PracticeBase):
    """抽题出参里的单题。"""

    question_id: str = Field(..., description="题目 ID")
    type: str = Field("", description="题型：选择/判断")
    stem: str = Field("", description="题干")
    options: List[OptionOut] = Field(default_factory=list, description="选项（判断题为空）")
    difficulty: Optional[int] = Field(None, description="难度 1/3/5")
    grade: Optional[int] = Field(None, description="年级")
    edition: Optional[str] = Field(None, description="教材版本")
    section: Optional[str] = Field(None, description="册内分区")
    kp_id: Optional[str] = Field(None, description="知识点 ID，新题可能为空")


class DrawResponse(PracticeBase):
    """``/session`` 响应 data。"""

    session_id: str = Field(..., description="练习会话 ID")
    questions: List[DrawQuestionOut] = Field(default_factory=list, description="本次题目")
    pool_size: int = Field(0, description="实际使用的抽题池大小")
    unmapped_count: int = Field(0, description="池中不可归因（无知识点）的题数，诚实披露")
    auto_relaxed: bool = Field(False, description="是否自动放开到了全 L1 安全池")
    requested_count: int = Field(0, description="请求题量（夹取后）")


class SubmitResponse(PracticeBase):
    """``/answer`` 响应 data。

    ⚠️ 这里**刻意没有** ``audit_reason``（``JudgeOutcome`` 的对内字段）。判分的
    完整依据含标准答案，只进日志、不进响应体：Q3 拍板的口径是「默认不给答案，
    主按钮是让老师带我想」，想看标准答案必须显式走 ``/reveal``（且只对做错的题
    开放）。往本模型加字段前请先确认它不会把答案带出去。
    """

    question_id: str = Field(..., description="题目 ID")
    is_correct: bool = Field(False, description="是否判对；level=4 时恒为 false")
    level: int = Field(1, description="1=L1 规则判分；4=无法自动判定")
    reason: str = Field("", description="对外判分说明；答错时不含标准答案（想看答案走 /reveal）")
    duration_sec: Optional[int] = Field(None, description="落库的耗时（已 min(d,600)）")


class RevealResponse(PracticeBase):
    """``/reveal`` 响应 data。"""

    question_id: str = Field(..., description="题目 ID")
    correct_answer: str = Field("", description="参考答案")
    analysis: str = Field("", description="解析")


class FinishItemOut(PracticeBase):
    """结算出参里的逐题快照。"""

    question_id: str = Field(..., description="题目 ID")
    status: str = Field("pending", description="pending/done")
    is_correct: Optional[bool] = Field(None, description="是否判对，未作答为 null")
    level: Optional[int] = Field(None, description="判分层级")
    selected_answer: Optional[str] = Field(None, description="学生作答")
    duration_sec: Optional[int] = Field(None, description="耗时（秒）")


class WeakPointOut(PracticeBase):
    """结算出参里的薄弱知识点。"""

    kp_id: str = Field(..., description="知识点 ID")
    kp_name: str = Field("", description="知识点名称")
    wrong_count: int = Field(0, description="本次错题数")


class FinishResponse(PracticeBase):
    """``/finish`` 响应 data（契约 6 字段 + 结果页所需附加字段）。"""

    session_id: str = Field(..., description="练习会话 ID")
    total: int = Field(0, description="本次题目总数")
    correct: int = Field(0, description="答对题数")
    wrong: int = Field(0, description="答错题数")
    mistakes_created: int = Field(0, description="新建的错题本条数（幂等，重复结算为 0）")
    mastery_refreshed: bool = Field(False, description="本次是否练到了可归因知识点（会进掌握度闭环）")
    answered: int = Field(0, description="实际作答题数")
    accuracy: float = Field(0.0, description="正确率 0~1（按已作答题计）")
    unmapped_count: int = Field(0, description="本次题目里无知识点的题数")
    items: List[FinishItemOut] = Field(default_factory=list, description="逐题清单")
    weak_points: List[WeakPointOut] = Field(default_factory=list, description="薄弱知识点 Top5")


class GuideResponse(PracticeBase):
    """``/guide`` 响应 data。"""

    question_id: str = Field(..., description="题目 ID")
    reply: str = Field("", description="给孩子看的引导语（首轮不给答案）")
    ask_final_confirm: bool = Field(False, description="老师是否在问「这是最终答案吗」")
    prompt_version: str = Field("", description="提示词版本，便于回溯")


#: 供路由层做 ``Any`` 兜底时引用（保持 mypy 友好）
JsonDict = Any

__all__ = [
    "PracticeBase",
    "DrawRequest",
    "SubmitRequest",
    "SessionRequest",
    "QuestionRefRequest",
    "GuideRequest",
    "OptionOut",
    "DrawQuestionOut",
    "DrawResponse",
    "SubmitResponse",
    "RevealResponse",
    "FinishItemOut",
    "WeakPointOut",
    "FinishResponse",
    "GuideResponse",
    "JsonDict",
]
