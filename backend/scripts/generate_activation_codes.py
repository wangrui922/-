"""生成邀请制试运营激活码并写入 CSV。

示例：
    python backend/scripts/generate_activation_codes.py --count 80 --label beta-2026 --out activation-codes.csv

CSV 中的明文激活码只应由项目负责人保存和分发；数据库只保存其摘要。
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.db import SessionLocal
from app.services.auth_service import generate_activation_codes


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 AI 数学教练试运营激活码")
    parser.add_argument("--count", type=int, required=True, help="生成数量，1-200")
    parser.add_argument("--label", default="", help="给这一批码的备注")
    parser.add_argument("--out", required=True, help="CSV 输出路径，不允许覆盖已有文件")
    args = parser.parse_args()

    output_path = Path(args.out).expanduser().resolve()
    if output_path.exists():
        raise SystemExit(f"输出文件已存在，为避免覆盖已停止：{output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    session = SessionLocal()
    try:
        codes = generate_activation_codes(session, args.count, args.label)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["激活码", "备注"])
        writer.writerows((code, args.label) for code in codes)
    print(f"已生成 {len(codes)} 个激活码：{output_path}")


if __name__ == "__main__":
    main()
