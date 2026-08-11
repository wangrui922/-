# Runbook · KF 知识树 L2 上线跑批

> 项目: `kf_knowledge_tree_l2`  
> 架构师: 高见远  
> 关联设计文档: `docs/design-kf-tree-l2.md`  
> 适用环境: 开发库 SQLite (`backend/dev.db`)；生产 PG 见末尾"生产环境差异"

---

## 0. 前置条件

- [x] T01–T04 代码已合并，`tests/test_kp_tree_seed.py` 25 项全绿
- [x] `tests/test_backfill_kp.py` 全绿（93 节点 L2 适配版）
- [x] `backend/seed/knowledge_points.json` v2.0.0（93 节点）
- [x] `backend/seed/kp_keywords.json` v2.0.0（93 条规则）
- [x] 后端进程已停止（避免 WAL 写入干扰备份）

---

## 1. 红线提示（执行前必读）

| # | 红线 | 原因 |
|---|---|---|
| R1 | **UUID5 公式不可改** | `kp_uuid(code) = uuid5(NAMESPACE_DNS, "aicoach:kp:"+code)` 改了 = 13 个 L1 的 id 全变 = 2174 道题 `kp_id` 外键集体悬空 |
| R2 | **禁止 `DELETE FROM knowledge_points WHERE parent_id IS NOT NULL`** | `KnowledgePoint.children` 和 `.questions` 是 `cascade="all, delete-orphan"`，ORM 层删 L2 会连带删掉挂在它下面的题目 |
| R3 | **回滚只能用备份覆盖** | 见上，不能删行；停后端 → `cp dev.db.bak-YYYYMMDD dev.db` → 启后端 |
| R4 | **`seed_db.py` 禁加 `--reset`** | `--reset` 会 `drop_all + create_all`，清空全库（含 2174 道题） |
| R5 | **`--relabel-l1` 隐含 `--no-ark`** | 规则层是确定性字符串匹配；方舟是 LLM（非确定性）。重打标带方舟会导致每次跑批结果抖动、掌握度曲线跳变 |
| R6 | **`report_service.py` 不要碰** | Q5 rollup 的收敛点是 `profile_service.py:_kp_ids_of()`，改 report_service 会二次聚合出错 |

---

## 2. 执行步骤（严格按序）

### Step 1 · WAL checkpoint + 冷备份

> ⚠️ dev.db 是 WAL 模式（有 `-wal` / `-shm` 伴生文件），直接 copy `dev.db` 会丢未 checkpoint 的数据。

```bash
cd D:/制作网页/cuotixiaolaoshi/backend
./.venv/Scripts/python.exe -c "
import sqlite3, shutil, datetime
db = sqlite3.connect('dev.db')
db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
db.close()
bak = f'dev.db.bak-{datetime.datetime.now().strftime(\"%Y%m%d%H%M\")}'
shutil.copy2('dev.db', bak)
print(f'backup: {bak}')
"
```

**验证**: 输出 `backup: dev.db.bak-YYYYMMDDHHMM`，文件存在且大小 > 0。

### Step 2 · 加载 93 节点种子数据

```bash
./.venv/Scripts/python.exe -m app.scripts.seed_db
```

**期望输出包含**:
- `知识点=93 (L1=13 / L2=80)`
- `parent_id IS NOT NULL` 计数 = 80

**幂等性**: `merge()` 按 PK (`uuid5`) upsert，重跑 N 次结果与跑 1 次完全一致。13 个 L1 的 id 不变 → 2174 道题外键不断。

### Step 3 · 回标（纯规则层）

```bash
./.venv/Scripts/python.exe -m app.scripts.backfill_kp --relabel-l1 --no-ark
```

**说明**:
- `--relabel-l1`：把 `kp_id IS NULL` 或（`kp_id IN 13个L1` 且 `kp_verified=0`）的题一并重打标
- `--no-ark`：只跑规则层（93 条关键词词典），不调方舟，保证幂等可复现
- 归因结果一律 `kp_verified=0`（不污染可归因池）
- 末尾自动同步 `mistakes.kp_id`（design §4.3）

**期望输出**: 归因分布报告，含 L1/L2 命中数、双口径验收数字。

### Step 4 · 验证（6 条 SQL）

依次执行以下 SQL，确认每条结果符合预期。

```bash
./.venv/Scripts/python.exe -c "
import sqlite3
db = sqlite3.connect('dev.db')
cur = db.cursor()

# ① 节点数：期望 total=93, l1=13, l2=80
cur.execute('''
SELECT count(*) AS total,
       sum(CASE WHEN parent_id IS NULL THEN 1 ELSE 0 END) AS l1,
       sum(CASE WHEN parent_id IS NOT NULL THEN 1 ELSE 0 END) AS l2
FROM knowledge_points
''')
print('① 节点数:', cur.fetchone())

# ② 禁 L3：期望 0 行
cur.execute('''
SELECT count(*) FROM knowledge_points c
JOIN knowledge_points p ON c.parent_id = p.id
WHERE p.parent_id IS NOT NULL
''')
print('② 禁L3 (期望0):', cur.fetchone()[0])

# ③ 存量题外键完好：期望 0（无悬空 kp_id）
cur.execute('''
SELECT count(*) FROM questions q
WHERE q.kp_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM knowledge_points k WHERE k.id = q.kp_id)
''')
print('③ 悬空外键 (期望0):', cur.fetchone()[0])

# ④ L2 占比
cur.execute('''
SELECT sum(CASE WHEN k.parent_id IS NOT NULL THEN 1 ELSE 0 END) * 1.0 / count(*) AS l2_ratio
FROM questions q JOIN knowledge_points k ON k.id = q.kp_id
''')
print('④ L2占比:', round(cur.fetchone()[0], 4))

# ⑤ 归因安全：kp_verified=1 的题数（前后不变）
cur.execute('SELECT count(*) FROM questions WHERE kp_verified = 1')
print('⑤ kp_verified=1 题数:', cur.fetchone()[0])

# ⑥ rollup 后桶数：期望 <= 13
cur.execute('''
SELECT count(DISTINCT COALESCE(k.parent_id, k.id)) AS l1_buckets
FROM questions q JOIN knowledge_points k ON k.id = q.kp_id
''')
print('⑥ rollup桶数 (期望<=13):', cur.fetchone()[0])

db.close()
"
```

**验证标准**:
- ① total=93, l1=13, l2=80
- ② = 0
- ③ = 0
- ④ L2 占比 ≥ 50%（纯规则层，不含方舟兜底）
- ⑤ 与回标前完全相同（人工复核过的题绝不被覆盖）
- ⑥ ≤ 13

### Step 5 · 按 L1 聚合练习题数（P0 修复验证）

> 这一步验证 R3 修复：backfill 后题目已挂 L2，但按 L1 请求练习仍能抽到题。

```bash
./.venv/Scripts/python.exe -c "
import sqlite3
db = sqlite3.connect('dev.db')
cur = db.cursor()
cur.execute('''
SELECT k1.code AS l1_code, k1.name AS l1_name, count(q.id) AS question_count
FROM knowledge_points k1
LEFT JOIN knowledge_points k2 ON k2.parent_id = k1.id
LEFT JOIN questions q ON q.kp_id IN (k1.id, k2.id)
WHERE k1.parent_id IS NULL
GROUP BY k1.code, k1.name
ORDER BY count(q.id) DESC
''')
print('L1编码            L1名称      题数')
print('-' * 50)
for row in cur.fetchall():
    print(f'{row[0]:20s} {row[1]:10s} {row[2]:5d}')
db.close()
"
```

**验证标准**: 每个 L1 下的题数 > 0（特别是 `KP_CALC` 等主要桶）。

### Step 6 · 跑全部测试

```bash
cd D:/制作网页/cuotixiaolaoshi/backend

# 全量测试（排除 backfill 先跑，再单独跑）
./.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_backfill_kp.py
./.venv/Scripts/python.exe -m pytest tests/test_backfill_kp.py -q
./.venv/Scripts/python.exe -m pytest tests/test_kp_tree_seed.py -q
```

**验证标准**: 全绿。如果 `test_backfill_kp.py` 有失败，继续修到全绿。

---

## 3. 回滚方案

> ⚠️ **禁止** `DELETE FROM knowledge_points WHERE parent_id IS NOT NULL`
> （cascade 会删题！）

回滚步骤：

1. **停后端进程**
2. **用备份覆盖**:
   ```bash
   cd D:/制作网页/cuotixiaolaoshi/backend
   cp dev.db.bak-YYYYMMDDHHMM dev.db
   # 如果有 -wal / -shm 文件也一并删除
   rm -f dev.db-wal dev.db-shm
   ```
3. **启后端**，验证:
   - `SELECT count(*) FROM knowledge_points` = 13（回到 L1 平铺）
   - `SELECT count(*) FROM questions WHERE kp_id IS NOT NULL` 与备份前一致

---

## 4. 生产环境差异

| 项 | 开发环境 | 生产环境 |
|---|---|---|
| 数据库 | SQLite (`dev.db`) | PostgreSQL (`DATABASE_URL`) |
| 备份 | `PRAGMA wal_checkpoint(TRUNCATE)` + `cp` | `pg_dump` |
| 回滚 | `cp dev.db.bak-YYYYMMDD dev.db` | `pg_restore` |
| seed | `python -m app.scripts.seed_db` | 相同（ORM merge 幂等） |
| backfill | 相同命令 | 相同命令 |
| 验证 SQL | 相同（dialect-agnostic） | 相同 |

**生产环境额外注意**:
- backfill 跑批前通知前端观察练习页是否白屏（R3 是唯一线上事故风险点）
- 方舟兜底（第 2 轮 `python -m app.scripts.backfill_kp`）是 LLM 调用，注意限流和超时
- 建议在低峰期执行

---

## 5. 故障排查

| 症状 | 可能原因 | 处理 |
|---|---|---|
| seed_db 报 `AssertionError: parentCode 不存在` | `knowledge_points.json` 损坏或版本不对 | 检查文件 version=2.0.0，重新从 git 恢复 |
| seed_db 报 `parent_id IS NOT NULL` 计数 ≠ 80 | L1 未先 flush，L2 的 parent_id 找不到父行 | 确认 seed_db.py 两趟写入逻辑未被改坏 |
| backfill 后练习页白屏 | R3 未上线（practice_service 未改 subtree_ids） | 确认 `practice_service.py:225` 已改为 `kp_id.in_(subtree_ids(kp_id))` |
| backfill 后雷达图 93 桶 | R1 未上线（profile_service 未改 rollup） | 确认 `profile_service.py:_kp_ids_of()` 已改为 `rollup(kp_id)` |
| `kp_verified=1` 题数变化 | relabel_l1 误选了已复核题 | 立即回滚；检查 `_select_targets` 的 `kp_verified IS FALSE` 条件 |
| 方舟调用超时 | 网络/Key 问题 | `--no-ark` 模式不受影响；方舟失败自动降级为 unmatched |

---

## 6. 附录 · mistakes 同步 SQL

```sql
-- design §4.3：题目侧 kp_verified=0 才允许覆盖错题快照
UPDATE mistakes
SET kp_id = (SELECT q.kp_id FROM questions q WHERE q.id = mistakes.question_id)
WHERE mistakes.kp_id IS NULL
   OR EXISTS (
        SELECT 1 FROM questions q
        WHERE q.id = mistakes.question_id
          AND q.kp_verified = 0
          AND q.kp_id IS NOT NULL
          AND mistakes.kp_id <> q.kp_id
   );
```

不加 `mistakes.kp_verified` 列（加列 = 违反零迁移红线）。
权威源头是 `questions.kp_verified`，用 EXISTS 子查询挂到题目侧。
