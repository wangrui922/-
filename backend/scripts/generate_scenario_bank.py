# -*- coding: utf-8 -*-
"""生成生活场景题（方向六 · 场景题库），幂等落库 questions/answers（方向七口径）。

四个场景（scenario_type）：
  shopping  购物算账
  cake      分蛋糕
  time      时间规划
  distance  路程计算

约束（与现有骨架对齐）：
  - C7 判分锁 L1：题型必须是「选择」（本题库 choices），选项 JSON {"A":..,"B":..,"C":..,"D":..}
  - 答案格存 "选X"（可被 judge_engine.extract_choice_key 解析）
  - grade / difficulty 枚举沿用 {1,3,5}；speed 挑战每局 20 题（SPEED_QUESTION_COUNT），
    故每个 (scenario, difficulty) 至少生成 24 题，留出余量
  - 幂等：以 content 判重，可安全重跑

用法：
    python backend/scripts/generate_scenario_bank.py backend/dev.db
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

SCENARIOS = {
    "shopping": "购物算账",
    "cake": "分蛋糕",
    "time": "时间规划",
    "distance": "路程计算",
}
GRADES = [3, 4, 5]  # 生活场景题主投放小学中年级
DIFFICULTIES = [1, 3, 5]
TARGET_PER_CELL = 24  # (scenario, grade, difficulty) 每题池目标


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ 模板（参数 -> (题干, 正确答案文本, 干扰项列表)）

def _fmt_num(v: float) -> str:
    if v == int(v):
        return str(int(v))
    return f"{v:.1f}"


def t_shopping(cell: int, rng: random.Random):
    kind = cell % 6
    if kind == 0:
        # 买 Y 个单价 Z 的商品，付 X 元，剩多少
        price = rng.randint(2, 20)
        count = rng.randint(2, 9)
        paid = price * count + rng.randint(1, 30)
        ans = paid - price * count
        return (
            f"小明带了{paid}元去文具店，买了{count}支笔，每支{price}元，还剩多少元？",
            str(ans),
            [str(ans - 1) if ans > 1 else str(ans + 1), str(ans + 5), str(price * count)],
        )
    if kind == 1:
        # A 件 x 元 + B 件 y 元，共多少元
        a, b = rng.randint(2, 8), rng.randint(2, 8)
        x, y = rng.randint(1, 20), rng.randint(1, 20)
        ans = a * x + b * y
        return (
            f"妈妈买了{a}千克苹果，每千克{x}元，又买了{b}千克香蕉，每千克{y}元，一共花了多少元？",
            str(ans),
            [str(ans - a), str(ans - b), str(a + b)],
        )
    if kind == 2:
        # 单价比较：X 元买了 Y 个，每个多少元（确保整除）
        unit = rng.randint(2, 12)
        count = rng.randint(2, 9)
        total = unit * count
        return (
            f"小华用{total}元买了{count}本同样的练习本，每本练习本多少元？",
            str(unit),
            [str(unit + 1), str(unit - 1) if unit > 1 else str(unit + 2), str(total // 10 or 1)],
        )
    if kind == 3:
        a = rng.randint(2, 9)
        b = rng.randint(2, 9)
        if a == b:
            a += 1
        x = rng.randint(1, 25)
        y = rng.randint(1, 25)
        ans = a * x + b * y
        return (
            f"超市促销：牛奶{a}元/盒，面包{b}元/个。小明买了{x}盒牛奶和{y}个面包，一共要付多少元？",
            str(ans),
            [str(x + y), str(a * b), str(ans - x if ans > x else ans + 8)],
        )
    if kind == 4:
        # 满减：满 X 减 Y，原价 Z（Z>X）
        threshold = rng.randint(50, 90)
        discount = rng.randint(5, 20)
        price = threshold + rng.randint(1, 30)
        ans = price - discount
        return (
            f"商场促销：购物满{threshold}元立减{discount}元。小红买了{price}元的衣服，实际应付多少元？",
            str(ans),
            [str(price), str(price + discount), str(price - threshold)],
        )
    # kind == 5：找零组合
    price = rng.randint(5, 40)
    count = rng.randint(2, 6)
    total = price * count
    paid = (total // 100 + 1) * 100
    ans = paid - total
    return (
        f"小刚买了{count}个玩具，每个{price}元。他付给收银员{paid}元，应找回多少元？",
        str(ans),
        [str(paid - price), str(price * count), str(total % 100 or 100)],
    )


def t_cake(cell: int, rng: random.Random):
    kind = cell % 6
    if kind == 0:
        # X 人平分 Y 块（整除）
        kids = rng.randint(2, 9)
        each = rng.randint(1, 6)
        total = kids * each
        return (
            f"老师把{total}块蛋糕平均分给{kids}个小朋友，每个小朋友分到几块？",
            str(each),
            [str(each + 1), str(each - 1) if each > 1 else str(each + 2), str(total + 1)],
        )
    if kind == 1:
        a = rng.randint(2, 9)
        b = rng.randint(2, 9)
        return (
            f"把{a}个西瓜平均分成{b}份，每份是多少？",
            f"{a}/{b}" if a % b else str(a // b),
            [_fmt_num(a / b + 0.5), str(a + b), "1"],
        )
    if kind == 2:
        kids = rng.randint(2, 8)
        each = rng.randint(2, 7)
        ans = kids * each
        return (
            f"每个小朋友分到{each}块饼干，{kids}个小朋友一共分到多少块饼干？",
            str(ans),
            [str(ans + kids), str(ans - each) if ans > each else str(ans + 1), str(kids + each)],
        )
    if kind == 3:
        total = rng.randint(20, 60)
        kids = rng.randint(2, 8)
        each = rng.randint(1, 5)
        left = total - kids * each
        if left < 0:
            total = kids * each + rng.randint(0, 10)
            left = total - kids * each
        return (
            f"妈妈做了{total}块小蛋糕，平均分给{kids}个小朋友，每人{each}块后还剩几块？",
            str(left),
            [str(left + each), str(left - 1) if left > 1 else str(left + 1), str(kids)],
        )
    if kind == 4:
        each = rng.randint(2, 8)
        pieces = rng.randint(2, 6)
        ans = each * pieces
        return (
            f"每个蛋糕切成{pieces}块，{each}个蛋糕一共能切成多少块？",
            str(ans),
            [str(ans - pieces) if ans > pieces else str(ans + 1), str(each + pieces), str(each * 2)],
        )
    # kind == 5：每人几块（带余）
    kids = rng.randint(3, 9)
    each = rng.randint(3, 8)
    total = kids * each + rng.randint(1, kids - 1)
    ans = each
    return (
        f"{kids}个小朋友平均分{total}块巧克力，平均每人最多分到几块？（剩下不足一块不分）",
        str(ans),
        [str(ans + 1), str(ans - 1) if ans > 1 else str(ans + 2), str(total - kids * (ans - 1))],
    )


def _hm_label(minutes: int) -> str:
    return f"{minutes // 60}点{str(minutes % 60).zfill(2)}分"


def t_time(cell: int, rng: random.Random):
    kind = cell % 6
    if kind == 0:
        # 开始时刻 + 时长 -> 结束
        start_h = rng.randint(8, 19)
        start_m = rng.choice([0, 10, 15, 20, 30, 40, 45, 50])
        dur = rng.choice([30, 40, 45, 60, 75, 90, 100])
        start = start_h * 60 + start_m
        end = start + dur
        ans = f"{end // 60}时{str(end % 60).zfill(2)}分"
        return (
            f"电影{start_h}时{str(start_m).zfill(2)}分开始，放映{dur}分钟，大约几点结束？",
            ans,
            [f"{start_h}时{str(start_m).zfill(2)}分", f"{end // 60}时{str(start_m).zfill(2)}分",
             f"{end // 60 + 1}时00分" if end % 60 >= 30 else f"{end // 60}时15分"],
        )
    if kind == 1:
        # 出发时刻 + 时长 -> 到达
        start_h = rng.randint(6, 17)
        start_m = rng.choice([0, 5, 10, 20, 30, 40])
        dur = rng.choice([10, 15, 20, 25, 30, 35, 40, 45])
        start = start_h * 60 + start_m
        end = start + dur
        ans = f"{end // 60}时{str(end % 60).zfill(2)}分"
        return (
            f"小明{start_h}时{str(start_m).zfill(2)}分出发去学校，路上要走{dur}分钟，他几时几分到校？",
            ans,
            [f"{start_h}时{str(start_m + dur).zfill(2)}分", f"{end // 60}时{str(start_m).zfill(2)}分",
             f"{end // 60}时{str(end % 60 + 5).zfill(2)}分" if end % 60 < 55 else "次日0时00分"],
        )
    if kind == 2:
        lessons = rng.randint(3, 6)
        per = rng.choice([40, 45])
        ans = lessons * per
        return (
            f"一节课{per}分钟，{lessons}节课一共多少分钟？",
            str(ans),
            [str(ans + per), str(ans - per) if ans > per else str(per), str(lessons + per)],
        )
    if kind == 3:
        hours = rng.randint(1, 8)
        ans = hours * 60
        return (
            f"{hours}小时等于多少分钟？",
            str(ans),
            [str(ans - 60), str(ans + 60), str(ans // 10 or 1)],
        )
    if kind == 4:
        total = rng.choice([60, 90, 120, 150, 180])
        done = rng.randint(0, total)
        left = total - done
        return (
            f"作业一共需要{total}分钟，小丽已经写了{done}分钟，还剩多少分钟写完？",
            str(left),
            [str(left + 5), str(left - 5) if left > 5 else str(left + 10), str(done)],
        )
    # kind == 5：跨小时时刻差
    h1 = rng.randint(8, 12)
    h2 = rng.randint(h1 + 1, h1 + 4)
    m1 = rng.choice([0, 15, 30, 45])
    m2 = rng.choice([0, 15, 30, 45])
    ans_m = (h2 * 60 + m2) - (h1 * 60 + m1)
    return (
        f"从{h1}时{str(m1).zfill(2)}分到{h2}时{str(m2).zfill(2)}分，经过了多长时间？",
        str(ans_m) + "分钟",
        [str(ans_m - 30) + "分钟", str(ans_m + 30) + "分钟", str(ans_m * 2) + "分钟"],
    )


def t_distance(cell: int, rng: random.Random):
    kind = cell % 6
    if kind == 0:
        speed = rng.choice([10, 12, 15, 20, 25, 30, 40, 50, 60, 80])
        time = rng.randint(1, 6)
        ans = speed * time
        return (
            f"汽车每小时行驶{speed}千米，行驶{time}小时，一共行驶多少千米？",
            str(ans),
            [str(ans + speed), str(ans - speed) if ans > speed else str(ans + 1), str(speed + time)],
        )
    if kind == 1:
        speed = rng.choice([20, 25, 30, 40, 50])
        dist = speed * rng.choice([2, 3, 4, 5])
        ans = dist // speed
        return (
            f"一辆货车每小时行{speed}千米，行驶{dist}千米需要几小时？",
            str(ans),
            [str(ans + 1), str(ans - 1) if ans > 1 else str(ans + 2), str(dist)],
        )
    if kind == 2:
        meters = rng.choice([300, 450, 600, 750, 900, 1200])
        minutes = rng.choice([5, 6, 10, 15])
        ans = meters // minutes
        return (
            f"小宇{minutes}分钟走了{meters}米，他平均每分钟走多少米？",
            str(ans),
            [str(ans + 10), str(ans - 10) if ans > 10 else str(ans + 20), str(meters * minutes // 100)],
        )
    if kind == 3:
        km = rng.randint(1, 20)
        ans = km * 1000
        return (
            f"{km}千米等于多少米？",
            str(ans),
            [str(ans - 1000), str(ans + 1000), str(ans // 1000 + 100)],
        )
    if kind == 4:
        speed = rng.choice([15, 20, 25, 30, 40])
        time = rng.choice([1.5, 2, 2.5, 3, 4])
        ans = speed * time
        ans_txt = _fmt_num(ans)
        return (
            f"自行车每小时行{speed}千米，骑{_fmt_num(time)}小时，一共能行多少千米？",
            ans_txt,
            [f"{_fmt_num(ans - speed)}", f"{_fmt_num(ans + speed)}", f"{_fmt_num(ans / 2)}"],
        )
    # kind == 5：两段路程和
    a = rng.randint(2, 9)
    b = rng.randint(2, 9)
    ans = a + b
    return (
        f"小丽先走了{a}千米，又走了{b}千米，一共走了多少千米？",
        str(ans),
        [str(ans + 1), str(ans - 1) if ans > 1 else str(ans + 2), str(a)],
    )


def t_distance2(cell: int, rng: random.Random):
    kind = cell % 6
    if kind == 0:
        speed = rng.choice([10, 12, 15, 20, 25, 30, 40, 50, 60, 80])
        time = rng.randint(1, 6)
        ans = speed * time
        return (
            f"火车每小时行驶{speed}千米，行驶{time}小时，一共行驶多少千米？",
            str(ans),
            [str(ans + speed), str(ans - speed) if ans > speed else str(ans + 1), str(speed + time)],
        )
    if kind == 1:
        speed = rng.choice([20, 25, 30, 40, 50])
        dist = speed * rng.choice([2, 3, 4, 5])
        ans = dist // speed
        return (
            f"一辆汽车每小时行{speed}千米，行驶{dist}千米需要几小时？",
            str(ans),
            [str(ans + 1), str(ans - 1) if ans > 1 else str(ans + 2), str(dist)],
        )
    if kind == 2:
        meters = rng.choice([240, 360, 480, 600, 720, 840, 960, 1080])
        minutes = rng.choice([4, 6, 8, 12])
        ans = meters // minutes
        return (
            f"小芳{minutes}分钟走了{meters}米，她平均每分钟走多少米？",
            str(ans),
            [str(ans + 10), str(ans - 10) if ans > 10 else str(ans + 20), str(meters * minutes // 100)],
        )
    if kind == 3:
        km = rng.randint(1, 15)
        m = km * 1000 + rng.choice([0, 500])
        return (
            f"{km}千米{rng.choice(['', '500米'])}等于多少米？",
            str(m),
            [str(m - 1000), str(m + 1000), str(m // 1000 + 100)],
        )
    if kind == 4:
        a = rng.randint(1, 9)
        b = rng.randint(1, 9)
        ans = a * 1000 + b * 100
        return (
            f"小明家到学校{a}千米{b}百米，等于多少米？",
            str(ans),
            [str(ans - 100), str(ans + 100), str((a + b) * 1000)],
        )
    # kind == 5：平均速度
    dist = rng.choice([120, 180, 240, 300, 360, 480])
    hours = rng.choice([2, 3, 4, 6])
    ans = dist // hours
    return (
        f"一辆客车{hours}小时行了{dist}千米，平均每小时行多少千米？",
        str(ans),
        [str(ans + 10), str(ans - 10) if ans > 10 else str(ans + 20), str(dist * 2)],
    )


def t_distance_dual(cell: int, rng: random.Random):
    """distance 场景双套模板交替（cell 奇偶切换），扩大参数空间避免重复。"""
    if cell % 2 == 0:
        return t_distance(cell // 2, rng)
    return t_distance2((cell - 1) // 2, rng)


TEMPLATES = {
    "shopping": t_shopping,
    "cake": t_cake,
    "time": t_time,
    "distance": t_distance_dual,
}


def _options(correct: str, distractors: list[str]) -> str:
    """4 项选择，正确答案与干扰项内部均去重后随机排布。"""
    pool: list[str] = []
    seen: set[str] = set()
    for d in [correct] + distractors:
        d = d.strip()
        if d and d not in seen:
            seen.add(d)
            pool.append(d)
    keys = ["A", "B", "C", "D"]
    items = list(zip(keys, pool[:4]))
    extra_i = len(pool) + 1
    while len(items) < 4:  # 干扰项不足时补齐（extra_i 递增，避免死循环）
        extra = str(extra_i)
        extra_i += 1
        if extra not in [t for _, t in items]:
            items.append((keys[len(items)], extra))
    rng = random.Random(hash((correct, tuple(pool))) & 0xFFFF)
    rng.shuffle(items)
    return json.dumps({k: (v if v != correct else correct) for k, v in items}, ensure_ascii=False)


def _correct_key(options_json: str, correct: str) -> str:
    opts = json.loads(options_json)
    for k, v in opts.items():
        if v == correct:
            return k
    return "A"


def build_items() -> list[dict]:
    items: list[dict] = []
    used: set[str] = set()  # 全局题干去重，避免课内/跨格重复
    for scene, _label in SCENARIOS.items():
        gen = TEMPLATES[scene]
        for grade in GRADES:
            for difficulty in DIFFICULTIES:
                rng = random.Random(f"{scene}-{grade}-{difficulty}")
                cell = 0
                attempts = 0
                cell_count = lambda: sum(  # noqa: E731
                    1
                    for it in items
                    if it["question"]["scenario_type"] == scene
                    and it["question"]["grade"] == grade
                    and it["question"]["difficulty"] == difficulty
                )
                while cell_count() < TARGET_PER_CELL and attempts < 600:
                    content, ans, dis = gen(cell, rng)
                    attempts += 1
                    if content in used:
                        # 该组合已生成过：换一个 kind 再试，避免同格内重复
                        cell += 1
                        continue
                    cell += 1
                    used.add(content)
                    qid = str(uuid.uuid4())
                    opts = _options(ans, dis)
                    key = _correct_key(opts, ans)
                    items.append(
                        {
                            "question": {
                                "id": qid,
                                "content": content,
                                "subject": "数学",
                                "grade": grade,
                                "difficulty": difficulty,
                                "type": "选择",
                                "options": opts,
                                "status": "active",
                                "kp_verified": 1,
                                "source_tag": "green",
                                "ship_ready": 1,
                                "scenario_type": scene,
                                "scenario_template": f"{scene}/{cell % 6}",
                                "scenario_params": json.dumps({"variant": cell}, ensure_ascii=False),
                            },
                            "answer": {
                                "question_id": qid,
                                "correct_answer": f"选{key}",
                                "analysis": f"生活场景·{SCENARIOS[scene]}。答案：{ans}",
                                "distractor_analysis": json.dumps(dis, ensure_ascii=False),
                            },
                        }
                    )
    return items


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: generate_scenario_bank.py <db>")
        return 2
    db_path = sys.argv[1]
    items = build_items()
    print(f"[gen] total={len(items)}")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()
    existing = {r[0] for r in cur.execute("SELECT content FROM questions").fetchall()}
    now = utcnow()
    inserted_q = inserted_a = skipped = 0

    for it in items:
        q = it["question"]
        a = it["answer"]
        if q["content"] in existing:
            skipped += 1
            continue
        cur.execute(
            """INSERT OR IGNORE INTO questions
               (id, content, image_url, subject, grade, difficulty, kp_id, type,
                options, status, edition, source_type, section, source_doc,
                kp_verified, source_tag, ship_ready, created_at,
                scenario_type, scenario_template, scenario_params)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                q["id"], q["content"], None, q["subject"], q["grade"], q["difficulty"],
                None, q["type"], q["options"], q["status"], None, "scenario_bank",
                None, "scenario_bank", q["kp_verified"], q["source_tag"], q["ship_ready"],
                now, q["scenario_type"], q["scenario_template"], q["scenario_params"],
            ),
        )
        if cur.rowcount == 0:
            skipped += 1
            continue
        cur.execute(
            """INSERT OR REPLACE INTO answers
               (id, question_id, correct_answer, analysis, distractor_analysis)
               VALUES (?,?,?,?,?)""",
            (str(uuid.uuid4()), a["question_id"], a["correct_answer"], a["analysis"],
             a["distractor_analysis"]),
        )
        inserted_q += 1
        inserted_a += 1
        existing.add(q["content"])

    conn.commit()

    # 校验：每 (scenario, grade, difficulty) 可用题数 ≥ 20
    audit = cur.execute(
        """SELECT scenario_type, grade, difficulty, COUNT(*)
           FROM questions
           WHERE scenario_type IS NOT NULL AND status='active' AND ship_ready=1
           GROUP BY scenario_type, grade, difficulty
           ORDER BY scenario_type, grade, difficulty"""
    ).fetchall()
    print("[audit] scenario cells (scenario,grade,difficulty,count):")
    for row in audit:
        mark = "OK" if row[3] >= 20 else "SHORT"
        print(f"  {row[0]} g{row[1]} d{row[2]} -> {row[3]} {mark}")
    orphan = cur.execute(
        """SELECT COUNT(*) FROM questions q LEFT JOIN answers a ON a.question_id=q.id
           WHERE q.scenario_type IS NOT NULL AND a.id IS NULL"""
    ).fetchone()[0]
    print(f"[done] inserted questions={inserted_q} answers={inserted_a} skipped={skipped} orphan={orphan}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())