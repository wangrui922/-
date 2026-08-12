"""短信验证码服务。

现状与边界：
    * 本模块把「发码 / 验码」抽象成 ``SmsProvider`` 协议，V4.2 接腾讯云 SMS 时
      只需新增一个 Provider 并改 ``SMS_PROVIDER`` 环境变量，业务层零改动；
    * **MockSmsProvider**（默认）—— 生成随机 6 位验证码，存内存 5 分钟过期；
      dev 环境通过 ``get_last_sent_code()`` 拿到码回传前端，生产环境不回传；
    * 生产环境（``APP_ENV=prod`` + ``SMS_PROVIDER != mock``）会落到
      ``NotImplementedSmsProvider``，调用即抛 ``NotImplementedError``，
      避免「以为接了其实没接」的静默风险。

Provider 选择：由 ``settings.sms_provider`` 决定
    * ``mock``（默认）—— 随机验证码，内存存储，dev 环境可回传。
    * 其他值        —— ``NotImplementedSmsProvider``，抛 ``NotImplementedError``。
"""

from __future__ import annotations

import logging
import random
import re
import time
from abc import ABC, abstractmethod
from typing import Dict, Final, Optional, Tuple
from threading import Lock

from app.core.config import settings

logger = logging.getLogger("app.services.sms")

#: 中国大陆手机号
PHONE_PATTERN: Final = re.compile(r"^1[3-9]\d{9}$")

#: 验证码位数
CODE_LENGTH: Final[int] = 6

#: 验证码有效期（秒）
CODE_TTL_SEC: Final[int] = 300  # 5 分钟

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
    """开发用假通道：生成随机验证码，内存存储，5 分钟过期。

    验证码验证后立即删除（一次性），防止重放攻击。
    """

    name = PROVIDER_MOCK

    # 类级共享存储：phone -> (code, expiry_timestamp)
    _codes: Dict[str, Tuple[str, float]] = {}
    _lock: Lock = Lock()

    def send_code(self, phone: str) -> bool:
        """生成随机 6 位验证码并存入内存。"""
        code = f"{random.randint(0, 999999):0{CODE_LENGTH}d}"
        expiry = time.time() + CODE_TTL_SEC
        with self._lock:
            self._codes[phone] = (code, expiry)
        logger.info("[sms:mock] 向 %s 下发验证码 (有效期 %ds)", phone, CODE_TTL_SEC)
        return True

    def verify_code(self, phone: str, code: str) -> bool:
        """校验验证码：检查存在、过期、匹配；通过后立即删除。"""
        with self._lock:
            stored = self._codes.get(phone)
            if stored is None:
                logger.info("[sms:mock] %s 无已发验证码 -> 不通过", phone)
                return False

            stored_code, expiry = stored
            if time.time() > expiry:
                del self._codes[phone]
                logger.info("[sms:mock] %s 验证码已过期 -> 不通过", phone)
                return False

            passed = (code or "").strip() == stored_code
            if passed:
                del self._codes[phone]  # 一次性使用
            logger.info("[sms:mock] 校验 %s 的验证码 -> %s", phone, "通过" if passed else "不通过")
            return passed

    @classmethod
    def get_last_sent_code(cls, phone: str) -> Optional[str]:
        """获取已下发的验证码（dev 环境回传前端用）。过期返回 None。"""
        with cls._lock:
            stored = cls._codes.get(phone)
            if stored is None:
                return None
            code, expiry = stored
            if time.time() > expiry:
                return None
            return code

    @classmethod
    def _reset(cls) -> None:
        """清空所有存储的验证码（测试用）。"""
        with cls._lock:
            cls._codes.clear()


class NotImplementedSmsProvider(SmsProvider):
    """真实通道占位：未接入，调用即抛错（避免静默放行）。"""

    name = "not-implemented"

    def send_code(self, phone: str) -> bool:
        raise NotImplementedError(
            f"短信通道 {settings.sms_provider!r} 尚未接入，无法向 {phone} 下发验证码"
        )

    def verify_code(self, phone: str, code: str) -> bool:
        raise NotImplementedError(
            f"短信通道 {settings.sms_provider!r} 尚未接入，无法校验 {phone} 的验证码"
        )


# ---------------------------------------------------------------- 单例缓存
_provider_instance: Optional[SmsProvider] = None


def get_provider() -> SmsProvider:
    """按配置返回当前短信通道实例（单例）。"""
    global _provider_instance
    if _provider_instance is not None:
        return _provider_instance

    provider = (settings.sms_provider or PROVIDER_MOCK).strip().lower()
    if provider == PROVIDER_MOCK:
        _provider_instance = MockSmsProvider()
    else:
        _provider_instance = NotImplementedSmsProvider()
    return _provider_instance


def send_code(phone: str) -> bool:
    """模块级便捷函数：下发验证码。"""
    return get_provider().send_code(phone)


def verify_code(phone: str, code: str) -> bool:
    """模块级便捷函数：校验验证码。"""
    return get_provider().verify_code(phone, code)


def get_last_sent_code(phone: str) -> Optional[str]:
    """模块级便捷函数：获取已下发的验证码（dev 环境回传前端用）。"""
    provider = get_provider()
    if isinstance(provider, MockSmsProvider):
        return provider.get_last_sent_code(phone)
    return None


__all__ = [
    "SmsProvider",
    "MockSmsProvider",
    "NotImplementedSmsProvider",
    "get_provider",
    "send_code",
    "verify_code",
    "get_last_sent_code",
    "is_valid_phone",
    "PHONE_PATTERN",
    "CODE_LENGTH",
    "CODE_TTL_SEC",
    "PROVIDER_MOCK",
]
