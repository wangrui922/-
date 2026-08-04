"""短信验证码服务（V4.1 **占位**，不接真实短信通道）。

现状与边界：
    * 老板拍板 V4.1 不接短信，登录靠 **dev 万能码**（``settings.dev_sms_code``，默认 ``000000``）；
    * 本模块把「发码 / 验码」抽象成 ``SmsProvider`` 协议，V4.2 接腾讯云 SMS 时
      只需新增一个 Provider 并改 ``SMS_PROVIDER`` 环境变量，业务层零改动；
    * 生产环境（``APP_ENV=prod``）会落到 ``NotImplementedSmsProvider``，
      调用即抛 ``NotImplementedError``，避免「以为接了其实没接」的静默风险。

Provider 选择：由 ``settings.sms_provider`` 决定
    * ``mock``（默认）—— 发码只打日志；验码接受 ``settings.dev_sms_code``。
    * 其他值        —— ``NotImplementedSmsProvider``，抛 ``NotImplementedError``。
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Final

from app.core.config import settings

logger = logging.getLogger("app.services.sms")

#: 中国大陆手机号
PHONE_PATTERN: Final = re.compile(r"^1[3-9]\d{9}$")

#: 验证码位数
CODE_LENGTH: Final[int] = 6

PROVIDER_MOCK: Final[str] = "mock"


def is_valid_phone(phone: str) -> bool:
    """校验手机号格式（中国大陆 11 位）。"""
    return bool(phone) and bool(PHONE_PATTERN.match(phone.strip()))


class SmsProvider(ABC):
    """短信通道抽象。"""

    name: str = "base"

    @abstractmethod
    def send_code(self, phone: str) -> bool:
        """下发验证码。

        Args:
            phone: 目标手机号。

        Returns:
            是否下发成功。
        """
        raise NotImplementedError

    @abstractmethod
    def verify_code(self, phone: str, code: str) -> bool:
        """校验验证码。

        Args:
            phone: 手机号。
            code: 用户输入的验证码。

        Returns:
            是否校验通过。
        """
        raise NotImplementedError


class MockSmsProvider(SmsProvider):
    """开发用假通道：不发真短信，只认万能码。"""

    name = PROVIDER_MOCK

    def send_code(self, phone: str) -> bool:
        """只打日志，永远返回 True。"""
        logger.info(
            "[sms:mock] 假装向 %s 下发了验证码；开发期请直接使用万能码 %s",
            phone,
            settings.dev_sms_code,
        )
        return True

    def verify_code(self, phone: str, code: str) -> bool:
        """只接受配置里的万能码。"""
        expected = (settings.dev_sms_code or "").strip()
        actual = (code or "").strip()
        passed = bool(expected) and actual == expected
        logger.info("[sms:mock] 校验 %s 的验证码 -> %s", phone, "通过" if passed else "不通过")
        return passed


class NotImplementedSmsProvider(SmsProvider):
    """真实通道占位：V4.1 未接入，调用即抛错（避免静默放行）。"""

    name = "not-implemented"

    def send_code(self, phone: str) -> bool:
        raise NotImplementedError(
            f"短信通道 {settings.sms_provider!r} 尚未接入（V4.1 不接真短信），无法向 {phone} 下发验证码"
        )

    def verify_code(self, phone: str, code: str) -> bool:
        raise NotImplementedError(
            f"短信通道 {settings.sms_provider!r} 尚未接入（V4.1 不接真短信），无法校验 {phone} 的验证码"
        )


def get_provider() -> SmsProvider:
    """按配置返回当前短信通道实例。"""
    provider = (settings.sms_provider or PROVIDER_MOCK).strip().lower()
    if provider == PROVIDER_MOCK:
        return MockSmsProvider()
    return NotImplementedSmsProvider()


def send_code(phone: str) -> bool:
    """模块级便捷函数：下发验证码。"""
    return get_provider().send_code(phone)


def verify_code(phone: str, code: str) -> bool:
    """模块级便捷函数：校验验证码。"""
    return get_provider().verify_code(phone, code)


__all__ = [
    "SmsProvider",
    "MockSmsProvider",
    "NotImplementedSmsProvider",
    "get_provider",
    "send_code",
    "verify_code",
    "is_valid_phone",
    "PHONE_PATTERN",
    "CODE_LENGTH",
    "PROVIDER_MOCK",
]
