"""T13 合规域出入参 Schema（camelCase alias，见 schemas/common.BaseSchema）。

命名约定（全项目一致）：字段声明用 snake_case，对外 camelCase 由
``BaseSchema`` 的 ``alias_generator`` 自动转换，无需手写 alias。
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import BaseSchema


class GuardianConsentIn(BaseSchema):
    """``POST /api/compliance/guardian-consent`` 入参。

    Attributes:
        guardian_phone: 监护人手机号（同意动作的联系方式，留痕备查）。
        consent_version: 同意的规则版本号，与公示版
            《儿童个人信息处理规则-内测版》的版本一一对应。
        agreed: 是否同意；仅 ``true`` 会被记录，``false`` 按 400 拒绝。
    """

    guardian_phone: str = Field(
        ..., min_length=5, max_length=20, description="监护人手机号"
    )
    consent_version: str = Field(
        ..., min_length=1, max_length=32, description="同意的规则版本号"
    )
    agreed: bool = Field(..., description="是否同意（仅 true 会被记录）")


__all__ = ["GuardianConsentIn"]
