"""家长周报的响应模型（Epic6 · T4/T5）。

⚠️ 与 ``schemas/profile.py`` 同款约定，这里是**故意的**：

    直接继承 ``pydantic.BaseModel``，**禁止继承 ``schemas/common.BaseSchema``**。
    后者的 ``alias_generator`` 会把 ``kp_id`` 变成 ``kpId``，
    而前端 ``ErrorPatternChart`` / ``MasteryBar`` 复用的就是 snake_case 结构，
    一旦被 alias 掉，前端组件零改造复用的前提直接报废。

这些模型主要用于**接口文档（/docs）**与类型自检；路由层实际返回的是
``app.core.response.ok(...)`` 包裹的普通 dict，两者字段保持一一对应。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ReportBase(BaseModel):
    """周报域响应基类：保持 snake_case，禁止额外字段泄漏。"""

    model_config = ConfigDict(populate_by_name=True, extra="ignore", from_attributes=True)


# ==================================================================== 请求体
class GenerateReportRequest(ReportBase):
    """``POST /api/student/report/generate`` 的入参（全可选）。"""

    force: bool = Field(False, description="true=跳过缓存强制重算并重调模型")


# ==================================================================== 生成结果
class GenerateReportData(ReportBase):
    """``POST /api/student/report/generate`` 的 data 载荷。"""

    report_id: str = Field(..., description="周报 ID")
    share_token: str = Field(..., description="分享令牌（重新生成不会变）")
    share_url: str = Field(..., description="发给家长的完整链接")
    period_start: Optional[str] = Field(None, description="周期起（北京时间 ISO8601）")
    period_end: Optional[str] = Field(None, description="周期止（北京时间 ISO8601）")
    is_partial: bool = Field(False, description="本周尚未结束")
    expires_at: Optional[str] = Field(None, description="链接过期时刻 ISO8601")
    cached: bool = Field(False, description="true=命中缓存未重算")
    ai_source: str = Field("fallback", description="ai=模型生成 / fallback=本地降级文案")


# ==================================================================== 分享载荷
class ReportStudent(ReportBase):
    """分享页展示的学生信息（**只有这两个字段**，见 S17）。"""

    display_name: str = Field("孩子", description="学生姓名，为空时降级为「孩子」")
    grade: Optional[int] = Field(None, description="年级")


class ReportPeriodData(ReportBase):
    """分享页展示的统计周期。"""

    start: Optional[str] = Field(None, description="周期起（北京时间日期 YYYY-MM-DD）")
    end: Optional[str] = Field(None, description="周期止（北京时间日期 YYYY-MM-DD）")
    is_partial: bool = Field(False, description="本周尚未结束")


class ReportEngagement(ReportBase):
    """本周学习投入的真实计数（**不含学习时长**，见设计文档 K3）。"""

    study_days: int = Field(0, description="学习天数（按北京时间日期去重）")
    attempt_count: int = Field(0, description="做题数")
    correct_count: int = Field(0, description="做对数")
    correct_rate: Optional[float] = Field(None, description="正确率；零练习时为 null（不是 0）")
    new_mistake_count: int = Field(0, description="本周新增错题数")
    solved_mistake_count: int = Field(0, description="本周攻克错题数")
    tutor_session_count: int = Field(0, description="本周辅导会话次数")


class ReportWeakPoint(ReportBase):
    """一条薄弱知识点（字段直喂前端 MasteryBar）。"""

    kp_id: str = Field("", description="知识点 ID")
    kp_name: str = Field("", description="知识点名称")
    score: float = Field(0.0, description="当前分值 0~1")
    level: int = Field(0, description="0未入门/1薄弱/2基本掌握/3熟练")
    error_rate: float = Field(0.0, description="错误率 0~1")
    prev_score: Optional[float] = Field(None, description="上周分值，无历史时为 null")
    delta: Optional[float] = Field(None, description="相对上周的变化，无历史时为 null")


class ReportComparison(ReportBase):
    """与上一份周报的环比结果。"""

    available: bool = Field(False, description="是否有上周数据可比")
    reason: str = Field("first_report", description="first_report | ok")
    prev_period_start: Optional[str] = Field(None, description="上一份周报的周期起点")
    correct_rate_delta: Optional[float] = Field(None, description="正确率变化，任一侧缺失时为 null")
    improved: List[Dict[str, Any]] = Field(default_factory=list, description="进步的知识点")
    declined: List[Dict[str, Any]] = Field(default_factory=list, description="退步的知识点")


class ReportNarrative(ReportBase):
    """给家长的三段话。"""

    headline: str = Field("", description="一句话总结")
    overview_text: str = Field("", description="第一段：本周学习概览")
    progress_text: str = Field("", description="第二段：能力变化")
    advice_items: List[str] = Field(default_factory=list, description="第三段：2~3 条可执行建议")
    source: str = Field("fallback", description="ai | fallback（前端不得展示）")


class ReportFlag(ReportBase):
    """空态 / 低数据提示。"""

    is_empty: bool = Field(False, description="是否命中该状态")
    message: Optional[str] = Field(None, description="提示文案，未命中时为 null")


class ReportLowDataFlag(ReportBase):
    """练习量偏少提示。"""

    is_low: bool = Field(False, description="是否练习量偏少（0 < n < 3）")
    message: Optional[str] = Field(None, description="提示文案，未命中时为 null")


class SharedReportData(ReportBase):
    """``GET /api/report/share/{token}`` 的 data 载荷（公开、无登录）。"""

    student: ReportStudent = Field(default_factory=ReportStudent, description="学生信息")
    period: ReportPeriodData = Field(default_factory=ReportPeriodData, description="统计周期")
    engagement: ReportEngagement = Field(default_factory=ReportEngagement, description="学习投入")
    error_patterns: Dict[str, Any] = Field(
        default_factory=dict,
        description="与 /api/profile/error-patterns 的 data 同构，前端图表零改造复用",
    )
    weak_points: List[ReportWeakPoint] = Field(default_factory=list, description="Top3 薄弱知识点")
    comparison: ReportComparison = Field(default_factory=ReportComparison, description="环比")
    narrative: ReportNarrative = Field(default_factory=ReportNarrative, description="三段叙述")
    empty_state: ReportFlag = Field(default_factory=ReportFlag, description="整页空态")
    low_data: ReportLowDataFlag = Field(default_factory=ReportLowDataFlag, description="低数据提示")
    generated_at: Optional[str] = Field(None, description="生成时间（北京时间 ISO8601）")
    expires_at: Optional[str] = Field(None, description="链接过期时刻 ISO8601")


# ==================================================================== 历史列表
class ReportHistoryItem(ReportBase):
    """历史周报摘要。"""

    report_id: str = Field(..., description="周报 ID")
    period_start: Optional[str] = Field(None, description="周期起 YYYY-MM-DD")
    period_end: Optional[str] = Field(None, description="周期止 YYYY-MM-DD")
    share_token: str = Field("", description="分享令牌")
    share_url: str = Field("", description="分享链接")
    expires_at: Optional[str] = Field(None, description="过期时刻 ISO8601")
    created_at: Optional[str] = Field(None, description="首次生成时间 ISO8601")
    view_count: int = Field(0, description="家长打开次数")
    is_expired: bool = Field(False, description="链接是否已失效")


class ReportHistoryData(ReportBase):
    """``GET /api/student/report/history`` 的 data 载荷。"""

    items: List[ReportHistoryItem] = Field(default_factory=list, description="按周期倒序")
    total: int = Field(0, description="条数")


__all__ = [
    "GenerateReportRequest",
    "GenerateReportData",
    "ReportStudent",
    "ReportPeriodData",
    "ReportEngagement",
    "ReportWeakPoint",
    "ReportComparison",
    "ReportNarrative",
    "ReportFlag",
    "ReportLowDataFlag",
    "SharedReportData",
    "ReportHistoryItem",
    "ReportHistoryData",
]
