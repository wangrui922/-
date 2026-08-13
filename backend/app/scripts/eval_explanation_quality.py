"""讲解质量评测 harness 的命令行入口（#67）。

给一段「AI 家教的讲解」自动打 5 维分 + 综合分 + 改进建议，守住"教得好"。

用法
----
# 给内置好/坏两个样例打分（需配置 ARK_API_KEY 且有网）
python -m app.scripts.eval_explanation_quality

# 评测自定义一段讲解
python -m app.scripts.eval_explanation_quality \
    --question "解方程 2x+3=11" \
    --attempt "孩子直接写了 x=4" \
    --explanation "我们反过来想：等号两边都先减 3，会得到什么？" \
    --error-type "移项符号错误"

# 输出 JSON（便于接入质量闸门）
python -m app.scripts.eval_explanation_quality --question "..." --explanation "..." --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List

# 让 `python app/scripts/eval_explanation_quality.py` 与 `-m` 都可用
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.ai.ark_client import ArkError  # noqa: E402
from app.services.explanation_quality import (  # noqa: E402
    DIMENSION_KEYS,
    evaluate_explanation,
)

#: 内置演示样例：① 优秀苏格拉底引导 ② 不及格（直接给答案）
_SAMPLES: List[dict] = [
    {
        "name": "样例A · 优秀的苏格拉底引导",
        "question": "解方程 2x + 3 = 11",
        "student_attempt": "我直接写了 x = 4，但不知道怎么来的",
        "ai_explanation": (
            "我们先不急着要答案。想想看：现在左边是 2x 加 3，"
            "如果想先看到 2x 单独待着，等号两边都减 3，会变成什么？"
        ),
        "error_type": "移项符号错误",
    },
    {
        "name": "样例B · 不及格（直接给答案）",
        "question": "解方程 2x + 3 = 11",
        "student_attempt": "我直接写了 x = 4，但不知道怎么来的",
        "ai_explanation": "答案是 x=4。因为 11-3=8，8÷2=4。记住这个结果就行。",
        "error_type": "移项符号错误",
    },
]


def _print_human(sample_name: str, eq) -> None:
    print(f"\n=== {sample_name} ===")
    for line in eq.report_lines():
        print(line)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI 讲解质量评测 harness")
    parser.add_argument("--question", help="题目文本")
    parser.add_argument("--attempt", default="", help="学生的作答 / 错误描述")
    parser.add_argument("--explanation", help="要评测的 AI 讲解文本")
    parser.add_argument("--error-type", default=None, help="可选的错因类型（诊断结论）")
    parser.add_argument("--threshold", type=int, default=4, help="质量闸门阈值（默认 4）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args(argv)

    # 没给 --explanation 就跑内置样例
    if not args.explanation:
        samples = _SAMPLES
    else:
        samples = [
            {
                "name": "自定义评测",
                "question": args.question or "",
                "student_attempt": args.attempt or "",
                "ai_explanation": args.explanation,
                "error_type": args.error_type,
            }
        ]

    results = []
    for s in samples:
        try:
            eq = evaluate_explanation(
                question=s["question"],
                student_attempt=s["student_attempt"],
                ai_explanation=s["ai_explanation"],
                error_type=s.get("error_type"),
            )
        except ArkError as exc:
            print(f"\n=== {s['name']} ===")
            print(f"⚠️ 评测失败（方舟调用异常）：{exc}")
            print("   请确认 ARK_API_KEY 已配置且本机可访问方舟。")
            return 1

        if args.json:
            results.append(
                {
                    "name": s["name"],
                    "overall": eq.overall,
                    "grade": eq.grade,
                    "dimensions": eq.dimensions,
                    "suggestion": eq.suggestion,
                    "passes": eq.passes(args.threshold),
                    "parsed_ok": eq.parsed_ok,
                }
            )
        else:
            _print_human(s["name"], eq)
            print(f"→ 是否达闸门(>={args.threshold})：{'是' if eq.passes(args.threshold) else '否'}")

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
