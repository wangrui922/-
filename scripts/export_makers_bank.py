"""把题库导出成 Makers 云函数可用的自包含 Python 模块（bank_data.py）。

为什么这么做
----------
Makers 的 **Python 云函数没有持久化数据库**（Blob 存储 SDK 仅 Node 可用，
KV 仅 Edge Functions 可用）。但抽题/判题是「只读题库」场景，最干净的 serverless
做法是：**冷启动时把整张 L1 安全池读进内存**，后续请求全部走内存，零外部依赖。

本脚本把 ``backend/dev.db`` 里所有「active 且通过 L1 安全池口径」的题，连同
知识点树、学段区间常量化成 ``cloud-functions/api/bank_data.py``。

⚠️ 关键：L1 安全池的**唯一真相源**就是 ``app.services.judge_engine.is_l1_safe``
（详见该模块 docstring）。本脚本直接 import 它来过滤，保证导出的题与本地后端
抽到的题**逐题一致**，不会出现「本地能抽、上云抽不到」的口径漂移。

产物（bank_data.py）
------------------
- ``QUESTIONS``：list[dict]，每题含 id/type/content/options/correct_answer/
  analysis/difficulty/grade/edition/section/kp_id；
- ``KP_NODES``：list[dict]{id,name,parent_id,sort}，供知识树 rollup/subtree；
- ``GRADE_BANDS`` / ``BAND_GRADE_RANGE`` / ``EDITION_VALUES`` / ``SECTION_VALUES``
  小常量，与 ``app.models.content`` 保持一致。

用法
----
    python scripts/export_makers_bank.py
默认读 ``backend/dev.db``，写到 ``cloud-functions/api/bank_data.py``。
可用环境变量 ``DEV_DB`` / ``OUT`` 覆盖路径。

⚠️ 红线：本脚本只**读取** dev.db，绝不修改。导出是幂等的（每次全量重写）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ---- 把 backend 挂到 sys.path，复用 is_l1_safe 这唯一真相源 --------------
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "..", "backend")
sys.path.insert(0, os.path.abspath(BACKEND))

from app.services.judge_engine import is_l1_safe  # noqa: E402

DEV_DB = os.environ.get("DEV_DB", os.path.join(BACKEND, "dev.db"))
OUT = os.environ.get("OUT", os.path.join(HERE, "..", "cloud-functions", "api", "bank_data.py"))


def _load_json(value: Any) -> Any:
    """题库 options 列是 JSON 文本，解析成 Python 对象；失败返回原值。"""
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def _norm_id(raw: Any) -> Optional[str]:
    """UUID → str；其它原样转 str 或 None。"""
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def export() -> Dict[str, int]:
    """读 dev.db，写出 bank_data.py，返回统计。"""
    if not os.path.exists(DEV_DB):
        raise SystemExit(f"找不到数据库：{DEV_DB}")

    conn = sqlite3.connect(DEV_DB)
    conn.row_factory = sqlite3.Row
    try:
        # 先做 schema 自检，避免列名漂移导致静默导出空数据
        cols_q = {r["name"] for r in conn.execute("PRAGMA table_info(questions)")}
        cols_a = {r["name"] for r in conn.execute("PRAGMA table_info(answers)")}
        needed_q = {"id", "type", "content", "options", "difficulty", "grade",
                    "edition", "section", "kp_id", "status", "kp_verified"}
        needed_a = {"question_id", "correct_answer", "analysis"}
        missing = (needed_q - cols_q) | (needed_a - cols_a)
        if missing:
            raise SystemExit(f"dev.db schema 缺列：{missing}，请检查数据库版本")

        # active + 有标准答案的题，左接 answers
        # ⚠️ 原始列名是 `type`（SQLAlchemy 侧属性名才是 type_）
        rows = conn.execute(
            """
            SELECT q.id, q.type, q.content, q.options, q.difficulty, q.grade,
                   q.edition, q.section, q.kp_id, q.kp_verified,
                   a.correct_answer, a.analysis
            FROM questions q
            JOIN answers a ON a.question_id = q.id
            WHERE q.status = 'active'
              AND a.correct_answer IS NOT NULL
              AND a.correct_answer != ''
            """
        ).fetchall()

        questions: List[Dict[str, Any]] = []
        skipped_unsafe = 0
        for r in rows:
            options = _load_json(r["options"])
            answer = (r["correct_answer"] or "").strip()
            # ⚠️ 唯一真相源：与本地后端抽题池口径一致
            if not is_l1_safe(r["type"], options, answer):
                skipped_unsafe += 1
                continue
            questions.append({
                "id": _norm_id(r["id"]),
                "type": r["type"] or "",
                "content": r["content"] or "",
                "options": options,
                "correct_answer": answer,
                "analysis": (r["analysis"] or "") if r["analysis"] is not None else "",
                "difficulty": r["difficulty"],
                "grade": r["grade"],
                "edition": r["edition"],
                "section": r["section"],
                "kp_id": _norm_id(r["kp_id"]),
                "kp_verified": bool(r["kp_verified"]),
            })

        # 知识点树（供 rollup / subtree）
        kp_rows = conn.execute(
            "SELECT id, name, parent_id, sort FROM knowledge_points"
        ).fetchall()
        kp_nodes: List[Dict[str, Any]] = [
            {
                "id": _norm_id(k["id"]),
                "name": (k["name"] or "").strip(),
                "parent_id": _norm_id(k["parent_id"]),
                "sort": int(k["sort"] or 0),
            }
            for k in kp_rows
        ]
    finally:
        conn.close()

    # ---- 写 bank_data.py ----------------------------------------------------
    out_dir = os.path.dirname(OUT)
    os.makedirs(out_dir, exist_ok=True)

    # 用 repr 生成合法 Python 字面量；options 可能是嵌套结构，repr 安全
    questions_repr = "[\n" + ",\n".join("    " + _q_repr(q) for q in questions) + "\n]"
    kp_repr = "[\n" + ",\n".join("    " + _kp_repr(k) for k in kp_nodes) + "\n]"

    header = (
        "# ⚠️ AUTO-GENERATED by scripts/export_makers_bank.py — DO NOT EDIT BY HAND.\n"
        f"# 生成时间：{datetime.now(timezone.utc).isoformat()}\n"
        "# 来源：backend/dev.db（仅 active 且通过 L1 安全池口径的题）。\n"
        "# 重新生成：python scripts/export_makers_bank.py\n"
        "# 注意：修改题库请改 dev.db 后重跑本脚本，不要手改本文件。\n\n"
    )

    body = (
        f"QUESTIONS: list[dict] = {questions_repr}\n\n"
        f"KP_NODES: list[dict] = {kp_repr}\n\n"
        "GRADE_BANDS: tuple[str, ...] = (\"小学\", \"初中\", \"高中\")\n"
        "BAND_GRADE_RANGE: dict[str, tuple[int, int]] = {\n"
        "    \"小学\": (1, 6),\n"
        "    \"初中\": (7, 9),\n"
        "    \"高中\": (10, 12),\n"
        "}\n"
        "EDITION_VALUES: tuple[str, ...] = (\"青岛版\", \"苏教版\")\n"
        "SECTION_VALUES: tuple[str, ...] = (\"温故\", \"预习\", \"提优\", \"摸底\", \"专项\", \"自测\")\n"
    )

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(header)
        f.write("from __future__ import annotations\n\n")
        f.write("from typing import Any, Dict, List, Optional\n\n")
        f.write(body)

    return {
        "total_active_with_answer": len(rows),
        "exported_l1_safe": len(questions),
        "skipped_unsafe": skipped_unsafe,
        "kp_nodes": len(kp_nodes),
        "bytes": os.path.getsize(OUT),
    }


def _q_repr(q: Dict[str, Any]) -> str:
    """把一道题渲染成紧凑但合法的 Python dict 字面量。"""
    return (
        "{" + ", ".join(f"{k!r}: {v!r}" for k, v in q.items()) + "}"
    )


def _kp_repr(k: Dict[str, Any]) -> str:
    return "{" + ", ".join(f"{key!r}: {val!r}" for key, val in k.items()) + "}"


if __name__ == "__main__":
    stats = export()
    print("[export_makers_bank] 完成：")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"  输出：{os.path.abspath(OUT)}")
