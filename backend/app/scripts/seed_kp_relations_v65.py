"""把 ``seed/knowledge_relations_v65.json`` 幂等灌入 ``knowledge_relations``（KG-002 关系边）。

设计要点（对齐 KG-001 seed_kp_v65.py 的既有链路）：
    * **主键派生必须保持确定性**：
      ``rel_uuid(source_code, relation_type, target_code)
       = uuid5(NS_QUESTION_BANK, "rel:" + source_code + ":" + relation_type + ":" + target_code)``
      与 ``kp_uuid`` 共用 ``NS_QUESTION_BANK`` 命名空间，前缀 ``rel:`` 避免碰撞；
      任何字符改动都会改变 id，破坏幂等 merge，因此**禁止改动派生公式**。
    * 先确保 ``knowledge_relations`` 表存在（migration 015 / Base.metadata.create_all）。
    * 只处理 V6.5 关系文件内的边；source/target 的 code 必须存在于
      ``knowledge_points``（FK 约束前置），若缺失则报错拒绝灌入，不静默跳过。
    * 仅用 ORM（按 PK merge upsert），不写方言专属 SQL。

用法：
    python app/scripts/seed_kp_relations_v65.py
    （成功输出：关系边总行数 / 本次 seed 行数 / 按类型统计）
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
SEED_DIR = BACKEND_DIR / "seed"
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import func, select  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.content import (
    KNOWLEDGE_RELATION_VALUES,
    KnowledgePoint,
    KnowledgeRelation,
)  # noqa: E402

#: 与 scripts/seed_db.py / seed_kp_v65.py 的 NS_QUESTION_BANK 完全一致（typedef 勿改）
NS_QUESTION_BANK: uuid.UUID = uuid.UUID("6f1b1a1e-6b3f-5c2b-9a44-0d5f1c0a7e21")


def kp_uuid(code: str) -> uuid.UUID:
    """知识点主键（与 KG-001 完全一致，禁止改动）。"""
    return uuid.uuid5(NS_QUESTION_BANK, f"kp:{code}")


def rel_uuid(source_code: str, relation_type: str, target_code: str) -> uuid.UUID:
    """关系边主键（确定性派生，禁止改动公式）。"""
    return uuid.uuid5(
        NS_QUESTION_BANK,
        f"rel:{source_code}:{relation_type}:{target_code}",
    )


def load_relations() -> list[dict]:
    payload = json.loads(
        (SEED_DIR / "knowledge_relations_v65.json").read_text(encoding="utf-8")
    )
    return payload["knowledgeRelations"]


def main() -> None:
    # 1) 保证关系表存在（migration 015 / create_all 兜底，幂等）
    Base.metadata.create_all(engine, tables=[KnowledgeRelation.__table__])

    edges = load_relations()
    if not edges:
        print("[seed-kp-relations-v65] 种子文件为空，退出")
        return

    # 2) 前置校验：source/target code 必须在 knowledge_points 内（FK 护栏）
    with SessionLocal() as s:
        known = set(
            s.execute(
                select(KnowledgePoint.code).where(KnowledgePoint.code.isnot(None))
            )
            .scalars()
            .all()
        )
        bad: list[str] = []
        for e in edges:
            assert e["relationType"] in KNOWLEDGE_RELATION_VALUES, (
                f"非法关系类型 {e['relationType']!r}（{e['sourceCode']}->{e['targetCode']}）"
            )
            if e["sourceCode"] not in known:
                bad.append(f"source={e['sourceCode']}")
            if e["targetCode"] not in known:
                bad.append(f"target={e['targetCode']}")
        if bad:
            raise SystemExit(
                f"[seed-kp-relations-v65] 悬空外键拒绝灌入：{', '.join(sorted(set(bad)))}"
                "（请先灌 knowledge_points：python app/scripts/seed_kp_v65.py）"
            )
        print(f"[seed-kp-relations-v65] 读取种子 {len(edges)} 条，前置 FK 校验通过")

        # 3) merge upsert（确定性主键 → 重复执行不产生重复行）
        for e in edges:
            s.merge(
                KnowledgeRelation(
                    id=rel_uuid(e["sourceCode"], e["relationType"], e["targetCode"]),
                    source_id=kp_uuid(e["sourceCode"]),
                    target_id=kp_uuid(e["targetCode"]),
                    relation_type=e["relationType"],
                )
            )
        s.flush()

        total = s.execute(select(func.count()).select_from(KnowledgeRelation)).scalar()
        by_type = {
            t: s.execute(
                select(func.count())
                .select_from(KnowledgeRelation)
                .where(KnowledgeRelation.relation_type == t)
            ).scalar()
            for t in KNOWLEDGE_RELATION_VALUES
        }
        self_loops = s.execute(
            select(func.count())
            .select_from(KnowledgeRelation)
            .where(KnowledgeRelation.source_id == KnowledgeRelation.target_id)
        ).scalar()
        s.commit()
        detail = {
            "knowledge_relations": total,
            "seeded": len(edges),
            "by_type": by_type,
            "self_loops": self_loops,
        }
        print(f"[seed-kp-relations-v65] OK  {detail}")


if __name__ == "__main__":
    main()
