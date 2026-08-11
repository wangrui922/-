"""导入汇总报告（T05 · T4）。

产出两份：
    * JSON —— 机器可读，供后续脚本/CI 比对；
    * Markdown —— 人读，直接贴给主理人验收（V5）。

报告必须让人一眼看清三件事：
    1. 每个文件解析了多少题、多少题有答案；
    2. **哪些卷被跳过、为什么**（苏教 A 组的三重闸门失败会在这里显形）；
    3. 新题经 **质量闸门** 判定后，进池（``active``）与待复核（``archived``）各多少
       （P1：污染题默认不上架）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.services.importers.schema import LoadStats, ParseOutcome


def _now_text() -> str:
    """当前 UTC 时间的可读字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def build_report(
    outcomes: Iterable[ParseOutcome],
    stats: LoadStats,
    *,
    failures: Optional[List[Dict[str, str]]] = None,
    db_counts: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """汇总成一个可 JSON 序列化的字典。

    Args:
        outcomes: 全部解析结果。
        stats: 落库统计。
        failures: 整单元失败清单 ``[{"source": ..., "reason": ...}]``。
        db_counts: 库内计数快照。
        dry_run: 是否只解析不落库。

    Returns:
        报告字典。
    """
    items = [o.to_dict() for o in outcomes]
    total_questions = sum(i["questions"] for i in items)
    total_answered = sum(i["answered"] for i in items)
    total_skipped = sum(len(i["skipped"]) for i in items)

    return {
        "generatedAt": _now_text(),
        "dryRun": dry_run,
        "summary": {
            "sources": len(items),
            "questionsParsed": total_questions,
            "questionsWithAnswer": total_answered,
            "questionsMissingAnswer": total_questions - total_answered,
            "papersSkipped": total_skipped,
            "sourcesFailed": len(failures or []),
        },
        "load": stats.to_dict(),
        "dbCounts": db_counts or {},
        "sources": items,
        "failures": list(failures or []),
    }


def render_markdown(report: Dict[str, Any]) -> str:
    """把报告渲染成 Markdown。

    Args:
        report: :func:`build_report` 的产物。

    Returns:
        Markdown 文本。
    """
    summary = report["summary"]
    load = report["load"]
    lines: List[str] = []

    lines.append("# docx 题库导入报告")
    lines.append("")
    lines.append(f"> 生成时间：{report['generatedAt']}　｜　"
                 f"模式：{'DRY-RUN（只解析不落库）' if report['dryRun'] else '正式导入'}")
    lines.append("")

    lines.append("## 一、总览")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 处理源单元 | {summary['sources']} |")
    lines.append(f"| 解析题目数 | {summary['questionsParsed']} |")
    lines.append(f"| 其中带答案 | {summary['questionsWithAnswer']} |")
    lines.append(f"| 答案缺失 | {summary['questionsMissingAnswer']} |")
    lines.append(f"| 跳过的卷 | {summary['papersSkipped']} |")
    lines.append(f"| 整单元失败 | {summary['sourcesFailed']} |")
    lines.append(f"| 落库新增 | {load['inserted']} |")
    lines.append(f"| 落库更新 | {load['updated']} |")
    lines.append(f"| 质量闸门 clean（进推荐池） | {load['gatedClean']} |")
    lines.append(f"| 质量闸门 review（archived 待复核） | {load['gatedReview']} |")
    lines.append(f"| 已上架题被保护(未降级) | {load['promotedPreserved']} |")
    lines.append("")

    db_counts = report.get("dbCounts") or {}
    if db_counts:
        lines.append("## 二、库内计数（导入后）")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        for key, value in db_counts.items():
            lines.append(f"| {key} | {value} |")
        lines.append("")

    lines.append("## 三、逐文件明细")
    lines.append("")
    lines.append("| 源 | 版本 | 类型 | 年级(升入) | 题数 | 带答案 | 跳过卷数 |")
    lines.append("|----|------|------|-----------|------|--------|---------|")
    for item in report["sources"]:
        lines.append(
            f"| {item['label']} | {item['edition']} | {item['sourceType']} | "
            f"{item['grade']} | {item['questions']} | {item['answered']} | "
            f"{len(item['skipped'])} |"
        )
    lines.append("")

    skipped_rows = [
        (item["label"], s) for item in report["sources"] for s in item["skipped"]
    ]
    lines.append("## 四、跳过的卷（逐条原因）")
    lines.append("")
    if skipped_rows:
        lines.append("| 源 | 卷 | 原因 | 详情 |")
        lines.append("|----|----|------|------|")
        for label, s in skipped_rows:
            lines.append(f"| {label} | {s['paperNo']} | {s['reason']} | {s['detail']} |")
    else:
        lines.append("（无）")
    lines.append("")

    lines.append("## 五、整单元失败（苏教 A 组配对闸门等）")
    lines.append("")
    if report["failures"]:
        lines.append("| 源 | 原因 |")
        lines.append("|----|------|")
        for fail in report["failures"]:
            lines.append(f"| {fail.get('source', '')} | {fail.get('reason', '')} |")
    else:
        lines.append("（无）")
    lines.append("")

    warn_rows = [(item["label"], w) for item in report["sources"] for w in item["warnings"]]
    lines.append("## 六、告警")
    lines.append("")
    if warn_rows:
        for label, warn in warn_rows:
            lines.append(f"- **{label}**：{warn}")
    else:
        lines.append("（无）")
    lines.append("")

    lines.append("## 七、下一步")
    lines.append("")
    lines.append("1. 新题经质量闸门判定：`clean` 进推荐池（active），`review` 落 `archived` 待复核（P1）。")
    lines.append("2. 抽检：`python backend/scripts/promote_bank.py --sample 30`")
    lines.append("3. 上架：`python backend/scripts/promote_bank.py --promote --source-doc <文件名>`（仅 clean 题）")
    lines.append("4. 全量上架（双保险，跳过 review）：`python backend/scripts/promote_bank.py --promote --all`")
    lines.append("")
    return "\n".join(lines)


def write_report(
    report: Dict[str, Any],
    output_dir: Path,
    *,
    stem: str = "docx_import_report",
) -> Dict[str, Path]:
    """把报告写盘（JSON + Markdown）。

    Args:
        report: 报告字典。
        output_dir: 输出目录（不存在会自动建）。
        stem: 文件名主干。

    Returns:
        ``{"json": Path, "markdown": Path}``。
    """
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)

    json_path = target / f"{stem}.json"
    md_path = target / f"{stem}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}


__all__ = ["build_report", "render_markdown", "write_report"]
