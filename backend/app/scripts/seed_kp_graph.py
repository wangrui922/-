"""知识图谱 seed：重建「13 L1 + 80 L2」层级 + 级/域标注 + 前置依赖边（P0·Phase 1）。

背景：
    生产库 ``knowledge_points`` 现有 93 条全部 ``parent_id = NULL``（层级丢失），
    与 ``kp_tree.py`` 设计注释「13 L1 + 80 L2」不符。本脚本一次性补齐：

        * 层级：80 个 L2 挂到对应 13 个 L1（``parent_id``）；
        * 标注：每个 KP 补 ``grade``（达标年级 1-9）与 ``domain``（四大领域）；
        * 依赖：写入 ``knowledge_prerequisite`` 前置依赖边（GKT 图骨架 + 抽题补漏）。

幂等口径：
    * **只能按 ``code`` 匹配现有行**（生产 KP 主键不是 uuid5 派生，严禁按 id 假设）；
    * 层级/标注用「存在才更新」：``code`` 找不到的行跳过并计数，不新建、不删除；
    * 依赖边先清空本表再写入（本表仅由本脚本维护，重建一次全量覆盖）；
    * 自检：L1 = 13、L2 = 80、边数 ≥ 50、无自环、无悬空引用。

用法（容器内）：
    docker exec deploy-backend-1 python app/scripts/seed_kp_graph.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import delete, func, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models.content import KnowledgePoint  # noqa: E402
from app.models.knowledge_prerequisite import KnowledgePrerequisite  # noqa: E402

# ====================================================================== 数据
#: code -> (parent_code | None, grade, domain)
#: 13 个 L1 的 parent_code 为 None；grade 为「达标年级」(1-9)；domain 为四大领域。
KP_GRAPH: dict[str, tuple[Optional[str], int, str]] = {
    # ---------------- L1（13 个）---------------
    "KP_CALC": (None, 6, "数与代数"),
    "KP_EQUATION": (None, 6, "数与代数"),
    "KP_WORD": (None, 6, "数与代数"),
    "KP_FRACTION": (None, 6, "数与代数"),
    "KP_DECIMAL": (None, 6, "数与代数"),
    "KP_PERCENT": (None, 6, "数与代数"),
    "KP_RATIO": (None, 6, "数与代数"),
    "KP_NUMBER_THEORY": (None, 7, "数与代数"),
    "KP_GEOMETRY": (None, 6, "图形与几何"),
    "KP_SHAPE": (None, 6, "图形与几何"),
    "KP_UNIT": (None, 6, "数与代数"),
    "KP_STATISTICS": (None, 6, "统计与概率"),
    "KP_MIXED": (None, 6, "综合实践"),
    # ---------------- 计算（7）----------------
    "KP_CALC_ADD": ("KP_CALC", 2, "数与代数"),
    "KP_CALC_MUL": ("KP_CALC", 3, "数与代数"),
    "KP_CALC_MIXED_OP": ("KP_CALC", 4, "数与代数"),
    "KP_CALC_LAW": ("KP_CALC", 4, "数与代数"),
    "KP_CALC_SIMPLE": ("KP_CALC", 5, "数与代数"),
    "KP_CALC_MENTAL": ("KP_CALC", 2, "数与代数"),
    "KP_CALC_RATIONAL": ("KP_CALC", 7, "数与代数"),
    # ---------------- 方程（7）----------------
    "KP_EQ_LETTER": ("KP_EQUATION", 5, "数与代数"),
    "KP_EQ_PROPERTY": ("KP_EQUATION", 5, "数与代数"),
    "KP_EQ_LINEAR1": ("KP_EQUATION", 7, "数与代数"),
    "KP_EQ_SYSTEM": ("KP_EQUATION", 8, "数与代数"),
    "KP_EQ_QUADRATIC": ("KP_EQUATION", 9, "数与代数"),
    "KP_EQ_INEQUALITY": ("KP_EQUATION", 7, "数与代数"),
    "KP_EQ_WORD": ("KP_EQUATION", 6, "数与代数"),
    # ---------------- 应用题（7）----------------
    "KP_WORD_SUM_DIFF": ("KP_WORD", 4, "数与代数"),
    "KP_WORD_TRAVEL": ("KP_WORD", 6, "数与代数"),
    "KP_WORD_WORK": ("KP_WORD", 6, "数与代数"),
    "KP_WORD_PRICE": ("KP_WORD", 6, "数与代数"),
    "KP_WORD_AGE": ("KP_WORD", 5, "数与代数"),
    "KP_WORD_PLANT": ("KP_WORD", 5, "数与代数"),
    "KP_WORD_GENERAL": ("KP_WORD", 6, "数与代数"),
    # ---------------- 分数（6）----------------
    "KP_FRAC_MEANING": ("KP_FRACTION", 3, "数与代数"),
    "KP_FRAC_PROPERTY": ("KP_FRACTION", 5, "数与代数"),
    "KP_FRAC_ADD_SUB": ("KP_FRACTION", 5, "数与代数"),
    "KP_FRAC_MUL_DIV": ("KP_FRACTION", 6, "数与代数"),
    "KP_FRAC_WORD": ("KP_FRACTION", 6, "数与代数"),
    "KP_FRAC_EXPR": ("KP_FRACTION", 8, "数与代数"),
    # ---------------- 小数（5）----------------
    "KP_DEC_MEANING": ("KP_DECIMAL", 3, "数与代数"),
    "KP_DEC_ADD_SUB": ("KP_DECIMAL", 4, "数与代数"),
    "KP_DEC_MUL_DIV": ("KP_DECIMAL", 5, "数与代数"),
    "KP_DEC_APPROX": ("KP_DECIMAL", 5, "数与代数"),
    "KP_DEC_CONVERT": ("KP_DECIMAL", 6, "数与代数"),
    # ---------------- 百分数（5）----------------
    "KP_PCT_MEANING": ("KP_PERCENT", 6, "数与代数"),
    "KP_PCT_DISCOUNT": ("KP_PERCENT", 6, "数与代数"),
    "KP_PCT_INTEREST": ("KP_PERCENT", 6, "数与代数"),
    "KP_PCT_CHANGE": ("KP_PERCENT", 6, "数与代数"),
    "KP_PCT_WORD": ("KP_PERCENT", 6, "数与代数"),
    # ---------------- 比例（5）----------------
    "KP_RATIO_BASIC": ("KP_RATIO", 6, "数与代数"),
    "KP_RATIO_ALLOC": ("KP_RATIO", 6, "数与代数"),
    "KP_RATIO_PROP": ("KP_RATIO", 6, "数与代数"),
    "KP_RATIO_DIRECT_INV": ("KP_RATIO", 6, "数与代数"),
    "KP_RATIO_SCALE": ("KP_RATIO", 6, "数与代数"),
    # ---------------- 数论（7）----------------
    "KP_NT_INTEGER": ("KP_NUMBER_THEORY", 1, "数与代数"),
    "KP_NT_FACTOR": ("KP_NUMBER_THEORY", 5, "数与代数"),
    "KP_NT_PRIME": ("KP_NUMBER_THEORY", 5, "数与代数"),
    "KP_NT_GCD_LCM": ("KP_NUMBER_THEORY", 5, "数与代数"),
    "KP_NT_DIVISIBLE": ("KP_NUMBER_THEORY", 5, "数与代数"),
    "KP_NT_NEGATIVE": ("KP_NUMBER_THEORY", 7, "数与代数"),
    "KP_NT_ROOT": ("KP_NUMBER_THEORY", 8, "数与代数"),
    # ---------------- 几何（7）----------------
    "KP_GEO_PERIMETER": ("KP_GEOMETRY", 3, "图形与几何"),
    "KP_GEO_AREA_PLANE": ("KP_GEOMETRY", 5, "图形与几何"),
    "KP_GEO_CIRCLE": ("KP_GEOMETRY", 6, "图形与几何"),
    "KP_GEO_SURFACE": ("KP_GEOMETRY", 6, "图形与几何"),
    "KP_GEO_VOLUME": ("KP_GEOMETRY", 6, "图形与几何"),
    "KP_GEO_COMPOSITE": ("KP_GEOMETRY", 6, "图形与几何"),
    "KP_GEO_PYTHAGORAS": ("KP_GEOMETRY", 8, "图形与几何"),
    # ---------------- 图形（8）----------------
    "KP_SHAPE_PLANE": ("KP_SHAPE", 1, "图形与几何"),
    "KP_SHAPE_SOLID": ("KP_SHAPE", 4, "图形与几何"),
    "KP_SHAPE_ANGLE": ("KP_SHAPE", 4, "图形与几何"),
    "KP_SHAPE_TRANSFORM": ("KP_SHAPE", 4, "图形与几何"),
    "KP_SHAPE_POSITION": ("KP_SHAPE", 3, "图形与几何"),
    "KP_SHAPE_TRIANGLE": ("KP_SHAPE", 8, "图形与几何"),
    "KP_SHAPE_SIMILAR": ("KP_SHAPE", 9, "图形与几何"),
    "KP_SHAPE_CIRCLE_PROP": ("KP_SHAPE", 6, "图形与几何"),
    # ---------------- 单位（6）----------------
    "KP_UNIT_LENGTH": ("KP_UNIT", 2, "数与代数"),
    "KP_UNIT_AREA_VOL": ("KP_UNIT", 5, "数与代数"),
    "KP_UNIT_MASS": ("KP_UNIT", 3, "数与代数"),
    "KP_UNIT_TIME": ("KP_UNIT", 2, "数与代数"),
    "KP_UNIT_MONEY": ("KP_UNIT", 1, "数与代数"),
    "KP_UNIT_COMPOUND": ("KP_UNIT", 6, "数与代数"),
    # ---------------- 统计（5）----------------
    "KP_STAT_COLLECT": ("KP_STATISTICS", 1, "统计与概率"),
    "KP_STAT_CHART": ("KP_STATISTICS", 3, "统计与概率"),
    "KP_STAT_AVERAGE": ("KP_STATISTICS", 4, "统计与概率"),
    "KP_STAT_PROBABILITY": ("KP_STATISTICS", 5, "统计与概率"),
    "KP_STAT_ANALYSIS": ("KP_STATISTICS", 6, "统计与概率"),
    # ---------------- 综合（5）----------------
    "KP_MIX_PATTERN": ("KP_MIXED", 2, "综合实践"),
    "KP_MIX_LOGIC": ("KP_MIXED", 4, "综合实践"),
    "KP_MIX_OPTIMIZE": ("KP_MIXED", 5, "综合实践"),
    "KP_MIX_FUNCTION": ("KP_MIXED", 8, "综合实践"),
    "KP_MIX_OTHER": ("KP_MIXED", 6, "综合实践"),
}

#: 前置依赖边：(pre_code, post_code, weight, note)。方向：先掌握 pre 再学 post。
#: 主干覆盖 K12 数学教学顺序；弱边用 weight<1 表达「建议前置」而非「硬门槛」。
PREREQUISITES: list[tuple[str, str, float, str]] = [
    # ---- 计算链 ----
    ("KP_NT_INTEGER", "KP_CALC_ADD", 1.0, "认识整数后才能学加减法"),
    ("KP_CALC_ADD", "KP_CALC_MUL", 1.0, "乘法是加法的简便运算"),
    ("KP_CALC_ADD", "KP_CALC_MENTAL", 0.8, "口算以加减法为基础"),
    ("KP_CALC_MUL", "KP_CALC_MIXED_OP", 1.0, "四则混合以乘除为基础"),
    ("KP_CALC_MIXED_OP", "KP_CALC_LAW", 1.0, "运算定律在混合运算中归纳"),
    ("KP_CALC_LAW", "KP_CALC_SIMPLE", 1.0, "简便运算依赖运算定律"),
    ("KP_NT_NEGATIVE", "KP_CALC_RATIONAL", 1.0, "有理数运算先认识负数"),
    ("KP_CALC_RATIONAL", "KP_NT_ROOT", 0.8, "实数域在有理数后扩展"),
    # ---- 数论链 ----
    ("KP_NT_INTEGER", "KP_NT_FACTOR", 1.0, "因数倍数基于整数"),
    ("KP_NT_FACTOR", "KP_NT_PRIME", 1.0, "质数合数是因数的分类"),
    ("KP_NT_PRIME", "KP_NT_GCD_LCM", 1.0, "公因数公倍数需质因数分解"),
    ("KP_NT_FACTOR", "KP_NT_DIVISIBLE", 0.8, "整除性质基于因数倍数"),
    ("KP_NT_INTEGER", "KP_NT_NEGATIVE", 0.9, "负数由整数扩展而来"),
    # ---- 分数链 ----
    ("KP_NT_INTEGER", "KP_FRAC_MEANING", 0.9, "分数基于整数均分产生"),
    ("KP_FRAC_MEANING", "KP_FRAC_PROPERTY", 1.0, "分数的基本性质以意义为基础"),
    ("KP_FRAC_PROPERTY", "KP_FRAC_ADD_SUB", 1.0, "约分通分后才能加减"),
    ("KP_CALC_MUL", "KP_FRAC_MUL_DIV", 0.7, "分数乘除需要整数乘除基础"),
    ("KP_FRAC_MUL_DIV", "KP_FRAC_WORD", 1.0, "分数应用题依赖分数四则"),
    ("KP_CALC_RATIONAL", "KP_FRAC_EXPR", 0.7, "分式是有理数运算的代数的形式"),
    # ---- 小数链 ----
    ("KP_NT_INTEGER", "KP_DEC_MEANING", 0.9, "小数基于十进制计数"),
    ("KP_DEC_MEANING", "KP_DEC_ADD_SUB", 1.0, "小数加减以意义为基础"),
    ("KP_CALC_MUL", "KP_DEC_MUL_DIV", 0.7, "小数乘除基于整数乘除"),
    ("KP_DEC_MUL_DIV", "KP_DEC_APPROX", 1.0, "近似数在小数除法的商的取舍中引入"),
    ("KP_FRAC_PROPERTY", "KP_DEC_CONVERT", 0.8, "分小互化依赖分数基本性质"),
    ("KP_DEC_APPROX", "KP_DEC_CONVERT", 0.7, "循环小数与分数互化"),
    # ---- 百分数链 ----
    ("KP_DEC_CONVERT", "KP_PCT_MEANING", 1.0, "百分数由分小百互化引入"),
    ("KP_PCT_MEANING", "KP_PCT_DISCOUNT", 1.0, "折扣成数是百分数的应用"),
    ("KP_PCT_MEANING", "KP_PCT_CHANGE", 1.0, "增减幅度用百分数表达"),
    ("KP_PCT_CHANGE", "KP_PCT_INTEREST", 1.0, "利率纳税是增减问题的延伸"),
    ("KP_PCT_MEANING", "KP_PCT_WORD", 1.0, "百分数应用题为意义直接应用"),
    # ---- 比与比例链 ----
    ("KP_FRAC_PROPERTY", "KP_RATIO_BASIC", 0.8, "比的性质源于分数基本性质"),
    ("KP_RATIO_BASIC", "KP_RATIO_ALLOC", 1.0, "按比分配依赖比的意义"),
    ("KP_RATIO_BASIC", "KP_RATIO_PROP", 1.0, "比例是相等的比"),
    ("KP_RATIO_PROP", "KP_RATIO_DIRECT_INV", 1.0, "正反比例是比例关系的特例"),
    ("KP_RATIO_PROP", "KP_RATIO_SCALE", 0.9, "比例尺是图上距离的实际比"),
    # ---- 方程链 ----
    ("KP_CALC_MIXED_OP", "KP_EQ_LETTER", 0.9, "用字母表示数需四则基础"),
    ("KP_EQ_LETTER", "KP_EQ_PROPERTY", 1.0, "等式性质用字母表示数"),
    ("KP_EQ_PROPERTY", "KP_EQ_LINEAR1", 1.0, "解一元一次方程用等式性质"),
    ("KP_EQ_LINEAR1", "KP_EQ_SYSTEM", 1.0, "二元一次方程组基于一元一次"),
    ("KP_EQ_LINEAR1", "KP_EQ_QUADRATIC", 0.8, "一元二次升级自一元一次"),
    ("KP_EQ_LINEAR1", "KP_EQ_INEQUALITY", 0.9, "不等式与方程同族"),
    ("KP_EQ_LINEAR1", "KP_EQ_WORD", 1.0, "列方程解应用题需要能解方程"),
    # ---- 应用题链 ----
    ("KP_CALC_MIXED_OP", "KP_WORD_GENERAL", 1.0, "一般应用题以四则运算为基础"),
    ("KP_CALC_MIXED_OP", "KP_WORD_SUM_DIFF", 0.9, "和差倍问题用四则关系"),
    ("KP_WORD_SUM_DIFF", "KP_WORD_AGE", 0.9, "年龄问题本质是和差倍"),
    ("KP_WORD_SUM_DIFF", "KP_WORD_TRAVEL", 0.8, "行程问题涉及和差关系"),
    ("KP_CALC_MUL", "KP_WORD_PLANT", 0.8, "植树间隔问题用除法"),
    ("KP_FRAC_MUL_DIV", "KP_WORD_WORK", 0.8, "工程问题用分数表示效率"),
    ("KP_PCT_CHANGE", "KP_WORD_PRICE", 0.9, "经济问题依赖百分数增减"),
    # ---- 几何链 ----
    ("KP_SHAPE_PLANE", "KP_GEO_PERIMETER", 1.0, "周长建立在平面图形认识之上"),
    ("KP_GEO_PERIMETER", "KP_GEO_AREA_PLANE", 0.8, "面积在周长基础上推进"),
    ("KP_GEO_AREA_PLANE", "KP_GEO_CIRCLE", 0.9, "圆的面积是面积计算的推广"),
    ("KP_GEO_AREA_PLANE", "KP_GEO_COMPOSITE", 0.9, "组合图形拆解为基本图形"),
    ("KP_SHAPE_PLANE", "KP_SHAPE_SOLID", 0.7, "立体图形认识基于平面图形"),
    ("KP_SHAPE_SOLID", "KP_GEO_SURFACE", 1.0, "表面积是立体图形面的展开"),
    ("KP_GEO_AREA_PLANE", "KP_GEO_VOLUME", 0.8, "体积计算常用底面积乘高"),
    ("KP_SHAPE_ANGLE", "KP_SHAPE_TRIANGLE", 1.0, "三角形全等依赖角与线的性质"),
    ("KP_SHAPE_TRIANGLE", "KP_SHAPE_SIMILAR", 0.9, "相似形是全等的推广"),
    ("KP_NT_ROOT", "KP_GEO_PYTHAGORAS", 0.9, "勾股定理计算需开方"),
    ("KP_SHAPE_TRIANGLE", "KP_GEO_PYTHAGORAS", 0.8, "勾股定理在直角三角形中"),
    ("KP_SHAPE_PLANE", "KP_SHAPE_ANGLE", 0.8, "角与线是平面图形的组成要素"),
    ("KP_SHAPE_PLANE", "KP_SHAPE_TRANSFORM", 0.6, "图形变换以图形认识为前提"),
    ("KP_SHAPE_PLANE", "KP_SHAPE_POSITION", 0.6, "位置方向以图形为对象"),
    # ---- 统计概率链 ----
    ("KP_STAT_COLLECT", "KP_STAT_CHART", 1.0, "统计图展示收集整理的数据"),
    ("KP_STAT_CHART", "KP_STAT_AVERAGE", 1.0, "平均数从统计图数据计算"),
    ("KP_STAT_AVERAGE", "KP_STAT_ANALYSIS", 1.0, "数据分析基于统计量"),
    ("KP_FRAC_MEANING", "KP_STAT_PROBABILITY", 0.6, "概率可用分数表示"),
    ("KP_STAT_COLLECT", "KP_STAT_PROBABILITY", 0.6, "概率事件靠数据收集"),
    # ---- 单位链 ----
    ("KP_UNIT_LENGTH", "KP_UNIT_AREA_VOL", 1.0, "面积体积单位由长度单位派生"),
    ("KP_UNIT_LENGTH", "KP_UNIT_COMPOUND", 0.7, "复名数涉及长度换算"),
    ("KP_UNIT_TIME", "KP_UNIT_COMPOUND", 0.7, "复名数涉及时间换算"),
    ("KP_UNIT_MONEY", "KP_UNIT_COMPOUND", 0.7, "复名数涉及货币换算"),
    # ---- 综合链 ----
    ("KP_CALC_MIXED_OP", "KP_MIX_PATTERN", 0.7, "找规律基于运算规律"),
    ("KP_CALC_MIXED_OP", "KP_MIX_LOGIC", 0.5, "逻辑推理可借助运算枚举"),
    ("KP_CALC_MIXED_OP", "KP_MIX_OPTIMIZE", 0.7, "统筹优化需要运算比较"),
    ("KP_EQ_LINEAR1", "KP_MIX_FUNCTION", 0.8, "函数初步由方程关系引入"),
]


def main() -> None:
    with SessionLocal() as s:
        # 1) 载入现有 KP（按 code）
        rows = s.execute(
            select(KnowledgePoint.id, KnowledgePoint.code, KnowledgePoint.name)
        ).all()
        id_by_code = {code: kp_id for kp_id, code, _ in rows if code}
        missing = [code for code in KP_GRAPH if code not in id_by_code]
        if missing:
            print(f"[warn] 库内不存在，跳过: {missing}")
        excepted = set(KP_GRAPH) - set(missing)

        # 2) 校验：L1 计数
        l1_codes = [c for c, (pc, _, _) in KP_GRAPH.items() if pc is None]
        l2_codes = [c for c, (pc, _, _) in KP_GRAPH.items() if pc is not None]
        print(f"[data] 定义 L1={len(l1_codes)} L2={len(l2_codes)} 边={len(PREREQUISITES)}")

        # 3) 更新层级 + 标注（存在才更新）
        updated = 0
        for code in excepted:
            parent_code, grade, domain = KP_GRAPH[code]
            parent_id = id_by_code.get(parent_code) if parent_code else None
            row = s.get(KnowledgePoint, id_by_code[code])
            if row is None:
                continue
            row.parent_id = parent_id
            row.grade = grade
            row.domain = domain
            updated += 1
        s.flush()

        # 4) 重建前置依赖边（先清空再全量写入）
        s.execute(delete(KnowledgePrerequisite))
        edge_added = 0
        for pre_code, post_code, weight, note in PREREQUISITES:
            pre_id = id_by_code.get(pre_code)
            post_id = id_by_code.get(post_code)
            if pre_id is None or post_id is None:
                print(f"[warn] 边端点缺失，跳过: {pre_code} -> {post_code}")
                continue
            if pre_id == post_id:
                print(f"[warn] 自环跳过: {pre_code}")
                continue
            s.add(
                KnowledgePrerequisite(
                    pre_kp_id=pre_id,
                    kp_id=post_id,
                    weight=weight,
                    note=note,
                )
            )
            edge_added += 1
        s.commit()

        # 5) 自检
        l1_cnt = s.execute(
            select(func.count())
            .select_from(KnowledgePoint)
            .where(KnowledgePoint.parent_id.is_(None))
        ).scalar()
        l2_cnt = s.execute(
            select(func.count())
            .select_from(KnowledgePoint)
            .where(KnowledgePoint.parent_id.isnot(None))
        ).scalar()
        edge_cnt = s.execute(select(func.count()).select_from(KnowledgePrerequisite)).scalar()
        print(f"[ok] 更新 KP={updated}  库内 L1={l1_cnt} L2={l2_cnt}  前置边={edge_cnt}")
        assert l1_cnt == 13, f"L1 应为 13，实际 {l1_cnt}"
        assert l2_cnt == 80, f"L2 应为 80，实际 {l2_cnt}"
        assert edge_cnt >= 50, f"边数过少: {edge_cnt}"
        print("[ok] 知识图谱 seed 完成")


if __name__ == "__main__":
    main()