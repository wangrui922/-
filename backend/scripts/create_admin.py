"""创建 / 重置管理员账号（手机号 + 个人密码）。

密码通过 ``hash_password`` 摘要后写入 ``users.access_pin_hash``，
管理员登录接口（POST /api/auth/admin/login）使用同一字段校验，无需新增表结构。

示例：
    python backend/scripts/create_admin.py --phone 13800000000 --pin 123456 --nickname "管理员"

重复执行是幂等的：已有手机号账号会被提升/重置为管理员角色。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select

from app.core.security import hash_password
from app.db import SessionLocal
from app.models import User


def main() -> None:
    parser = argparse.ArgumentParser(description="创建 / 重置管理员账号")
    parser.add_argument("--phone", required=True, help="管理员手机号")
    parser.add_argument("--pin", required=True, help="个人密码（至少 6 位）")
    parser.add_argument("--nickname", default="管理员", help="昵称")
    args = parser.parse_args()

    phone = (args.phone or "").strip()
    pin = (args.pin or "").strip()
    if len(pin) < 6:
        raise SystemExit("个人密码至少 6 位")

    session = SessionLocal()
    try:
        user = session.execute(select(User).where(User.phone == phone)).scalar_one_or_none()
        if user is None:
            user = User(
                phone=phone,
                nickname=args.nickname.strip() or "管理员",
                role="admin",
                status="active",
                access_pin_hash=hash_password(pin),
            )
            session.add(user)
        else:
            user.role = "admin"
            user.status = "active"
            user.nickname = args.nickname.strip() or user.nickname or "管理员"
            user.access_pin_hash = hash_password(pin)
        session.commit()
        print(f"管理员账号已就绪: phone={phone} nickname={user.nickname}")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()