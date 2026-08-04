"""从 ``index.html`` 抽取 ``QUESTION_BANK`` 常量，产出 seed JSON（T02 · 2.3）。

背景：
    V3 单页应用把 909 道题硬编码在 ``index.html:2737`` 的 ``const QUESTION_BANK={...}``。
    这是 **JS 对象字面量**（键不带引号、字符串用单引号、允许尾逗号），
    ``json.loads`` 直接解析必然失败。

解析策略（两级，自动降级）：
    1. **首选 node**：把对象块包进一段临时 ``.mjs``，用 ``node`` eval 后
       ``JSON.stringify`` 输出到 stdout，Python 再 ``json.loads`` 读回。
       与浏览器同源语义，零解析风险。
    2. **纯 Python 兜底**：本机没有 node 时，用内置的 ``JsObjectParser``
       （一个只认 对象/数组/字符串/数字/字面量 的小型递归下降解析器）解析，
       不执行任何代码，安全且确定。

产出：
    * ``backend/seed/questions.json``       —— 扁平化题目列表 + meta 统计
    * ``backend/seed/knowledge_points.json``—— 知识点清单（code/name/sort/description）

映射规则（与 docs/v41-task-breakdown.md T02·2.3 一致）：
    * ``tags.knowledge`` → knowledge_points.code/name
    * ``tags.difficulty`` 易/中/难 → 1/3/5
    * ``a``               → answers.correct_answer
    * ``tags.options``    → questions.options(JSON)
    * 小学 6 个年级 → status=active；初中 → status=archived

用法::

    python backend/scripts/extract_bank.py
    python backend/scripts/extract_bank.py --html ../index.html --out ./seed
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 目录结构: <root>/backend/scripts/extract_bank.py → BACKEND_DIR = <root>/backend
BACKEND_DIR: Path = Path(__file__).resolve().parent.parent
ROOT_DIR: Path = BACKEND_DIR.parent
DEFAULT_HTML: Path = ROOT_DIR / "index.html"
DEFAULT_OUT_DIR: Path = BACKEND_DIR / "seed"

#: JS 常量名锚点
BANK_ANCHOR: str = "const QUESTION_BANK="

# ---------------------------------------------------------------- 枚举与映射
#: 年级中文 → 数字
GRADE_MAP: Dict[str, int] = {
    "一年级": 1,
    "二年级": 2,
    "三年级": 3,
    "四年级": 4,
    "五年级": 5,
    "六年级": 6,
    "七年级": 7,
    "八年级": 8,
    "九年级": 9,
    "初一": 7,
    "初二": 8,
    "初三": 9,
}

#: 难度中文 → 1/3/5
DIFFICULTY_MAP: Dict[str, int] = {"易": 1, "中": 3, "难": 5}

#: 学段 → 题目状态（老板拍板：V4.1 只做小学，初中题归档留存）
STAGE_STATUS: Dict[str, str] = {"小学": "active", "初中": "archived"}

#: 知识点清单（code 唯一、name 与 tags.knowledge 逐字对齐）
KNOWLEDGE_POINTS: List[Dict[str, Any]] = [
    {"code": "KP_CALC", "name": "计算", "sort": 10, "description": "四则运算、口算笔算、简便运算与运算定律"},
    {"code": "KP_EQUATION", "name": "方程", "sort": 20, "description": "用字母表示数、解方程与列方程解决问题"},
    {"code": "KP_WORD", "name": "应用题", "sort": 30, "description": "文字情境题，考察审题与数量关系建模"},
    {"code": "KP_FRACTION", "name": "分数", "sort": 40, "description": "分数的意义、性质、四则运算与分数应用"},
    {"code": "KP_DECIMAL", "name": "小数", "sort": 50, "description": "小数的意义、性质与小数四则运算"},
    {"code": "KP_PERCENT", "name": "百分数", "sort": 60, "description": "百分数意义、折扣税率利率与百分数应用"},
    {"code": "KP_RATIO", "name": "比例", "sort": 70, "description": "比、比例、正反比例与按比分配"},
    {"code": "KP_GEOMETRY", "name": "几何", "sort": 80, "description": "周长面积体积等度量计算"},
    {"code": "KP_SHAPE", "name": "图形", "sort": 90, "description": "图形的认识、位置与变换、观察物体"},
    {"code": "KP_STATISTICS", "name": "统计", "sort": 100, "description": "数据收集整理、统计图表与平均数"},
    {"code": "KP_UNIT", "name": "单位换算", "sort": 110, "description": "长度重量面积体积时间货币单位换算"},
    {"code": "KP_NUMBER_THEORY", "name": "数论", "sort": 120, "description": "因数倍数、质数合数、公因数公倍数"},
    {"code": "KP_MIXED", "name": "综合", "sort": 900, "description": "跨知识点综合题与思维拓展题"},
]

#: name → code 快查表
KP_CODE_BY_NAME: Dict[str, str] = {item["name"]: item["code"] for item in KNOWLEDGE_POINTS}

#: 未在清单内的知识点统一挂到「综合」，并在 meta.unknownKnowledge 里回报
FALLBACK_KP_NAME: str = "综合"


# ==================================================================== 纯 Python 兜底解析器
class JsObjectParser:
    """极简 JS 对象字面量解析器（只解析数据，不执行代码）。

    支持：对象 / 数组 / 单双引号与反引号字符串（无插值）/ 数字 /
    ``true|false|null|undefined`` / 不带引号的键 / 尾逗号 / ``//`` 与 ``/* */`` 注释。

    不支持（本数据集不含）：函数、模板插值、正则、扩展运算符。
    """

    def __init__(self, text: str) -> None:
        self.text: str = text
        self.pos: int = 0
        self.length: int = len(text)

    # ---------------------------------------------------------- 基础工具
    def _error(self, message: str) -> ValueError:
        head = max(0, self.pos - 60)
        snippet = self.text[head:self.pos + 60].replace("\n", "\\n")
        return ValueError(f"{message} @pos={self.pos} 附近: ...{snippet}...")

    def _peek(self) -> str:
        return self.text[self.pos] if self.pos < self.length else ""

    def _skip_ws(self) -> None:
        """跳过空白与注释。"""
        while self.pos < self.length:
            char = self.text[self.pos]
            if char in " \t\r\n\ufeff":
                self.pos += 1
                continue
            if char == "/" and self.pos + 1 < self.length:
                nxt = self.text[self.pos + 1]
                if nxt == "/":
                    end = self.text.find("\n", self.pos)
                    self.pos = self.length if end < 0 else end + 1
                    continue
                if nxt == "*":
                    end = self.text.find("*/", self.pos + 2)
                    if end < 0:
                        raise self._error("块注释未闭合")
                    self.pos = end + 2
                    continue
            break

    # ---------------------------------------------------------- 各类字面量
    def _parse_string(self) -> str:
        quote = self.text[self.pos]
        self.pos += 1
        buf: List[str] = []
        escapes = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
        while self.pos < self.length:
            char = self.text[self.pos]
            if char == "\\":
                self.pos += 1
                if self.pos >= self.length:
                    raise self._error("字符串转义未闭合")
                esc = self.text[self.pos]
                if esc == "u":
                    hex4 = self.text[self.pos + 1:self.pos + 5]
                    buf.append(chr(int(hex4, 16)))
                    self.pos += 5
                    continue
                if esc == "x":
                    hex2 = self.text[self.pos + 1:self.pos + 3]
                    buf.append(chr(int(hex2, 16)))
                    self.pos += 3
                    continue
                buf.append(escapes.get(esc, esc))
                self.pos += 1
                continue
            if char == quote:
                self.pos += 1
                return "".join(buf)
            buf.append(char)
            self.pos += 1
        raise self._error("字符串未闭合")

    def _parse_ident(self) -> str:
        start = self.pos
        while self.pos < self.length:
            char = self.text[self.pos]
            if char.isalnum() or char in "_$":
                self.pos += 1
                continue
            break
        if start == self.pos:
            raise self._error("期望标识符")
        return self.text[start:self.pos]

    def _parse_number(self) -> Any:
        start = self.pos
        if self._peek() in "+-":
            self.pos += 1
        while self.pos < self.length and (self.text[self.pos].isdigit() or self.text[self.pos] in ".eE+-"):
            # 指数符号后的正负号才允许，普通位置的 +- 需在数字内部出现
            if self.text[self.pos] in "+-" and self.text[self.pos - 1] not in "eE":
                break
            self.pos += 1
        raw = self.text[start:self.pos]
        try:
            return int(raw)
        except ValueError:
            return float(raw)

    def _parse_array(self) -> List[Any]:
        self.pos += 1  # 吃掉 '['
        items: List[Any] = []
        while True:
            self._skip_ws()
            if self._peek() == "]":
                self.pos += 1
                return items
            items.append(self.parse_value())
            self._skip_ws()
            if self._peek() == ",":
                self.pos += 1
                continue
            if self._peek() == "]":
                self.pos += 1
                return items
            raise self._error("数组元素后期望 , 或 ]")

    def _parse_object(self) -> Dict[str, Any]:
        self.pos += 1  # 吃掉 '{'
        obj: Dict[str, Any] = {}
        while True:
            self._skip_ws()
            if self._peek() == "}":
                self.pos += 1
                return obj
            char = self._peek()
            if char in "'\"`":
                key = self._parse_string()
            else:
                key = self._parse_ident()
            self._skip_ws()
            if self._peek() != ":":
                raise self._error(f"键 {key!r} 之后期望 :")
            self.pos += 1
            obj[key] = self.parse_value()
            self._skip_ws()
            if self._peek() == ",":
                self.pos += 1
                continue
            if self._peek() == "}":
                self.pos += 1
                return obj
            raise self._error("对象成员后期望 , 或 }")

    def parse_value(self) -> Any:
        """解析任意一个值。"""
        self._skip_ws()
        char = self._peek()
        if char == "":
            raise self._error("意外的输入结束")
        if char == "{":
            return self._parse_object()
        if char == "[":
            return self._parse_array()
        if char in "'\"`":
            return self._parse_string()
        if char.isdigit() or (char in "+-" and self.pos + 1 < self.length and self.text[self.pos + 1].isdigit()):
            return self._parse_number()
        ident = self._parse_ident()
        if ident == "true":
            return True
        if ident == "false":
            return False
        if ident in ("null", "undefined"):
            return None
        raise self._error(f"无法识别的字面量 {ident!r}")


# ==================================================================== 抽取
def slice_bank_literal(html_text: str) -> str:
    """从 HTML 全文里切出 ``QUESTION_BANK`` 的对象字面量（含首尾花括号）。

    Args:
        html_text: ``index.html`` 全文。

    Returns:
        形如 ``{'小学':{...},'初中':{...}}`` 的 JS 源码片段。

    Raises:
        ValueError: 找不到锚点或花括号不配对。
    """
    anchor_at = html_text.find(BANK_ANCHOR)
    if anchor_at < 0:
        raise ValueError(f"在 HTML 中找不到锚点 {BANK_ANCHOR!r}")

    start = html_text.find("{", anchor_at + len(BANK_ANCHOR))
    if start < 0:
        raise ValueError("锚点之后找不到对象起始花括号")

    depth = 0
    index = start
    in_string: Optional[str] = None
    while index < len(html_text):
        char = html_text[index]
        if in_string is not None:
            if char == "\\":
                index += 2
                continue
            if char == in_string:
                in_string = None
            index += 1
            continue
        if char in "'\"`":
            in_string = char
            index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return html_text[start:index + 1]
        index += 1
    raise ValueError("对象字面量花括号不配对（未找到结束位置）")


def parse_with_node(literal: str) -> Optional[Dict[str, Any]]:
    """用 node eval 把 JS 字面量转成 JSON；node 不可用时返回 ``None``。"""
    node_bin = shutil.which("node")
    if not node_bin:
        return None

    script = "const BANK = %s;\nprocess.stdout.write(JSON.stringify(BANK));\n" % literal
    tmp_dir = Path(tempfile.mkdtemp(prefix="bank_extract_"))
    tmp_js = tmp_dir / "dump_bank.mjs"
    try:
        tmp_js.write_text(script, encoding="utf-8")
        completed = subprocess.run(
            [node_bin, str(tmp_js)],
            capture_output=True,
            timeout=120,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            print(f"[extract_bank] node 解析失败，降级到纯 Python 解析器：{stderr[:200]}")
            return None
        payload = completed.stdout.decode("utf-8", errors="strict")
        return json.loads(payload)
    except Exception as exc:  # pragma: no cover - 环境相关
        print(f"[extract_bank] node 调用异常，降级到纯 Python 解析器：{exc}")
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def parse_bank(literal: str) -> Tuple[Dict[str, Any], str]:
    """解析 JS 字面量，返回 ``(题库对象, 使用的解析器名)``。"""
    parsed = parse_with_node(literal)
    if parsed is not None:
        return parsed, "node"
    parser = JsObjectParser(literal)
    return parser.parse_value(), "python"


# ==================================================================== 转换
def _normalize_options(raw_options: Any) -> Optional[Dict[str, str]]:
    """把 options 规整成 ``{"A": "...", "B": "..."}``；无选项返回 None。"""
    if not raw_options:
        return None
    if isinstance(raw_options, dict):
        return {str(key): str(value) for key, value in raw_options.items()}
    if isinstance(raw_options, list):
        letters = "ABCDEFGH"
        return {letters[i]: str(value) for i, value in enumerate(raw_options) if i < len(letters)}
    return None


def _infer_question_type(knowledge: str, options: Optional[Dict[str, str]]) -> str:
    """推断题型（questions.type，取值必须落在 QUESTION_TYPE_VALUES）。"""
    if options:
        return "选择"
    if knowledge == "应用题":
        return "应用"
    if knowledge in ("计算", "分数", "小数", "百分数", "方程"):
        return "计算"
    return "填空"


def flatten_bank(bank: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """把嵌套题库拍平成题目列表，并统计 meta。

    Args:
        bank: ``{学段: {年级: [题目, ...]}}``。

    Returns:
        ``(题目列表, meta 统计字典)``。
    """
    questions: List[Dict[str, Any]] = []
    seen_ids: Dict[str, int] = {}
    duplicated: List[str] = []
    unknown_knowledge: Dict[str, int] = {}
    by_stage: Dict[str, int] = {}
    by_grade: Dict[str, int] = {}
    by_difficulty: Dict[str, int] = {}
    by_knowledge: Dict[str, int] = {}

    for stage, grades in bank.items():
        if not isinstance(grades, dict):
            continue
        for grade_label, items in grades.items():
            if not isinstance(items, list):
                continue
            grade_num = GRADE_MAP.get(grade_label)
            for item in items:
                if not isinstance(item, dict):
                    continue
                tags: Dict[str, Any] = item.get("tags") or {}
                knowledge = str(tags.get("knowledge") or FALLBACK_KP_NAME).strip()
                if knowledge not in KP_CODE_BY_NAME:
                    unknown_knowledge[knowledge] = unknown_knowledge.get(knowledge, 0) + 1
                    kp_name = FALLBACK_KP_NAME
                else:
                    kp_name = knowledge

                difficulty_label = str(tags.get("difficulty") or "中").strip()
                options = _normalize_options(tags.get("options"))
                bank_id = str(item.get("id") or "").strip()
                if not bank_id:
                    continue
                seen_ids[bank_id] = seen_ids.get(bank_id, 0) + 1
                if seen_ids[bank_id] > 1:
                    duplicated.append(bank_id)
                    continue

                record: Dict[str, Any] = {
                    "bankId": bank_id,
                    "stage": stage,
                    "gradeLabel": grade_label,
                    "grade": grade_num,
                    "content": str(item.get("q") or "").strip(),
                    "answer": str(item.get("a") or "").strip(),
                    "knowledge": kp_name,
                    "rawKnowledge": knowledge,
                    "kpCode": KP_CODE_BY_NAME[kp_name],
                    "difficultyLabel": difficulty_label,
                    "difficulty": DIFFICULTY_MAP.get(difficulty_label, 3),
                    "errorType": str(tags.get("errorType") or "").strip() or None,
                    "ability": str(tags.get("ability") or "").strip() or None,
                    "options": options,
                    "type": _infer_question_type(kp_name, options),
                    "status": STAGE_STATUS.get(stage, "archived"),
                }
                questions.append(record)

                by_stage[stage] = by_stage.get(stage, 0) + 1
                by_grade[f"{stage}-{grade_label}"] = by_grade.get(f"{stage}-{grade_label}", 0) + 1
                by_difficulty[difficulty_label] = by_difficulty.get(difficulty_label, 0) + 1
                by_knowledge[kp_name] = by_knowledge.get(kp_name, 0) + 1

    meta: Dict[str, Any] = {
        "total": len(questions),
        "primaryTotal": by_stage.get("小学", 0),
        "juniorTotal": by_stage.get("初中", 0),
        "byStage": by_stage,
        "byGrade": dict(sorted(by_grade.items())),
        "byDifficulty": by_difficulty,
        "byKnowledge": dict(sorted(by_knowledge.items(), key=lambda kv: -kv[1])),
        "duplicatedIds": sorted(set(duplicated)),
        "unknownKnowledge": unknown_knowledge,
    }
    return questions, meta


def write_json(path: Path, payload: Any) -> None:
    """UTF-8 无 BOM 写 JSON（缩进 2，保留中文）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run(html_path: Path, out_dir: Path) -> Dict[str, Any]:
    """执行抽取全流程，返回 meta 统计。"""
    if not html_path.exists():
        raise FileNotFoundError(f"找不到源文件: {html_path}")

    html_text = html_path.read_text(encoding="utf-8", errors="strict")
    literal = slice_bank_literal(html_text)
    print(f"[extract_bank] 已切出 QUESTION_BANK 字面量，长度 {len(literal)} 字符")

    bank, parser_name = parse_bank(literal)
    print(f"[extract_bank] 解析完成（解析器: {parser_name}），学段: {list(bank.keys())}")

    questions, meta = flatten_bank(bank)
    meta["parser"] = parser_name
    meta["source"] = str(html_path)

    write_json(out_dir / "questions.json", {"meta": meta, "questions": questions})
    write_json(out_dir / "knowledge_points.json", {"knowledgePoints": KNOWLEDGE_POINTS})

    print(f"[extract_bank] 题目总数         : {meta['total']}")
    print(f"[extract_bank] 小学(active)     : {meta['primaryTotal']}")
    print(f"[extract_bank] 初中(archived)   : {meta['juniorTotal']}")
    print(f"[extract_bank] 各年级分布       : {meta['byGrade']}")
    print(f"[extract_bank] 难度分布         : {meta['byDifficulty']}")
    print(f"[extract_bank] 知识点分布       : {meta['byKnowledge']}")
    if meta["duplicatedIds"]:
        print(f"[extract_bank] ⚠ 重复 id（已去重）: {meta['duplicatedIds']}")
    if meta["unknownKnowledge"]:
        print(f"[extract_bank] ⚠ 未登记知识点（已归入综合）: {meta['unknownKnowledge']}")
    print(f"[extract_bank] 输出: {out_dir / 'questions.json'}")
    print(f"[extract_bank] 输出: {out_dir / 'knowledge_points.json'}")
    return meta


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="从 index.html 抽取 QUESTION_BANK 生成 seed JSON")
    parser.add_argument("--html", default=str(DEFAULT_HTML), help="index.html 路径")
    parser.add_argument("--out", default=str(DEFAULT_OUT_DIR), help="seed 输出目录")
    args = parser.parse_args()

    try:
        run(Path(args.html).resolve(), Path(args.out).resolve())
    except Exception as exc:
        print(f"[extract_bank] 失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
