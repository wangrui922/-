"""学习画像看板的响应模型（T04 · B7）。

⚠️ 命名风格与本目录其他 schema **不同**，这里是**故意的**：

    ``app/schemas/common.py::BaseSchema`` 会把字段名自动转成 camelCase，
    而 T04 新接口的契约明确要求 **snake_case**（与 /api/chat、/api/exercise 对齐，
    也就是「新接口统一 snake_case」这条约定）。所以本文件直接继承
    ``pydantic.BaseModel``，不套 ``BaseSchema``，避免 alias_generator 把
    ``kp_id`` 变成 ``kpId``。

这些模型主要用于**接口文档（/docs）**与类型自检；路由层实际返回的是
``app.core.response.ok(...)`` 包裹的普通 dict，两者字段保持一一对应。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ProfileBase(BaseModel):
    """画像域响应基类：保持 snake_case，禁止额外字段泄漏。"""

    model_config = ConfigDict(populate_by_name=True, extra="ignore", from_attributes=True)


# ==================================================================== 掌握度
class MasteryItem(ProfileBase):
    """单个知识点的掌握度。"""

    kp_id: str = Field(..., description="知识点 ID")
    kp_name: str = Field(..., description="知识点名称")
    score: float = Field(0.0, description="掌握度 0~1")
    level: int = Field(0, description="0未入门/1薄弱/2基本掌握/3熟练")
    attempts: int = Field(0, description="参与计算的事件总数")
    correct_count: int = Field(0, description="其中正确次数")
    error_rate: float = Field(0.0, description="错误率 0~1")
    effective_attempts: float = Field(0.0, description="时效衰减后的有效样本量")
    last_practiced_at: Optional[str] = Field(None, description="最近练习时间 ISO8601")


class MasterySummary(ProfileBase):
    """掌握度汇总。"""

    total_kp: int = Field(0, description="纳入统计的知识点总数")
    level_count: Dict[str, int] = Field(
        default_factory=dict, description='各等级知识点数量，如 {"0":1,"1":2,"2":0,"3":0}'
    )


class MasteryData(ProfileBase):
    """``GET /api/profile/mastery`` 的 data 载荷。"""

    student_id: str = Field(..., description="学生 ID")
    mastery: List[MasteryItem] = Field(default_factory=list, description="按等级升序的掌握度列表")
    summary: MasterySummary = Field(default_factory=MasterySummary, description="汇总")
    computed_at: Optional[str] = Field(None, description="计算时间 ISO8601")
    mode: str = Field("fresh", description="fresh 实时计算 / cache 命中物化缓存")


# ==================================================================== 学情雷达图
class RadarAxisItem(ProfileBase):
    """雷达图的一个轴（L1 知识点）。"""

    kp_id: str = Field(..., description="知识点 ID")
    kp_name: str = Field(..., description="知识点名称")
    score: float = Field(0.0, description="掌握度百分比 0~100")
    level: int = Field(0, description="0未入门/1薄弱/2基本掌握/3熟练")
    practiced: bool = Field(False, description="该知识点是否已有练习数据")


class RadarSummary(ProfileBase):
    """雷达图汇总。"""

    total_kp: int = Field(0, description="课程表全部 L1 知识点数")
    practiced_kp: int = Field(0, description="其中孩子已练习过的知识点数")
    overall_score: float = Field(0.0, description="已练知识点掌握度均值 0~100")
    level_count: Dict[str, int] = Field(
        default_factory=dict, description='各等级知识点数量，如 {"0":1,"1":2,"2":0,"3":0}'
    )


class RadarData(ProfileBase):
    """``GET /api/profile/radar`` 的 data 载荷。"""

    student_id: str = Field(..., description="学生 ID")
    axes: List[RadarAxisItem] = Field(default_factory=list, description="全部 L1 知识点轴")
    summary: RadarSummary = Field(default_factory=RadarSummary, description="汇总")
    computed_at: Optional[str] = Field(None, description="计算时间 ISO8601")
    mode: str = Field("fresh", description="fresh 实时计算 / cache 命中物化缓存")


# ==================================================================== 错因分布
class ErrorPatternItem(ProfileBase):
    """单类错因的聚合结果。"""

    category: str = Field(..., description="CONCEPT/METHOD/STEP/MISREAD/CALC/OTHER")
    label: str = Field(..., description="中文标签")
    count: int = Field(0, description="命中次数")
    ratio: float = Field(0.0, description="占比 0~1")
    sample_hits: List[str] = Field(default_factory=list, description="样例错因文本")


class ErrorPatternData(ProfileBase):
    """``GET /api/profile/error-patterns`` 的 data 载荷。"""

    student_id: str = Field(..., description="学生 ID")
    error_patterns: List[ErrorPatternItem] = Field(
        default_factory=list, description="恒返回 6 类，count 可为 0"
    )
    total_mistakes: int = Field(0, description="窗口内错题总数（含未关联知识点的）")
    unmapped_mistakes: int = Field(0, description="其中未关联知识点、不进掌握度计算的条数")
    range_days: int = Field(90, description="统计窗口天数")
    computed_at: Optional[str] = Field(None, description="计算时间 ISO8601")
    mode: str = Field("fresh", description="本接口恒为 fresh（不做窗口级缓存）")


# ==================================================================== 推荐
class RecommendationItem(ProfileBase):
    """一条练习推荐。"""

    kp_id: str = Field(..., description="知识点 ID")
    kp_name: str = Field(..., description="知识点名称")
    reason: str = Field("", description="后端生成的中文推荐理由")
    priority: float = Field(0.0, description="优先级 0~1，越大越该练")
    score: float = Field(0.0, description="当前掌握度")
    error_rate: float = Field(0.0, description="错误率")
    top_error_category: Optional[str] = Field(None, description="该点最突出的错因类别")
    suggested_question_ids: List[str] = Field(
        default_factory=list, description="可直接练的题目 ID（status=active）"
    )


class RecommendationData(ProfileBase):
    """``GET /api/profile/recommendations`` 的 data 载荷。"""

    student_id: str = Field(..., description="学生 ID")
    recommendations: List[RecommendationItem] = Field(default_factory=list, description="Top N 推荐")
    computed_at: Optional[str] = Field(None, description="计算时间 ISO8601")
    mode: str = Field("fresh", description="fresh / cache，跟随掌握度缓存状态")


__all__ = [
    "MasteryItem",
    "MasterySummary",
    "MasteryData",
    "RadarAxisItem",
    "RadarSummary",
    "RadarData",
    "ErrorPatternItem",
    "ErrorPatternData",
    "RecommendationItem",
    "RecommendationData",
]
