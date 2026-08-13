"""导入真题 PDF 整理出的结构化 JSON 入库。

设计要点：
- **幂等**：题目 id 用 uuid5 从 ``source_doc + 题号`` 派生，重复导入覆盖而非重复。
- **双不变式闸门**：选择题过 ``quality_gate.classify_question`` (ABCD卡口) +
  ``judge_engine.is_l1_safe`` (L1安全池) + ``validate_choice_options`` (键位合法)。
  填空/解答只做"非空"最小校验（L1 不支持非选择题，符合现有口径）。
- **溯源护栏**：批次 A(云南中考真题) → ``source_tag=green, ship_ready=1``；
  批次 B(下载站模拟卷) → ``source_tag=green, ship_ready=0``(仅内部金标准)。
- **零迁移**：不动表结构；新列 ``source_tag`` / ``ship_ready`` 由 schema_guard 自愈。

用法：
    python -m app.scripts.import_exam_pdf seed/exam_yunnan_2025.json
    python -m app.scripts.import_exam_pdf seed/exam_national_huixiang.json  # ship_ready=0
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

# 让 `python app/scripts/import_exam_pdf.py` 与 `-m app.scripts.import_exam_pdf` 都可用
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models.content import Answer, KnowledgePoint, Question  # noqa: E402
from app.services.importers.quality_gate import (  # noqa: E402
    classify_question,
    validate_choice_options,
)
from app.services.judge_engine import is_l1_safe  # noqa: E402

# uuid5 命名空间（DNS 风格；固定 UUID 不跨项目变化 → 同题号幂等）
NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def _qid(source_doc: str, no: int) -> uuid.UUID:
    """题目 id 派生：同一 source_doc+题号 → 同一 uuid → upsert 幂等。"""
    return uuid.uuid5(NS, f"exam_pdf:{source_doc}:{no}")


def _resolve_kp(s: Session, kp_code: str | None) -> uuid.UUID | None:
    if not kp_code:
        return None
    kp = s.query(KnowledgePoint).filter(KnowledgePoint.code == kp_code).first()
    return kp.id if kp else None


def _check_choice(stem: str, options: Any, answer_letter: str, *, ctx: str) -> bool:
    """选择题三道闸门（卡口 + L1 安全池 + 键位合法）。"""
    # 闸门 1：质量门 (清白 + ABCD 卡口)
    verdict, reasons = classify_question(stem, None, q_type="选择", options=options)
    if verdict != "clean":
        print(f"  [SKIP] {ctx}  quality_gate verdict={verdict} reasons={reasons}")
        return False
    # 闸门 2：选项与键位合法性
    if not validate_choice_options(options, answer_letter):
        print(f"  [SKIP] {ctx}  validate_choice_options=False options={options} key={answer_letter}")
        return False
    # 闸门 3：L1 安全池（与 judge 行为一致）
    if not is_l1_safe("选择", options, answer_letter):
        print(f"  [SKIP] {ctx}  is_l1_safe=False")
        return False
    return True


def _check_other(stem: str, answer: str, q_type: str, *, ctx: str) -> bool:
    """填空/解答只做最小非空校验（L1 不支持非选择题 → 不入池但入库做展示）。"""
    if not stem or not stem.strip():
        print(f"  [SKIP] {ctx}  {q_type} 题干为空")
        return False
    if not answer or not str(answer).strip():
        print(f"  [SKIP] {ctx}  {q_type} 答案为空")
        return False
    return True


def import_one(s: Session, source_doc: str, ship_ready: bool, default_tag: str, q: dict[str, Any]) -> str:
    """upsert 单题，返回结果状态字符串。"""
    no = q.get("no") or q.get("q_no")
    q_type = q["type"]
    stem = q.get("content") or q.get("stem")
    raw_answer = q.get("answer") or q.get("answer_key") or ""
    kp_code = q.get("kp_code")
    difficulty = q.get("difficulty", 1)
    # difficulty 受 CHECK 约束 (1/3/5)；用户语义 1/2/3 → 1/3/5
    difficulty = {1: 1, 2: 3, 3: 5, 4: 5, 5: 5}.get(difficulty, 1)
    qid = _qid(source_doc, no)

    if q_type == "选择":
        options = q.get("options") or {}
        answer_letter = raw_answer.strip().upper()
        if answer_letter not in {"A", "B", "C", "D"}:
            return f"SKIP(no={no}): 选择答案非 ABCD: {raw_answer!r}"
        if not _check_choice(stem, options, answer_letter, ctx=f"#{no}"):
            return f"SKIP(no={no}): 选择闸门未过"
        correct = answer_letter
        stored_options = options
    elif q_type in ("填空", "解答", "计算", "应用", "证明"):
        if not _check_other(stem, raw_answer, q_type, ctx=f"#{no}"):
            return f"SKIP(no={no}): {q_type} 最小校验失败"
        correct = raw_answer
        stored_options = None
    else:
        return f"SKIP(no={no}): 未知题型 {q_type!r}"

    kp_id = _resolve_kp(s, kp_code)

    existing = s.get(Question, qid)
    if existing:
        existing.content = stem
        existing.subject = "数学"
        existing.grade = 9  # 2025 云南中考为 9 年级
        existing.difficulty = difficulty
        existing.kp_id = kp_id
        existing.kp_verified = True
        existing.source_doc = source_doc
        existing.source_tag = default_tag
        existing.ship_ready = ship_ready
        existing.type_ = q_type
        existing.options = stored_options
        existing.status = "active"
        q_row = existing
    else:
        q_row = Question(
            id=qid,
            content=stem,
            subject="数学",
            grade=9,
            difficulty=difficulty,
            kp_id=kp_id,
            kp_verified=True,
            source_doc=source_doc,
            source_tag=default_tag,
            ship_ready=ship_ready,
            type_=q_type,
            options=stored_options,
            status="active",
        )
        s.add(q_row)

    ans = s.query(Answer).filter(Answer.question_id == qid).first()
    if ans:
        ans.correct_answer = correct
        ans.analysis = q.get("explanation", "")
    else:
        s.add(Answer(
            id=uuid.uuid4(),
            question_id=qid,
            correct_answer=correct,
            analysis=q.get("explanation", ""),
            distractor_analysis={},
        ))
    return f"OK(no={no}, type={q_type})"


def run(json_path: str, *, ship_ready_override: bool | None = None) -> dict[str, int]:
    """主入口；返回统计。"""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    source_doc = data["source_doc"]
    ship_ready = data.get("ship_ready", 1) if ship_ready_override is None else int(ship_ready_override)
    tag = data.get("source_tag", "green")
    qs = data["questions"]

    counts = {"ok": 0, "skip": 0, "errors": 0}
    with SessionLocal() as s:
        for q in qs:
            status = import_one(s, source_doc, bool(ship_ready), tag, q)
            if status.startswith("OK"):
                counts["ok"] += 1
            else:
                counts["skip"] += 1
                print(f"  [import-exam-pdf] {status}", flush=True)
        s.commit()
    counts["total"] = len(qs)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="导入真题 PDF 整理出的结构化 JSON 入库")
    parser.add_argument("json_path", help="seed/exam_*.json 路径")
    parser.add_argument(
        "--ship-ready",
        choices=["0", "1"],
        help="覆盖 JSON 里的 ship_ready（仅内部金标准时设 0）",
    )
    args = parser.parse_args()
    if not os.path.isfile(args.json_path):
        print(f"[err] 文件不存在: {args.json_path}")
        sys.exit(1)
    override = int(args.ship_ready) if args.ship_ready is not None else None
    counts = run(args.json_path, ship_ready_override=override)
    print(
        f"[import-exam-pdf] {args.json_path} -> "
        f"OK={counts['ok']} SKIP={counts['skip']} TOTAL={counts['total']}"
    )


if __name__ == "__main__":
    main()