# -*- coding: utf-8 -*-
"""方向八 · 辅导话术质量评测（mathtutorbench 思路落地）。

定期跑一遍「诊断式追问」链路，验证两个层面：

1. 离线层（``--dry``，默认零模型零网络）—— 纯模板 / 规则质量检查：
   - MathDial 话术库：五错因 × 三环节（diagnose/hint/elaborate）全覆盖且非空；
   - L2 注入点 ``state_machine.diagnostic_tactic_prompt``：各 mode × 错因的语义
     （含「先诊断再教学」总则、错因标签行、模式→环节映射、None/非法回退）；
   - L3 话术生成 ``tutor._build_request``：system 拼接链含诊断后缀，且
     ``error_type=None`` 时与改造前完全一致（不追加）；
   - 练习引导 ``practice_guide.PROMPT`` v1.3.0 含【先诊断再教学】小节。

2. 在线层（默认，需 ARK_API_KEY / ARK_MODEL）—— 真实模型抽样评测：
   - 内置 ``CASES`` 教学样例（题干 / 作答 / 错因 / 期望行为），逐个跑
     ``tutor.next_question``；
   - 判定：不泄漏答案、贴着错因问、不直接给答案、结尾是追问（只报统计，
     不做硬性通过与失败，交给人工复核看样例回复）。

用法：:

    python backend/scripts/eval_tutor_bench.py --dry            # 只做离线模板检查
    python backend/scripts/eval_tutor_bench.py                  # 离线 + 在线抽样
    python backend/scripts/eval_tutor_bench.py --report DIR     # 指定报告输出目录

输出：JSON 报告写入 ``scripts/eval_reports/eval_tutor_bench_<ts>.json``；
退出码：离线层全部通过 = 0，任一失败 = 1；在线层失败不阻塞退出码（单独标注）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.ai import intent  # noqa: E402
from app.ai.agents import tutor  # noqa: E402
from app.ai.answer_guard import has_answer_leak  # noqa: E402
from app.ai.prompts import mathdial  # noqa: E402
from app.ai.prompts import practice_guide  # noqa: E402
from app.ai.state_machine import diagnostic_tactic_prompt  # noqa: E402
from app.core import error_categories as ec  # noqa: E402

REPORT_DIR = BACKEND_DIR / "scripts" / "eval_reports"

#: 五类错因 × 需要覆盖的三环节（MathDial 话术表完整性检查用）
ALL_ERRORS: tuple[str, ...] = (
    ec.ERROR_READ,
    ec.ERROR_METHOD,
    ec.ERROR_CALC,
    ec.ERROR_KNOWLEDGE,
    ec.ERROR_CHECK,
)
ALL_TACTICS: tuple[str, ...] = (mathdial.TACTIC_DIAGNOSE, mathdial.TACTIC_HINT, mathdial.TACTIC_ELABORATE)

#: 真实模式抽样教学样例（题干 / 作答 / 错因 / 期望行为）
CASES: List[Dict[str, Any]] = [
    {
        "id": "case-read-01",
        "q": "一块长方形菜地，长 12 米，宽 8 米，围着它走一圈要走多少米？",
        "attempt": "12 × 8 = 96，所以是 96 平方米。",
        "error": "READ",
        "expect": "先确认孩子读懂了「一圈 = 周长」还是求面积，不直接讲公式。",
    },
    {
        "id": "case-method-01",
        "q": "小明有 3 盒铅笔，每盒 6 支，一共有几支？",
        "attempt": "3 + 6 = 9 支。",
        "error": "METHOD",
        "expect": "用矛盾探测引导：3 盒每盒 6 支，加在一起对吗？总数应该怎么算。",
    },
    {
        "id": "case-calc-01",
        "q": "35 + 47，用竖式算一算。",
        "attempt": "35 + 47 = 72。",
        "error": "CALC",
        "expect": "不重新讲方法，只聚焦进位这一步：个位 5+7 满十进了吗？",
    },
    {
        "id": "case-knowledge-01",
        "q": "一个圆，半径 r=4 厘米，求它的面积。",
        "attempt": "不会，不知道用哪个公式。",
        "error": "KNOWLEDGE",
        "expect": "只补一句概念（面积公式），引导孩子自己套 r 的值。",
    },
    {
        "id": "case-check-01",
        "q": "一辆车每小时行 60 千米，3 小时行多少千米？",
        "attempt": "60 × 3 = 180。",
        "error": "CHECK",
        "expect": "结果对了，提醒孩子补单位与答句，不给答案。",
    },
    {
        "id": "case-unclassified-01",
        "q": "9 × 7 等于多少？",
        "attempt": "算出来是 65。",
        "error": "",
        "expect": "没有明确错因时先诊断：让孩子说出怎么算的，不要直接判错给答案。",
    },
]


# ================================================================== 离线层检查
def _check_mathdial_table(errors: List[str]) -> List[str]:
    """话术库完整性：五错因 × 三环节均有指令与参考问句。"""
    problems: List[str] = []
    for code in errors:
        for tactic in ALL_TACTICS:
            instruction = mathdial.tactic_instruction(code, tactic)
            phrase_a = mathdial.pick_phrase(code, tactic, variant=0)
            phrase_b = mathdial.pick_phrase(code, tactic, variant=1)
            if not instruction or not phrase_a or not phrase_b:
                problems.append(f"mathdial 表缺内容: ({code}, {tactic})")
    return problems


def _check_diagnostic_prompt() -> List[str]:
    """L2 注入点语义：总则 / 错因标签 / 模式映射 / 非法回退。"""
    problems: List[str] = []

    # 1. 总则出现在所有模式
    for mode in intent.MODE_VALUES:
        text = diagnostic_tactic_prompt(mode, error_type=ec.ERROR_READ)
        if mathdial.DIAGNOSE_FIRST_INSTRUCTION not in text:
            problems.append(f"diagnostic_tactic_prompt({mode}) 缺【先诊断再教学】总则")
        if "本轮教学动作" not in text:
            problems.append(f"diagnostic_tactic_prompt({mode}) 缺教学动作行")

    # 2. 错因标签行只在该有错因时出现
    with_label = diagnostic_tactic_prompt("HINT", error_type=ec.ERROR_READ)
    if "读题错" not in with_label:
        problems.append("diagnostic_tactic_prompt(HINT, READ) 应有『读题错』标签行")

    # 3. 模式 → 环节映射：HINT→提示、DEMO→讲解、GUIDE→诊断
    if "提示" not in diagnostic_tactic_prompt("HINT", error_type=ec.ERROR_READ):
        problems.append("HINT 模式未映射到『提示』环节")
    if "讲解" not in diagnostic_tactic_prompt(intent.MODE_DEMO, error_type=ec.ERROR_READ):
        problems.append("DEMO 模式未映射到『讲解』环节")
    if "诊断" not in diagnostic_tactic_prompt("GUIDE", error_type=ec.ERROR_READ):
        problems.append("GUIDE 模式未映射到『诊断』环节")

    # 4. None / 非法 mode → 空串（向后兼容）；None 错因 → 通用诊断指令
    if diagnostic_tactic_prompt(None, error_type=ec.ERROR_READ) != "":
        problems.append("diagnostic_tactic_prompt(None, ...) 应返回空串")
    if diagnostic_tactic_prompt("nonsense", error_type=ec.ERROR_READ) != "":
        problems.append("diagnostic_tactic_prompt(非法mode) 应返回空串")
    code = ec.normalize_error(None)
    if code != ec.ERROR_UNCLASSIFIED:
        problems.append(f"normalize_error(None) 应为 UNCLASSIFIED，实际 {code}")

    # 5. 五错因 × 三环节的问法不空
    problems += _check_mathdial_table(list(ALL_ERRORS))
    return problems


def _check_tutor_build_request() -> List[str]:
    """L3 拼接：error_type 提供 → 诊断后缀存在；None → 与改造前一致。"""
    problems: List[str] = []
    base = [
        {"role": "user", "content": "题目：3 + 4 = ?"},
        {"role": "assistant", "content": "你觉得先算哪一步？"},
    ]

    with_dx = tutor._build_request(
        session_messages=base, current_error=None, state="guiding", mode="GUIDE",
        error_type=ec.ERROR_CALC,
    )
    sys_text_with = with_dx[1]
    if mathdial.DIAGNOSE_FIRST_INSTRUCTION not in sys_text_with:
        problems.append("error_type 注入后 system 缺【先诊断再教学】总则")
    if "计算错" not in sys_text_with:
        problems.append("error_type=CALC 时 system 应有『计算错』标签行")

    no_dx = tutor._build_request(
        session_messages=base, current_error=None, state="guiding", mode="GUIDE",
        error_type=None,
    )
    sys_text_no = no_dx[1]
    if "诊断式追问" in sys_text_no:
        problems.append("error_type=None 时不应追加诊断式追问（向后兼容被破坏）")
    if "【诊断式追问】" in sys_text_no:
        problems.append("error_type=None 时不应追加【诊断式追问】区块（向后兼容被破坏）")
    return problems


def _check_practice_guide() -> List[str]:
    """练习引导模板 v1.3.0：含【先诊断再教学】小节。"""
    problems: List[str] = []
    if practice_guide.VERSION < "1.3.0":
        problems.append(f"practice_guide 版本过低: {practice_guide.VERSION}（应为 >= 1.3.0）")
    for marker in ("先诊断再教学", "他现在卡在哪一步", "你现在卡在哪一步"):
        if marker not in practice_guide.PROMPT:
            problems.append(f"practice_guide.PROMPT 缺少小节标记: {marker}")
    for placeholder in practice_guide.PLACEHOLDERS:
        if placeholder not in practice_guide.PROMPT:
            problems.append(f"practice_guide 丢失占位符: {placeholder}")
    return problems


def run_offline() -> List[str]:
    """离线模板检查，返回全部问题（空 = 通过）。"""
    problems: List[str] = []
    problems += _check_diagnostic_prompt()
    problems += _check_tutor_build_request()
    problems += _check_practice_guide()
    return problems


# ================================================================== 在线层评测
def _looks_like_followup(text: str) -> bool:
    """粗判回复结尾是否带追问（问号 / 疑问语气 / 祈使引导）。"""
    tail = text.strip().rstrip("…。.!！")
    if not tail:
        return False
    return tail.endswith(("？", "?", "吗", "呢", "吧", "看看", "想想", "试试", "再说说"))


def run_online(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """真实模式：逐个样例跑 tutor.next_question，返回逐条评价。"""
    results: List[Dict[str, Any]] = []
    for case in cases:
        err_type = case.get("error") or None
        mode = intent.MODE_HINT  # 引导追问阶段
        started = time.time()
        try:
            reply = tutor.next_question(
                session_messages=[{"role": "user", "content": "题目：" + case["q"] + "\n我写了：" + case["attempt"]}],
                state="guiding",
                mode=mode,
                error_type=err_type,
            )
            ok = True
            note = ""
        except Exception as exc:  # noqa: BLE001 —— 单条失败不中断整批
            reply = ""
            ok = False
            note = f"调用失败: {type(exc).__name__}: {exc}"
        leak = has_answer_leak(reply, strict=True) if reply else True
        followup = _looks_like_followup(reply) if reply else False
        results.append({
            "id": case["id"],
            "error": err_type or "UNCLASSIFIED",
            "expect": case["expect"],
            "reply": reply,
            "leak": leak,
            "followup": followup,
            "ok": ok,
            "note": note,
            "latency_ms": int((time.time() - started) * 1000),
        })
    return results


def _render_report(offline_problems: List[str], online: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "tutor-diagnostic-v1",
        "offline": {
            "passed": not offline_problems,
            "problems": offline_problems,
        },
        "online": {
            "cases": len(online),
            "leak_free": sum(1 for r in online if not r["leak"]),
            "followup": sum(1 for r in online if r["followup"]),
            "ok": sum(1 for r in online if r["ok"]),
            "details": online,
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="辅导话术质量评测（mathtutorbench 落地）")
    parser.add_argument("--dry", action="store_true", help="只做离线模板检查，不调用模型")
    parser.add_argument("--report", default=None, help="报告输出目录（默认 scripts/eval_reports）")
    args = parser.parse_args(argv)

    report_dir = Path(args.report) if args.report else REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)

    problems = run_offline()
    print("== 离线模板检查 ==")
    if problems:
        for p in problems:
            print(f"  [FAIL] {p}")
    else:
        print("  [PASS] MathDial 话术表 / L2 注入点 / L3 拼接 / 练习引导 v1.3.0 全部通过")

    online: List[Dict[str, Any]] = []
    if not args.dry:
        print("== 在线抽样评测（真实模型） ==")
        online = run_online(CASES)
        ok_cnt = sum(1 for r in online if r["ok"] and not r["leak"] and r["followup"])
        print(f"  [INFO] 合格 {ok_cnt}/{len(online)}；详单见报告")
        for r in online:
            flag = "PASS" if (r["ok"] and not r["leak"] and r["followup"]) else "WARN"
            print(f"  [{flag}] {r['id']} error={r['error']} leak={r['leak']} followup={r['followup']}")
            print(f"      expect: {r['expect']}")
            print(f"      reply : {r['reply'][:120]}")

    report = _render_report(problems, online)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = report_dir / f"eval_tutor_bench_{ts}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"== 报告 ==")
    print(f"  已写入 {out}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())