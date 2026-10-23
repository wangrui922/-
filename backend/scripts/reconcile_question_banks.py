"""C05 三库对账：生产（93 KP）vs dev.db（4166 题）vs Aug-19 快照。

正确基准：**生产 KP 体系是 93 个**。dev.db 后来扩到 328 KP，
两套体系只有部分交集。判据：
- dev 题的 kp_id 在生产 93 KP 里 → 可合并（挂得上知识点）
- dev 题的 kp_id 不在生产 / 为空 → 合并后 KP 悬空或无法路由 → 不合并
- 快照有而生产没有的题（且 KP 属生产体系）→ 生产流失候选
"""
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import os

DEV = Path(os.environ.get("C05_DEV_DB", "F:/错题教练/_wt/integrate/backend/dev.db"))
SNAP = Path(os.environ.get("C05_SNAP_DB", "F:/错题教练/_wt/_snapshot_20260819_dev.db"))
PROD_FP = Path(os.environ.get("C05_PROD_FP", "F:/错题教练/.qa_cache/prod_fp.json"))


def main() -> int:
    prod = json.loads(PROD_FP.read_text(encoding="utf-8"))
    prod_kps = set(prod["kps"])
    prod_prints = Counter(prod["prints"])
    dev = sqlite3.connect(f"file:{DEV}?mode=ro", uri=True)
    snap = sqlite3.connect(f"file:{SNAP}?mode=ro", uri=True)

    print("=" * 68)
    print("C05 题库对账报告")
    print("=" * 68)
    print(f"生产：{len(prod_kps)} KP / {sum(prod_prints.values())} 题")
    print(f"dev.db：{dev.execute('select count(*) from questions').fetchone()[0]} 题 / "
          f"{dev.execute('select count(*) from knowledge_points').fetchone()[0]} KP")
    print(f"Aug-19 快照：{snap.execute('select count(*) from questions').fetchone()[0]} 题 / "
          f"{snap.execute('select count(*) from knowledge_points').fetchone()[0]} KP")

    # ---- 一、dev → 生产 可合并性
    print()
    print("一、dev.db 题目对生产体系的可合并性")
    rows = dev.execute("select kp_id, count(*) from questions group by kp_id").fetchall()
    can, cannot_kp, cannot_null = 0, 0, 0
    orphan_kps = []
    for kp, n in rows:
        if kp is None:
            cannot_null += n
        elif kp in prod_kps:
            can += n
        else:
            cannot_kp += n
            orphan_kps.append((kp, n))
    print(f"  可合并（KP 属生产 93）：{can} 题")
    print(f"  KP 悬空（dev 独有 KP）：{cannot_kp} 题（{len(orphan_kps)} 个 KP）")
    print(f"  无 KP（无法路由）：{cannot_null} 题")
    # 可合并题里有多少内容已在生产（去重后净增）
    dev_can_prints = [
        r[0] for r in dev.execute(
            "select substr(content,1,100) from questions where kp_id is not null"
        )
    ]
    dev_kp_set = {r[0] for r in dev.execute("select id from knowledge_points")}
    mergeable_prints = []
    for r in dev.execute("select kp_id, substr(content,1,100) from questions"):
        if r[0] in prod_kps:
            mergeable_prints.append(r[1])
    net_new = [p for p in mergeable_prints if p not in prod_prints]
    dup = len(mergeable_prints) - len(net_new)
    print(f"  其中内容已在生产：{dup} 题（重复）→ 净增 {len(net_new)} 题")

    # ---- 二、快照 → 生产 流失候选
    print()
    print("二、Aug-19 快照 vs 生产（历史流失）")
    snap_kp_rows = snap.execute("select kp_id, substr(content,1,100) from questions").fetchall()
    lost = [(kp, p) for kp, p in snap_kp_rows if kp in prod_kps and p not in prod_prints]
    lost_other = [(kp, p) for kp, p in snap_kp_rows if p not in prod_prints]
    print(f"  快照题不在生产：{len(lost_other)} 题；其中 KP 属生产体系：{len(lost)} 题")
    kp_counter = Counter(kp for kp, _ in lost)
    names = {r[0]: r[1] for r in snap.execute("select id, name from knowledge_points")}
    print("  流失题按 KP Top5：")
    for kp, n in kp_counter.most_common(5):
        print(f"    [{n} 题] {names.get(kp, kp[:8])}")

    # ---- 三、生产 KP 覆盖（补题目标）
    print()
    print("三、生产 KP 覆盖与补题目标")
    cov = {}
    for kp, n in snap.execute("select kp_id, count(*) from questions where kp_id is not null group by kp_id"):
        if kp in prod_kps:
            cov[kp] = cov.get(kp, 0) + n
    for p, n in prod_prints.items():
        pass  # 内容级已在上面处理
    # 用 dev+快照两库估计生产各 KP 可达题量
    reach = {}
    for kp, n in dev.execute("select kp_id, count(*) from questions where kp_id is not null group by kp_id"):
        if kp in prod_kps:
            reach[kp] = reach.get(kp, 0) + n
    thin = sorted(((kp, reach.get(kp, 0)) for kp in prod_kps), key=lambda x: x[1])
    empty = [kp for kp, n in thin if n == 0]
    few = [(kp, n) for kp, n in thin if 0 < n < 5]
    print(f"  生产 {len(prod_kps)} KP 中：本地两库可提供题目为 0 的 {len(empty)} 个，1-4 题的 {len(few)} 个")
    prod_names = prod["kp_names"]
    for kp, n in few[:12]:
        print(f"    [{n} 题] {prod_names.get(kp, kp[:8])}")

    print()
    print("=" * 68)
    print("结论")
    print("=" * 68)
    print(f"  1) dev→生产可安全合并净增 {len(net_new)} 题（KP 挂生产体系 + 内容不重复）")
    print(f"  2) 快照→生产流失候选 {len(lost)} 题（KP 属生产体系）")
    print(f"  3) 生产有 {len(empty)} 个 KP 在本地两库零题量 + {len(few)} 个 KP 题量<5 → 补题目标")
    dev.close()
    snap.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
