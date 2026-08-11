"""把 seed/*.json 灌入题库表（knowledge_points / questions / answers）。

设计要点：
    * 主键用 ``uuid5(NAMESPACE_DNS, 稳定键)`` 派生，跨方言(PG/SQLite)均合法 UUID，
      且同一源数据每次跑都得到相同 id → **幂等**（重跑不重复、不报错）。
    * 知识点 id 由 ``kpCode`` 派生；题目 id 由 ``bankId`` 派生；题目.kp_id 指向知识点 id。
    * 初中题(status=初中)标记为 ``archived``，不进推荐池；小学全量 active。
    * 仅用 ORM（merge 按 PK  upsert），不写任何方言专属 SQL。
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

# 允许以脚本方式直接运行：把 backend/ 加入 sys.path 以便 import app
# 本文件位于 backend/app/scripts/seed_db.py → 上溯 3 级到 backend/
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.db import SessionLocal, engine  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.content import Answer, KnowledgePoint, Question  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

SEED_DIR = BACKEND_DIR / "seed"
NS = uuid.NAMESPACE_DNS


def kp_uuid(code: str) -> str:
    return str(uuid.uuid5(NS, "aicoach:kp:" + code))


def q_uuid(bank_id: str) -> str:
    return str(uuid.uuid5(NS, "aicoach:q:" + bank_id))


def load_json(name: str):
    return json.load(open(SEED_DIR / name, encoding="utf-8"))


def validate_kp_tree(kp_raw: list[dict]) -> None:
    """知识树 fail-fast 校验：任一不过立即抛 ``AssertionError``，绝不静默入库。

    三条不变式（对应 PRD 原则 P2「只做两层」）：
        1. ``parentCode`` 必须存在于本文件的 code 集合中；
        2. 禁自环：``parentCode != code``；
        3. 禁 L3：父节点自身的 ``parentCode`` 必须为 ``None``。

    Args:
        kp_raw: ``knowledge_points.json`` 的 ``knowledgePoints`` 数组。

    Raises:
        AssertionError: 任一校验不通过。
    """
    codes = {kp["code"] for kp in kp_raw}
    parent_of = {kp["code"]: kp["parentCode"] for kp in kp_raw}
    for code, pc in parent_of.items():
        if pc is None:
            continue
        assert pc in codes, f"parentCode 不存在: {code} -> {pc}"
        assert pc != code, f"自环: {code}"
        assert parent_of[pc] is None, f"树深超过 2 层: {code} -> {pc} -> {parent_of[pc]}"


def main() -> None:
    reset = "--reset" in sys.argv
    if reset:
        # 开发库允许整体重建，确保模型演进后 schema 与代码一致（生产请勿使用）
        print("[seed] --reset：drop_all + create_all 重建 schema")
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)

    kp_raw = load_json("knowledge_points.json")["knowledgePoints"]
    q_raw = load_json("questions.json")["questions"]

    validate_kp_tree(kp_raw)

    with SessionLocal() as s:
        # 1) 知识点：两趟写入（先 L1 后 L2），保证写 L2 时父行已在库、外键成立
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
        kmap = {kp["code"]: kp_uuid(kp["code"]) for kp in kp_raw}

        # 2) 题目 + 答案
        n_q = 0
        for q in q_raw:
            qid = q_uuid(q["bankId"])
            status = "archived" if q.get("stage") == "初中" else "active"
            s.merge(
                Question(
                    id=qid,
                    content=q["content"],
                    subject="数学",
                    grade=q.get("grade"),
                    difficulty=q.get("difficulty"),
                    kp_id=kmap.get(q.get("kpCode")),
                    type_=q.get("type"),
                    options=q.get("options"),
                    status=status,
                )
            )
            s.merge(
                Answer(
                    question_id=qid,
                    correct_answer=q.get("answer"),
                    analysis=q.get("ability") or q.get("errorType"),
                )
            )
            n_q += 1
        s.commit()

        # 2.5) 知识树索引缓存失效（同进程内立即按新树 rollup；跨进程靠行数校验自愈）
        from app.services.kp_tree import invalidate as _invalidate_kp_tree  # noqa: E402

        _invalidate_kp_tree()

        # 3) 校验
        total = s.execute(select(func.count()).select_from(Question)).scalar()
        active = s.execute(
            select(func.count()).select_from(Question).where(Question.status == "active")
        ).scalar()
        archived = s.execute(
            select(func.count()).select_from(Question).where(Question.status == "archived")
        ).scalar()
        ans = s.execute(select(func.count()).select_from(Answer)).scalar()
        kpc = s.execute(select(func.count()).select_from(KnowledgePoint)).scalar()
        kp_l2 = s.execute(
            select(func.count()).select_from(KnowledgePoint).where(KnowledgePoint.parent_id.isnot(None))
        ).scalar()
        print(f"[seed] 知识点={kpc} (L1={(kpc or 0) - (kp_l2 or 0)} / L2={kp_l2})  题目(upsert)={n_q}")
        print(f"[seed] questions 总={total}  active(小学)={active}  archived(初中)={archived}  answers={ans}")


if __name__ == "__main__":
    main()
