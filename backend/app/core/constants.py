"""T04 学习画像常量：错因枚举、关键词表、掌握度算法权重（B1）。

设计原则：
    * **所有可调参数集中在此**，算法层（``app.services.mastery_engine``）
      与服务层（``app.services.profile_service``）不再散落魔法数字；
    * 错因分类是**纯本地规则**（OQ2），不调方舟、不产生外部依赖；
    * 关键词表按「优先级仲裁顺序」排列，命中多个类别时取优先级最高者
      （``CONCEPT > METHOD > STEP > MISREAD > CALC > OTHER``）。
"""

from __future__ import annotations

from typing import Dict, Final, Tuple

# ==================================================================== 错因枚举
# 唯一真相源已迁到 ``app.core.error_categories``（缺口②统一枚举：5 类）。
# 此处仅做再导出，保证既有 import 路径（``from app.core.constants import ERROR_*``）不变。
from app.core.error_categories import (  # noqa: E402,F401
    ERROR_READ,
    ERROR_METHOD,
    ERROR_CALC,
    ERROR_KNOWLEDGE,
    ERROR_CHECK,
    ERROR_UNCLASSIFIED,
    ERROR_CATEGORIES,
    ERROR_KEYWORDS,
    error_label,
)
from app.core.error_categories import ERROR_LABELS as ERROR_CATEGORY_LABELS  # noqa: E402,F401

# ============================================================ 掌握度算法权重
#: 时效衰减半衰期（天）：距今 14 天的一次作答，权重衰减为 0.5
MASTERY_HALF_LIFE_DAYS: Final[float] = 14.0

#: 贝叶斯先验强度 K：等价于「先验值已经有 K 次有效作答的分量」
MASTERY_CONFIDENCE_K: Final[float] = 3.0

#: 掌握度先验分（无任何数据时的中性起点）
MASTERY_PRIOR_SCORE: Final[float] = 0.5

#: 掌握度物化缓存 TTL（秒），OQ3 = 15 分钟
MASTERY_CACHE_TTL_SECONDS: Final[int] = 900

#: 推荐条数上限
RECOMMEND_TOP_N: Final[int] = 5

#: 每类错因最多保留几条证据文本
ERROR_EVIDENCE_LIMIT: Final[int] = 5

#: 每个推荐知识点最多附带几道练习题 ID
RECOMMEND_QUESTION_LIMIT: Final[int] = 3

# ---------------------------------------------------------------- 分级阈值
#: level 1 下限（低于此值为 level 0）
MASTERY_LEVEL1_MIN: Final[float] = 0.40
#: level 2 下限
MASTERY_LEVEL2_MIN: Final[float] = 0.60
#: level 3 下限
MASTERY_LEVEL3_MIN: Final[float] = 0.85
#: 判定 level 2 所需的最小有效样本量
MASTERY_LEVEL2_MIN_EFFECTIVE: Final[float] = 3.0
#: 判定 level 3 所需的最小有效样本量
MASTERY_LEVEL3_MIN_EFFECTIVE: Final[float] = 5.0

#: 掌握度等级中文标签
MASTERY_LEVEL_LABELS: Final[Dict[int, str]] = {
    0: "未入门",
    1: "薄弱",
    2: "基本掌握",
    3: "熟练",
}

# ---------------------------------------------------------------- 事件源权重
#: ``attempts`` 表的作答流水（最可信的一手数据）
EVENT_WEIGHT_ATTEMPT: Final[float] = 1.0
#: ``mistakes`` 建档推断出的负样本（该题没有作答流水时才计入，避免重复计数）
EVENT_WEIGHT_MISTAKE: Final[float] = 0.8
#: 错题被标记攻克 → 近似正样本（OQ 默认值：solved 近似正确）
EVENT_WEIGHT_MISTAKE_SOLVED: Final[float] = 0.6

# ================================================================ 其他默认值
#: ``/api/profile/error-patterns`` 默认统计窗口（天）
ERROR_PATTERN_DEFAULT_DAYS: Final[int] = 90
#: 统计窗口允许的最大天数（防止误传超大值拖垮查询）
ERROR_PATTERN_MAX_DAYS: Final[int] = 3650

#: 画像计算异常时的业务码（不抛 500，让看板走局部空态）
CODE_PROFILE_COMPUTE_FAILED: Final[int] = 5001

#: 缓存命中标识
MODE_CACHE: Final[str] = "cache"
#: 实时计算标识
MODE_FRESH: Final[str] = "fresh"

# ========================================================== Epic6 家长周报
#: 周报统计周期长度（天）——自然周
REPORT_PERIOD_DAYS: Final[int] = 7
#: 分享令牌随机字节数，``secrets.token_urlsafe(24)`` → 32 字符 URL-safe
REPORT_TOKEN_BYTES: Final[int] = 24
#: 分享链接有效期（天）
REPORT_SHARE_TTL_DAYS: Final[int] = 30
#: 分享页展示的薄弱知识点条数
REPORT_WEAK_POINT_TOP_N: Final[int] = 3
#: 低于该作答量时提示「数据较少，仅供参考」
REPORT_LOW_DATA_THRESHOLD: Final[int] = 3
#: 快照结构版本号（改结构必须升版，diff 逻辑靠它识别老快照）
REPORT_SNAPSHOT_SCHEMA_VERSION: Final[int] = 1
#: 环比变化阈值：|delta| 小于该值视为无变化（抑制噪声）
REPORT_DELTA_THRESHOLD: Final[float] = 0.05
#: 周报生成失败的业务码（5001 是画像计算失败，不复用）
CODE_REPORT_GENERATE_FAILED: Final[int] = 5002
#: 「自然周」所用本地时区偏移（东八区），不引 pytz / zoneinfo
LOCAL_TZ_OFFSET_HOURS: Final[int] = 8


def mastery_level_label(level: int) -> str:
    """取掌握度等级的中文标签。

    Args:
        level: 0~3 的等级。

    Returns:
        中文标签字符串，越界时回退到「未入门」。
    """
    return MASTERY_LEVEL_LABELS.get(int(level), MASTERY_LEVEL_LABELS[0])


__all__ = [
    "ERROR_READ",
    "ERROR_METHOD",
    "ERROR_CALC",
    "ERROR_KNOWLEDGE",
    "ERROR_CHECK",
    "ERROR_UNCLASSIFIED",
    "ERROR_CATEGORIES",
    "ERROR_CATEGORY_LABELS",
    "ERROR_KEYWORDS",
    "MASTERY_HALF_LIFE_DAYS",
    "MASTERY_CONFIDENCE_K",
    "MASTERY_PRIOR_SCORE",
    "MASTERY_CACHE_TTL_SECONDS",
    "RECOMMEND_TOP_N",
    "ERROR_EVIDENCE_LIMIT",
    "RECOMMEND_QUESTION_LIMIT",
    "MASTERY_LEVEL1_MIN",
    "MASTERY_LEVEL2_MIN",
    "MASTERY_LEVEL3_MIN",
    "MASTERY_LEVEL2_MIN_EFFECTIVE",
    "MASTERY_LEVEL3_MIN_EFFECTIVE",
    "MASTERY_LEVEL_LABELS",
    "EVENT_WEIGHT_ATTEMPT",
    "EVENT_WEIGHT_MISTAKE",
    "EVENT_WEIGHT_MISTAKE_SOLVED",
    "ERROR_PATTERN_DEFAULT_DAYS",
    "ERROR_PATTERN_MAX_DAYS",
    "CODE_PROFILE_COMPUTE_FAILED",
    "MODE_CACHE",
    "MODE_FRESH",
    # Epic6 家长周报
    "REPORT_PERIOD_DAYS",
    "REPORT_TOKEN_BYTES",
    "REPORT_SHARE_TTL_DAYS",
    "REPORT_WEAK_POINT_TOP_N",
    "REPORT_LOW_DATA_THRESHOLD",
    "REPORT_SNAPSHOT_SCHEMA_VERSION",
    "REPORT_DELTA_THRESHOLD",
    "CODE_REPORT_GENERATE_FAILED",
    "LOCAL_TZ_OFFSET_HOURS",
    "error_label",
    "mastery_level_label",
]
