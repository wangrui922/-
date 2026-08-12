"""把 ``seed/math_kp_highschool.json`` 灌入 ``knowledge_points``（仅高中分支）。

设计要点（对齐 ``seed_db.py``）：
    * 主键用 ``uuid5(NAMESPACE_DNS, 稳定键)`` 派生 → **幂等**（重跑不重复、不报错）。
    * 沿用 ``KnowledgePoint`` 现有 2 层树约束（章节→知识点，禁 L3、禁自环、parentCode 必存在）。
    * 只 upsert 高中 KP，**不碰** 现有小学/初中 KP 与 909 道 seed 题。
    * 仅用 ORM（merge 按 PK upsert），不写任何方言专属 SQL。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

# 本文件位于 backend/app/scripts/seed_kp_hs.py → 上溯 3 级到 backend/
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.db import SessionLocal, engine  # noqa: E402
from app.models.content import KnowledgePoint  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

SEED_DIR = BACKEND_DIR / "seed"
NS = uuid.NAMESPACE_DNS


def kp_uuid(code: str) -> str:
    return str(uuid.uuid5(NS, "aicoach:kp:" + code))


def load_json(name: str):
    return json.load(open(SEED_DIR / name, encoding="utf-8"))


def validate(kp_raw: list[dict]) -> None:
    """高中知识树 fail-fast 校验：parentCode 存在 / 禁自环 / 禁 L3。"""
    codes = {kp["code"] for kp in kp_raw}
    parent_of = {kp["code"]: kp["parentCode"] for kp in kp_raw}
    for code, pc in parent_of.items():
        if pc is None:
            continue
        assert pc in codes, f"parentCode 不存在: {code} -> {pc}"
        assert pc != code, f"自环: {code}"
        assert parent_of[pc] is None, f"树深超过 2 层: {code} -> {pc} -> {parent_of[pc]}"


def main() -> None:
    kp_raw = load_json("math_kp_highschool.json")["knowledgePoints"]
    validate(kp_raw)

    with SessionLocal() as s:
        # 两趟写入（先 L1 后 L2），保证写 L2 时父行已存在、外键成立
        for is_l1_pass in (True, False):
            for kp in kp_raw:
                if (kp["parentCode"] is None) != is_l1_pass:
                    continue
                s.merge(
                    KnowledgePoint(
                        id=kp_uuid(kp["code"]),
                        code=kp["code"],
                        name=kp["name"],
                        description=kp.get("description"),
                        sort=kp.get("sort", 0),
                        parent_id=kp_uuid(kp["parentCode"]) if kp["parentCode"] else None,
                    )
                )
            s.flush()

        total = s.execute(select(func.count()).select_from(KnowledgePoint)).scalar()
        hs = s.execute(
            select(func.count()).select_from(KnowledgePoint).where(KnowledgePoint.code.like("HS_%"))
        ).scalar()
        hs_l2 = s.execute(
            select(func.count())
            .select_from(KnowledgePoint)
            .where(KnowledgePoint.code.like("HS_%"), KnowledgePoint.parent_id.isnot(None))
        ).scalar()
        s.commit()
        print(f"[seed-kp-hs] knowledge_points 总={total}  高中(HS)={hs} (L1={(hs or 0)-(hs_l2 or 0)} / L2={hs_l2})")


if __name__ == "__main__":
    main()
