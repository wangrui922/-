"""短信验证码服务。

现状与边界：
    * 本模块把「发码 / 验码」抽象成 ``SmsProvider`` 协议，V4.2 接腾讯云 SMS 时
      只需新增一个 Provider 并改 ``SMS_PROVIDER`` 环境变量，业务层零改动；
    * **MockSmsProvider**（默认）—— 生成随机 6 位验证码，存内存 5 分钟过期；
      dev 环境通过 ``get_last_sent_code()`` 拿到码回传前端，生产环境不回传；
    * **TencentSmsProvider** —— ``SMS_PROVIDER=tencent`` 时启用，走腾讯云
      SMS API（v2021-01-11，TC3-HMAC-SHA256 签名，纯标准库实现无 SDK 依赖）；
      验证码本地内存存储（与 Mock 同机制），下发时调用腾讯云 SendSms 真实发送；
      未配置密钥或发送失败时抛清晰异常，避免「以为接了其实没接」的静默风险。
    * 生产环境（``APP_ENV=prod``）下 mock 通道仍可用但验证码不回传前端。

Provider 选择：由 ``settings.sms_provider`` 决定
    * ``mock``（默认）—— 随机验证码，内存存储，dev 环境可回传。
    * ``tencent`` —— 腾讯云短信真实下发（需配置 SMS_TENCENT_* 环境变量）。
    * 其他值        —— ``NotImplementedSmsProvider``，抛 ``NotImplementedError``。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import re
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from threading import Lock
from typing import Dict, Final, List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger("app.services.sms")

#: 中国大陆手机号
PHONE_PATTERN: Final = re.compile(r"^1[3-9]\d{9}$")

#: 验证码位数
CODE_LENGTH: Final[int] = 6

#: 验证码有效期（秒）
CODE_TTL_SEC: Final[int] = 300  # 5 分钟

PROVIDER_MOCK: Final[str] = "mock"
PROVIDER_TENCENT: Final[str] = "tencent"


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


class TencentSmsProvider(SmsProvider):
    """腾讯云短信真实通道（v2021-01-11，TC3-HMAC-SHA256 签名）。

    验证码本地内存存储（与 Mock 同机制，5 分钟过期、一次性），
    下发时调用腾讯云 ``SendSms`` API 真实发送到用户手机。

    配置（环境变量）：
        * ``SMS_TENCENT_SECRET_ID``   腾讯云 API 密钥 ID（CAM 生成）
        * ``SMS_TENCENT_SECRET_KEY``  腾讯云 API 密钥 Key
        * ``SMS_TENCENT_SDK_APP_ID``  短信应用 ID（SdkAppId）
        * ``SMS_TENCENT_SIGN_NAME``   已审核通过的短信签名
        * ``SMS_TENCENT_TEMPLATE_ID`` 已审核通过的验证码模板 ID

    缺失配置或 API 返回错误时抛 ``RuntimeError``，业务层应转为 5xx，
    严禁静默放行。
    """

    name = PROVIDER_TENCENT

    _SERVICE: Final[str] = "sms"
    _VERSION: Final[str] = "2021-01-11"
    _ACTION: Final[str] = "SendSms"

    # 类级共享存储：phone -> (code, expiry_timestamp)，与 Mock 同机制
    _codes: Dict[str, Tuple[str, float]] = {}
    _lock: Lock = Lock()

    # ------------------------------------------------------------ 配置校验
    def _required_config(self) -> Tuple[str, str, str, str, str]:
        """读取并校验腾讯云短信必需配置，缺失则抛 RuntimeError。"""
        fields = {
            "SMS_TENCENT_SECRET_ID": settings.sms_tencent_secret_id,
            "SMS_TENCENT_SECRET_KEY": settings.sms_tencent_secret_key,
            "SMS_TENCENT_SDK_APP_ID": settings.sms_tencent_sdk_app_id,
            "SMS_TENCENT_SIGN_NAME": settings.sms_tencent_sign_name,
            "SMS_TENCENT_TEMPLATE_ID": settings.sms_tencent_template_id,
        }
        missing = [name for name, val in fields.items() if not (val or "").strip()]
        if missing:
            raise RuntimeError(
                f"腾讯云短信配置缺失: {', '.join(missing)}，请检查 .env.production"
            )
        return tuple(str(v).strip() for v in fields.values())  # type: ignore[return-value]

    # ------------------------------------------------------------ TC3 签名
    @staticmethod
    def _tc3_hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    def _send_sms(self, phone: str, code: str) -> None:
        """调用腾讯云 SendSms 下发验证码（TC3-HMAC-SHA256 签名）。"""
        secret_id, secret_key, sdk_app_id, sign_name, template_id = self._required_config()

        host = (settings.sms_tencent_endpoint or "sms.tencentcloudapi.com").strip()
        region = (settings.sms_tencent_region or "ap-guangzhou").strip()
        service = self._SERVICE

        payload = {
            "PhoneNumberSet": [f"+86{phone}"],
            "SmsSdkAppId": sdk_app_id,
            "SignName": sign_name,
            "TemplateId": template_id,
            "TemplateParamSet": [code],
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        timestamp = int(time.time())
        date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))

        # 1) CanonicalRequest
        canonical_headers = (
            f"content-type:application/json; charset=utf-8\n"
            f"host:{host}\n"
            f"x-tc-action:{self._ACTION.lower()}\n"
        )
        signed_headers = "content-type;host;x-tc-action"
        hashed_payload = hashlib.sha256(body).hexdigest()
        canonical_request = (
            f"POST\n/\n\n{canonical_headers}\n{signed_headers}\n{hashed_payload}"
        )

        # 2) StringToSign
        credential_scope = f"{date}/{service}/tc3_request"
        hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        string_to_sign = (
            f"TC3-HMAC-SHA256\n{timestamp}\n{credential_scope}\n{hashed_canonical}"
        )

        # 3) 派生密钥 + 签名
        secret = ("TC3" + secret_key).encode("utf-8")
        k_date = self._tc3_hmac(secret, date)
        k_service = self._tc3_hmac(k_date, service)
        k_signing = self._tc3_hmac(k_service, "tc3_request")
        signature = hmac.new(
            k_signing, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        authorization = (
            f"TC3-HMAC-SHA256 Credential={secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        req = urllib.request.Request(
            f"https://{host}/",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Host": host,
                "X-TC-Action": self._ACTION,
                "X-TC-Timestamp": str(timestamp),
                "X-TC-Version": self._VERSION,
                "X-TC-Region": region,
                "Authorization": authorization,
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            logger.error("[sms:tencent] 请求腾讯云短信失败: %s", exc)
            raise RuntimeError(f"腾讯云短信通道网络错误: {exc}") from exc

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("[sms:tencent] 腾讯云短信返回非 JSON: %r", raw[:300])
            raise RuntimeError(f"腾讯云短信返回异常: {raw[:200]}") from exc

        err = (data.get("Response") or {}).get("Error") or {}
        if err:
            code, msg = err.get("Code", ""), err.get("Message", "")
            logger.error("[sms:tencent] 发送失败: %s %s", code, msg)
            raise RuntimeError(f"腾讯云短信发送失败: {code} {msg}".strip())

    # ------------------------------------------------------------ 协议实现
    def send_code(self, phone: str) -> bool:
        """生成随机 6 位验证码，本地存储 + 腾讯云真实下发。"""
        code = f"{random.randint(0, 999999):0{CODE_LENGTH}d}"
        expiry = time.time() + CODE_TTL_SEC
        with self._lock:
            self._codes[phone] = (code, expiry)

        try:
            self._send_sms(phone, code)
        except Exception:
            # 发送失败时清理本地验证码，避免留下无法送达的"幽灵码"
            with self._lock:
                self._codes.pop(phone, None)
            raise

        logger.info("[sms:tencent] 向 %s 下发验证码 (有效期 %ds)", phone, CODE_TTL_SEC)
        return True

    def verify_code(self, phone: str, code: str) -> bool:
        """校验验证码：检查存在、过期、匹配；通过后立即删除。"""
        with self._lock:
            stored = self._codes.get(phone)
            if stored is None:
                logger.info("[sms:tencent] %s 无已发验证码 -> 不通过", phone)
                return False

            stored_code, expiry = stored
            if time.time() > expiry:
                del self._codes[phone]
                logger.info("[sms:tencent] %s 验证码已过期 -> 不通过", phone)
                return False

            passed = (code or "").strip() == stored_code
            if passed:
                del self._codes[phone]  # 一次性使用
            logger.info("[sms:tencent] 校验 %s 的验证码 -> %s", phone, "通过" if passed else "不通过")
            return passed


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
    elif provider == PROVIDER_TENCENT:
        _provider_instance = TencentSmsProvider()
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
    "TencentSmsProvider",
    "get_provider",
    "send_code",
    "verify_code",
    "get_last_sent_code",
    "is_valid_phone",
    "PHONE_PATTERN",
    "CODE_LENGTH",
    "CODE_TTL_SEC",
    "PROVIDER_MOCK",
    "PROVIDER_TENCENT",
]
