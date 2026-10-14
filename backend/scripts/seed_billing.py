"""初始化订阅计划数据。

用法：
    cd /home/ubuntu/coach && sudo docker exec deploy-backend-1 python /app/scripts/seed_billing.py
"""

import sys
from pathlib import Path

# 将 /app 加入 path（Docker 容器内路径）
sys.path.insert(0, "/app")

from app.db import SessionLocal
from app.services.billing_service import seed_plans


def main() -> None:
    db = SessionLocal()
    try:
        seed_plans(db)
        db.commit()
        print("[seed_billing] 计划数据初始化完成")
    except Exception as exc:
        db.rollback()
        print(f"[seed_billing] 失败: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
