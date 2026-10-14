"""登录与令牌服务。

流程（``POST /api/auth/login``）：
    1. 校验手机号格式（非法 → 400）；
    2. 限流检查（15min 内登录尝试上限）；
    3. 校验验证码：
       - ``sms.verify_code`` 校验内存中的随机验证码；
       - 校验不通过 → 401；
    4. 首次登录自动建账号：``users(role=student)`` + ``students`` + ``student_profile``；
    5. 签发 access(2h) / refresh(30d)，payload ``{sub: user_id, role, sid: student_id|null}``。

事务：本模块只 ``flush``，``commit`` 交给 ``app.db.get_db`` 依赖收口。

安全改进（P0-3）：
    * 废弃固定万能码 ``000000``，改用随机 6 位验证码（5 分钟过期，一次性）；
    * 新增限流：发码 60s 冷却 + 1h 5 次；登录 15min 10 次。
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import uuid
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.ratelimit import (
    LOGIN_ATTEMPT_MAX,
    LOGIN_ATTEMPT_SEC,
    SEND_CODE_COOLDOWN_SEC,
    SEND_CODE_HOURLY_MAX,
    SEND_CODE_HOURLY_SEC,
    code_send_limiter,
    login_attempt_limiter,
)
from app.core.response import (
    ApiError,
    CODE_BAD_REQUEST,
    CODE_FORBIDDEN,
    CODE_SERVER_ERROR,
    CODE_TOO_MANY_REQUESTS,
    CODE_UNAUTHORIZED,
)
from app.core.security import (
    ROLE_STUDENT,
    TOKEN_TYPE_REFRESH,
    VALID_ROLES,
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_token,
    verify_password,
)
from app.models import ActivationCode, Student, StudentProfile, User
from app.models.base import utcnow
from app.services import sms

logger = logging.getLogger("app.services.auth")

#: 新学生默认年级（老板拍板：注册不弹年级选择，进档案页再改）
DEFAULT_GRADE: int = 1
#: 新学生默认学段
DEFAULT_STAGE: str = "primary"
#: 账号状态
USER_STATUS_ACTIVE: str = "active"
USER_STATUS_FROZEN: str = "frozen"
ACTIVATION_CODE_PREFIX: str = "YY"
ACTIVATION_CODE_GROUPS: int = 4
ACTIVATION_CODE_GROUP_SIZE: int = 4
ACTIVATION_ALPHABET: str = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# ==================================================================== 验证码
def verify_login_code(phone: str, code: str) -> None:
    """校验登录验证码，不通过直接抛 ``ApiError``。

    Args:
        phone: 手机号（已保证格式合法）。
        code: 用户提交的验证码。

    Raises:
        ApiError: 401 验证码错误；500 短信通道未接入。
    """
    try:
        passed = sms.verify_code(phone, code)
    except NotImplementedError as exc:
        logger.error("[auth] 短信通道未接入: %s", exc)
        raise ApiError(CODE_SERVER_ERROR, "短信验证码服务尚未接入") from exc

    if not passed:
        raise ApiError(CODE_UNAUTHORIZED, "验证码错误或已失效")


# ==================================================================== 限流
def _check_send_rate(phone: str) -> None:
    """发码限流：60s 冷却 + 1h 5 次。超限抛 429。"""
    if not code_send_limiter.check(f"cooldown:{phone}", 1, SEND_CODE_COOLDOWN_SEC):
        raise ApiError(CODE_TOO_MANY_REQUESTS, f"操作过于频繁，请 {SEND_CODE_COOLDOWN_SEC} 秒后再试")
    if not code_send_limiter.check(f"hourly:{phone}", SEND_CODE_HOURLY_MAX, SEND_CODE_HOURLY_SEC):
        raise ApiError(CODE_TOO_MANY_REQUESTS, "验证码请求次数过多，请稍后再试")


def _check_login_rate(phone: str) -> None:
    """登录限流：15min 10 次。超限抛 429。"""
    if not login_attempt_limiter.check(f"login:{phone}", LOGIN_ATTEMPT_MAX, LOGIN_ATTEMPT_SEC):
        raise ApiError(CODE_TOO_MANY_REQUESTS, "登录尝试过于频繁，请 15 分钟后再试")


# ==================================================================== 账号
def get_user_by_phone(session: Session, phone: str) -> Optional[User]:
    """按手机号查未软删的账号。"""
    return session.execute(
        select(User).where(User.phone == phone, User.deleted_at.is_(None))
    ).scalar_one_or_none()


def get_student_by_user_id(session: Session, user_id: uuid.UUID) -> Optional[Student]:
    """按账号 id 查学生档案。"""
    return session.execute(
        select(Student).where(Student.user_id == user_id)
    ).scalar_one_or_none()


def ensure_student_profile(session: Session, student: Student) -> StudentProfile:
    """保证学生画像行存在（幂等）。"""
    profile = session.execute(
        select(StudentProfile).where(StudentProfile.student_id == student.id)
    ).scalar_one_or_none()
    if profile is not None:
        return profile

    profile = StudentProfile(
        student_id=student.id,
        mastery={},
        weak_points=[],
        learning_style={},
    )
    session.add(profile)
    session.flush()
    return profile


def register_student(session: Session, phone: str) -> Tuple[User, Student]:
    """首次登录自动建号：user(role=student) + student + student_profile。

    Args:
        session: 数据库会话。
        phone: 手机号（唯一）。

    Returns:
        ``(User, Student)`` 元组。
    """
    user = User(
        phone=phone,
        nickname=f"同学{phone[-4:]}",
        role=ROLE_STUDENT,
        status=USER_STATUS_ACTIVE,
    )
    session.add(user)
    session.flush()  # 拿到 user.id

    student = Student(
        user_id=user.id,
        grade=DEFAULT_GRADE,
        stage=DEFAULT_STAGE,
        goal=None,
    )
    session.add(student)
    session.flush()  # 拿到 student.id

    ensure_student_profile(session, student)
    logger.info("[auth] 新用户注册: phone=%s user_id=%s student_id=%s", phone, user.id, student.id)
    return user, student


def get_or_create_user(session: Session, phone: str) -> Tuple[User, Optional[Student], bool]:
    """取账号；不存在则按学生角色自动注册。

    Returns:
        ``(User, Student|None, is_new)``；家长/管理员角色没有 student 行。
    """
    user = get_user_by_phone(session, phone)
    if user is None:
        user, student = register_student(session, phone)
        return user, student, True

    if user.status == USER_STATUS_FROZEN:
        raise ApiError(CODE_FORBIDDEN, "账号已被冻结，请联系管理员")

    student = get_student_by_user_id(session, user.id) if user.role == ROLE_STUDENT else None
    if user.role == ROLE_STUDENT and student is None:
        # 兜底：历史脏数据（有 user 无 student）自动补齐
        student = Student(user_id=user.id, grade=DEFAULT_GRADE, stage=DEFAULT_STAGE)
        session.add(student)
        session.flush()
        ensure_student_profile(session, student)
    return user, student, False


# ==================================================================== 令牌
def build_token_payload(user: User, student: Optional[Student]) -> Dict[str, Any]:
    """按硬约定签发 access + refresh，并回传对外的 camelCase 载荷。

    返回体同时提供 ``access``/``refresh``（T02 契约字段）与
    ``accessToken``/``refreshToken``（T01 ``create_token_pair`` 约定字段），
    两套键指向同一对令牌，方便前端任一写法都能跑通。
    """
    sid = str(student.id) if student is not None else None
    access = create_access_token(sub=user.id, role=user.role, sid=sid)
    refresh = create_refresh_token(sub=user.id, role=user.role, sid=sid)
    return {
        "access": access,
        "refresh": refresh,
        "accessToken": access,
        "refreshToken": refresh,
        "tokenType": "Bearer",
        "expiresIn": settings.access_token_expire_minutes * 60,
    }


def build_user_brief(user: User, student: Optional[Student]) -> Dict[str, Any]:
    """登录返回的用户摘要（camelCase）。"""
    return {
        "userId": str(user.id),
        "studentId": str(student.id) if student is not None else None,
        "phone": None if user.phone.startswith("invite-") else user.phone,
        "nickname": user.nickname,
        "avatarUrl": user.avatar_url,
        "role": user.role,
        "grade": student.grade if student is not None else None,
        "stage": student.stage if student is not None else None,
    }


# ==================================================================== 用例
def normalize_activation_code(code: str) -> str:
    """统一激活码格式，兼容学生输入空格或省略横线。"""
    compact = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
    expected_length = len(ACTIVATION_CODE_PREFIX) + ACTIVATION_CODE_GROUPS * ACTIVATION_CODE_GROUP_SIZE
    if len(compact) != expected_length or not compact.startswith(ACTIVATION_CODE_PREFIX):
        raise ApiError(CODE_BAD_REQUEST, "激活码格式不正确，请检查后再试")
    return "-".join(
        [compact[: len(ACTIVATION_CODE_PREFIX)]]
        + [
            compact[offset : offset + ACTIVATION_CODE_GROUP_SIZE]
            for offset in range(len(ACTIVATION_CODE_PREFIX), expected_length, ACTIVATION_CODE_GROUP_SIZE)
        ]
    )


def _activation_digest(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _validate_activation_pin(pin: str) -> str:
    normalized = (pin or "").strip()
    if not re.fullmatch(r"\d{6}", normalized):
        raise ApiError(CODE_BAD_REQUEST, "个人密码请设置为 6 位数字")
    return normalized


def _new_invite_login_identifier() -> str:
    """为无手机号的邀请制学生创建内部标识，不向客户端回显。"""
    return f"invite-{secrets.token_hex(6)}"


def _find_activation_code(session: Session, code: str) -> ActivationCode:
    normalized = normalize_activation_code(code)
    activation = session.execute(
        select(ActivationCode).where(ActivationCode.code_digest == _activation_digest(normalized))
    ).scalar_one_or_none()
    if activation is None or activation.revoked_at is not None:
        raise ApiError(CODE_UNAUTHORIZED, "激活码不正确或已失效")
    if activation.expires_at is not None and activation.expires_at < utcnow():
        raise ApiError(CODE_UNAUTHORIZED, "激活码已过期，请联系老师")
    return activation


def _register_invite_student(session: Session, nickname: str, pin: str) -> tuple[User, Student]:
    name = (nickname or "").strip()
    if not name:
        raise ApiError(CODE_BAD_REQUEST, "第一次使用，请先写下你的称呼")
    user = User(
        phone=_new_invite_login_identifier(),
        nickname=name,
        access_pin_hash=hash_password(pin),
        role=ROLE_STUDENT,
        status=USER_STATUS_ACTIVE,
    )
    session.add(user)
    session.flush()
    student = Student(user_id=user.id, grade=DEFAULT_GRADE, stage=DEFAULT_STAGE)
    session.add(student)
    session.flush()
    ensure_student_profile(session, student)
    return user, student


def enter_by_activation_code(session: Session, code: str, pin: str, nickname: Optional[str] = None) -> Dict[str, Any]:
    """首次激活建号；已绑定激活码则用个人密码登录同一学生账号。"""
    activation = _find_activation_code(session, code)
    normalized_pin = _validate_activation_pin(pin)
    is_new = activation.user_id is None

    if is_new:
        user, student = _register_invite_student(session, nickname or "", normalized_pin)
        activation.user_id = user.id
        activation.activated_at = utcnow()
    else:
        user = session.get(User, activation.user_id)
        if user is None or user.deleted_at is not None:
            raise ApiError(CODE_UNAUTHORIZED, "激活码关联的账号不存在，请联系老师")
        if user.status == USER_STATUS_FROZEN:
            raise ApiError(CODE_FORBIDDEN, "账号已被冻结，请联系老师")
        if not user.access_pin_hash or not verify_password(normalized_pin, user.access_pin_hash):
            raise ApiError(CODE_UNAUTHORIZED, "个人密码不正确，请再试一次")
        student = get_student_by_user_id(session, user.id)
        if student is None:
            raise ApiError(CODE_SERVER_ERROR, "学生档案不完整，请联系老师")

    user.last_login_at = utcnow()
    session.flush()
    result = build_token_payload(user, student)
    result["isNewUser"] = is_new
    result["user"] = build_user_brief(user, student)
    return result


def generate_activation_codes(session: Session, count: int, label: Optional[str] = None) -> list[str]:
    """为邀请制试运营生成未绑定激活码，调用方负责把明文交给学生。"""
    if count < 1 or count > 200:
        raise ValueError("count must be between 1 and 200")
    generated: list[str] = []
    while len(generated) < count:
        groups = ["".join(secrets.choice(ACTIVATION_ALPHABET) for _ in range(ACTIVATION_CODE_GROUP_SIZE)) for _ in range(ACTIVATION_CODE_GROUPS)]
        code = f"{ACTIVATION_CODE_PREFIX}-" + "-".join(groups)
        exists = session.execute(
            select(ActivationCode.id).where(ActivationCode.code_digest == _activation_digest(code))
        ).scalar_one_or_none()
        if exists is not None:
            continue
        session.add(ActivationCode(code_digest=_activation_digest(code), label=(label or "").strip() or None))
        generated.append(code)
    session.flush()
    return generated


def login_by_code(session: Session, phone: str, code: str) -> Dict[str, Any]:
    """手机号 + 验证码登录（首次登录自动注册）。

    Args:
        session: 数据库会话。
        phone: 手机号。
        code: 验证码（通过 ``/api/auth/code`` 获取）。

    Returns:
        ``{access, refresh, accessToken, refreshToken, tokenType, expiresIn, isNewUser, user}``。

    Raises:
        ApiError: 400 手机号非法；401 验证码错误；403 账号冻结；
                  429 频率超限；500 短信通道未接入。
    """
    normalized_phone = (phone or "").strip()
    if not sms.is_valid_phone(normalized_phone):
        raise ApiError(CODE_BAD_REQUEST, "手机号格式不正确")

    _check_login_rate(normalized_phone)
    verify_login_code(normalized_phone, code)

    user, student, is_new = get_or_create_user(session, normalized_phone)
    user.last_login_at = utcnow()
    session.flush()

    payload = build_token_payload(user, student)
    payload["isNewUser"] = is_new
    payload["user"] = build_user_brief(user, student)
    return payload


def login_admin(session: Session, phone: str, pin: str) -> Dict[str, Any]:
    """管理员登录（手机号 + 个人密码）。

    仅放行 ``role=admin`` 的账号；校验 ``access_pin_hash`` 密码摘要，
    与激活码账号共用同一套密码字段，避免新增表结构。

    Returns:
        与 ``login_by_code`` 同构的令牌载荷（含 ``user`` 摘要）。
    """
    normalized_phone = (phone or "").strip()
    if not sms.is_valid_phone(normalized_phone):
        raise ApiError(CODE_BAD_REQUEST, "手机号格式不正确")

    user = get_user_by_phone(session, normalized_phone)
    if user is None or user.role != "admin":
        raise ApiError(CODE_UNAUTHORIZED, "管理员账号或密码错误")
    if user.status == USER_STATUS_FROZEN:
        raise ApiError(CODE_FORBIDDEN, "账号已被冻结，请联系管理员")
    if not user.access_pin_hash or not verify_password(pin, user.access_pin_hash):
        raise ApiError(CODE_UNAUTHORIZED, "管理员账号或密码错误")

    user.last_login_at = utcnow()
    session.flush()
    payload = build_token_payload(user, None)
    payload["user"] = build_user_brief(user, None)
    return payload


def refresh_tokens(session: Session, refresh_token: str) -> Dict[str, Any]:
    """用 refresh token 换一对新令牌。

    Args:
        session: 数据库会话。
        refresh_token: 30 天有效期的刷新令牌。

    Returns:
        与 ``login_by_code`` 同构的令牌载荷（不含 ``isNewUser``）。

    Raises:
        ApiError: 401 令牌无效/过期或账号不存在；403 账号冻结。
    """
    payload = verify_token(refresh_token, expected_type=TOKEN_TYPE_REFRESH)

    try:
        user_id = uuid.UUID(str(payload.get("sub")))
    except (TypeError, ValueError) as exc:
        raise ApiError(CODE_UNAUTHORIZED, "访问令牌无效") from exc

    user = session.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise ApiError(CODE_UNAUTHORIZED, "账号不存在或已注销")
    if user.status == USER_STATUS_FROZEN:
        raise ApiError(CODE_FORBIDDEN, "账号已被冻结，请联系管理员")
    if user.role not in VALID_ROLES:
        raise ApiError(CODE_UNAUTHORIZED, "账号角色异常")

    student = get_student_by_user_id(session, user.id) if user.role == ROLE_STUDENT else None
    result = build_token_payload(user, student)
    result["user"] = build_user_brief(user, student)
    return result


def send_login_code(phone: str) -> Dict[str, Any]:
    """下发登录验证码。

    限流：60s 冷却 + 1h 5 次。
    dev 环境：通过 ``sms.get_last_sent_code()`` 拿到随机码回传前端，方便自测。
    生产环境：不回传验证码（需接真短信通道）。

    Args:
        phone: 手机号。

    Returns:
        ``{"sent": bool, "devCode": str|None}``；dev 环境回带验证码方便自测。

    Raises:
        ApiError: 400 手机号非法；429 频率超限；500 通道未接入。
    """
    normalized_phone = (phone or "").strip()
    if not sms.is_valid_phone(normalized_phone):
        raise ApiError(CODE_BAD_REQUEST, "手机号格式不正确")

    _check_send_rate(normalized_phone)

    try:
        sent = sms.send_code(normalized_phone)
    except NotImplementedError as exc:
        raise ApiError(CODE_SERVER_ERROR, "短信验证码服务尚未接入") from exc

    # mock 模式下回传验证码，方便测试（真实通道上线后移除）
    dev_code: Optional[str] = None
    if settings.sms_provider == "mock" and sent:
        dev_code = sms.get_last_sent_code(normalized_phone)

    return {
        "sent": bool(sent),
        "devCode": dev_code,
    }


#: 公开注册允许的角色（admin 不开放自助注册）
ALLOWED_REGISTER_ROLES = ("student", "parent")


def register_user(session: Session, phone: str, role: str) -> Tuple[User, Optional[Student]]:
    """按指定角色建账号（不自动登录）。

    student 角色会一并创建 ``students`` + ``student_profile``；
    parent / admin 角色仅有 ``users`` 行，无 student 档案。
    """
    user = User(
        phone=phone,
        nickname=f"用户{phone[-4:]}",
        role=role,
        status=USER_STATUS_ACTIVE,
    )
    session.add(user)
    session.flush()  # 拿到 user.id

    student: Optional[Student] = None
    if role == ROLE_STUDENT:
        student = Student(user_id=user.id, grade=DEFAULT_GRADE, stage=DEFAULT_STAGE)
        session.add(student)
        session.flush()
        ensure_student_profile(session, student)
    return user, student


def register_user_by_code(session: Session, phone: str, code: str, role: str) -> Dict[str, Any]:
    """手机号 + 验证码注册（指定角色）。

    已存在该手机号的账号则直接返回其令牌（不重复建号）；不存在则按角色新建。
    仅允许 ``student`` / ``parent`` 角色自助注册。

    Returns:
        与 ``login_by_code`` 同构的令牌载荷（含 ``isNewUser`` / ``user``）。
    """
    normalized_phone = (phone or "").strip()
    if not sms.is_valid_phone(normalized_phone):
        raise ApiError(CODE_BAD_REQUEST, "手机号格式不正确")
    if role not in ALLOWED_REGISTER_ROLES:
        raise ApiError(CODE_BAD_REQUEST, "角色非法，仅支持 student / parent 注册")
    _check_login_rate(normalized_phone)
    verify_login_code(normalized_phone, code)

    user = get_user_by_phone(session, normalized_phone)
    if user is not None:
        if user.status == USER_STATUS_FROZEN:
            raise ApiError(CODE_FORBIDDEN, "账号已被冻结，请联系管理员")
        student = get_student_by_user_id(session, user.id) if user.role == ROLE_STUDENT else None
        user.last_login_at = utcnow()
        session.flush()
        payload = build_token_payload(user, student)
        payload["isNewUser"] = False
        payload["user"] = build_user_brief(user, student)
        return payload

    user, student = register_user(session, normalized_phone, role)
    user.last_login_at = utcnow()
    session.flush()
    payload = build_token_payload(user, student)
    payload["isNewUser"] = True
    payload["user"] = build_user_brief(user, student)
    return payload


__all__ = [
    "DEFAULT_GRADE",
    "DEFAULT_STAGE",
    "login_by_code",
    "login_admin",
    "refresh_tokens",
    "send_login_code",
    "verify_login_code",
    "get_user_by_phone",
    "get_student_by_user_id",
    "get_or_create_user",
    "register_student",
    "ensure_student_profile",
    "build_token_payload",
    "build_user_brief",
]
