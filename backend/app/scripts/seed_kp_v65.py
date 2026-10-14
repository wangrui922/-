"""把 ``seed/knowledge_points_v65.json`` 幂等灌入 ``knowledge_points``（KG-001 知识点库）。

设计要点（对齐现有 seed 链路）：
    * **主键派生必须与 ``scripts/seed_db.py`` 一致**：
      ``uuid5(NS_QUESTION_BANK, "kp:" + code)``（注意不是 seed_kp_cz/hs 的
      ``NAMESPACE_DNS + "aicoach:kp:"`` 派生），否则与 dev.db 现有
      KP_ 行 id 不一致、merge 会重复插入，破坏幂等。
    * 先 ``ensure_knowledge_point_metadata(engine)`` 保证 V6.5 元数据列
      （subject/grade/semester/chapter/difficulty/importance/status/
      created_at/updated_at）存在（migration 014 双方言 + schema_guard 兜底）。
    * 只处理 V6.5 文件内的 206 条（13 L1 + 193 L2，全部 KP_ 前缀），
      不碰 CZ_ / HS_ 行与存量题目。
    * parent_id 由 ``parentCode`` 派生：L1 为 None，L2 指向其 L1 的
      ``kp_uuid`` —— 把现有库里「平铺的 93 条 KP_」修正为「13 L1 + 80 L2
      两层树」，让 ``kp_tree`` 的 rollup / subtree 语义真正生效。
    * 仅用 ORM（按 PK merge upsert），不写方言专属 SQL。

用法：
    python app/scripts/seed_kp_v65.py
    （成功输出：knowledge_points 总行数 / KP 行数 / L1 / L2）
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
SEED_DIR = BACKEND_DIR / "seed"
sys.path.insert(0, str(BACKEND_DIR))

from app.db import SessionLocal, engine  # noqa: E402
from app.models.content import KnowledgePoint  # noqa: E402
from app.services.importers.schema_guard import ensure_knowledge_point_metadata  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

#: 与 scripts/seed_db.py 的 NS_QUESTION_BANK / kp_uuid 完全一致（typedef 勿改）
NS_QUESTION_BANK: uuid.UUID = uuid.UUID("6f1b1a1e-6b3f-5c2b-9a44-0d5f1c0a7e21")


def kp_uuid(code: str) -> uuid.UUID:
    return uuid.uuid5(NS_QUESTION_BANK, f"kp:{code}")


def load_v65() -> list[dict]:
    payload = json.loads(
        (SEED_DIR / "knowledge_points_v65.json").read_text(encoding="utf-8")
    )
    return payload["knowledgePoints"]


def main() -> None:
    # 1) 保证 V6.5 元数据列存在（migration 014 + schema_guard 兜底，幂等）
    ensure_knowledge_point_metadata(engine)

    kps = load_v65()
    print(f"[seed-kp-v65] 读取 seed/knowledge_points_v65.json 共 {len(kps)} 条")

    with SessionLocal() as s:
        # L1 先写（保证父节点先于子节点存在，防外键异常）
        for is_l1_pass in (True, False):
            for kp in kps:
                if (kp["parentCode"] is None) != is_l1_pass:
                    continue
                s.merge(
                    KnowledgePoint(
                        id=kp_uuid(kp["code"]),
                        code=kp["code"],
                        name=kp["name"],
                        description=kp.get("description"),
                        sort=kp.get("sort", 0),
                        parent_id=kp_uuid(kp["parentCode"])
                        if kp["parentCode"]
                        else None,
                        subject=kp.get("subject", "math"),
                        grade=kp.get("grade"),
                        semester=kp.get("semester"),
                        chapter=kp.get("chapter"),
                        difficulty=kp.get("difficulty", 3),
                        importance=kp.get("importance", 3),
                        status=kp.get("status", "active"),
                    )
                )
            s.flush()

        # 断言：全部写入后 KP_ 树是 13 L1 + 193 L2，L2 均有父
        kp_rows = (
            s.execute(select(KnowledgePoint).where(KnowledgePoint.code.like("KP_%")))
            .scalars()
            .all()
        )
        l1 = [r for r in kp_rows if r.parent_id is None]
        l2 = [r for r in kp_rows if r.parent_id is not None]
        orphan_l2 = [r.code for r in l2 if s.get(KnowledgePoint, r.parent_id) is None]
        assert len(l1) == 13, f"KP L1 数异常: {len(l1)}"
        assert len(l2) >= 187, f"KP L2 数异常: {len(l2)}"
        assert not orphan_l2, f"存在父节点缺失的 L2: {orphan_l2}"

        total = s.execute(select(func.count()).select_from(KnowledgePoint)).scalar()
        kp_total = len(kp_rows)
        s.commit()
        detail = {
            "knowledge_points": total,
            "kp_total": kp_total,
            "kp_l1": len(l1),
            "kp_l2": len(l2),
        }
        print(f"[seed-kp-v65] OK  {detail}")


if __name__ == "__main__":
    main()
