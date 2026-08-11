# PRD · KF 知识树 L2 二级分类方案

- Language: 中文
- Programming Language: Python 3 / FastAPI / SQLAlchemy（后端既有栈，本方案不引入新技术）
- Project Name: kf_knowledge_tree_l2
- 作者: 许清楚（PM）
- 状态: 待架构师裁决（见 §7 待确认问题）

## 0. 原始需求复述

现有知识点体系是 13 个**扁平 L1 粗桶**（KP_CALC / KP_EQUATION / ... / KP_MIXED），
既没有层级也没有学段信息。粒度太粗导致：错题归因只能定位到"计算"这种大类，
掌握度雷达 / 薄弱点推荐无法给出可执行的练习建议。

要求：在**不做数据库迁移**的前提下（`KnowledgePoint` 已自带 `parent_id` 自引用树），
把 13 个粗桶细化为二级结构，总节点数 60–100，覆盖小学 1–6 年级 + 初中数学，
并给出种子数据结构、自动打标（backfill）策略与旧数据处理方案。

---

## 1. 产品目标 + 分类原则

### 1.1 产品目标（3 条，正交）

| # | 目标 | 可衡量标准 |
|---|---|---|
| G1 | **归因可执行**：错题定位到"能直接开练"的最小知识单元 | 自动打标后，落在 L2 的题目占比 ≥ 70%（其余允许停在 L1） |
| G2 | **零迁移落地**：只改种子数据与加载器，不动表结构 | `alembic` 无新增 revision；`seed_db.py` 改动 ≤ 20 行 |
| G3 | **向后兼容**：旧标签不失效、统计不塌方 | 2174 道已有题（909 seed + 1265 docx）的 `kp_id` 全部仍指向合法节点；报表可 rollup 回 13 桶 |

### 1.2 分类原则（5 条）

1. **P1 · L1 冻结**：13 个 L1 的 `code` / `name` / `sort` **一律不改、不删、不并**。
   L1 既是树根，也是自动打标的兜底桶，还是旧数据的落脚点。
   ⚠️ `KnowledgePoint.children` 与 `KnowledgePoint.questions` 都是
   `cascade="all, delete-orphan"`——**删一个 L1 会连带删掉它名下的所有 L2 和所有题目**。
   任何情况下都不许 DELETE L1 节点。
2. **P2 · 只做两层**：树深严格 = 2（L1 → L2），不做 L3。
   L3 的辨析成本高于收益，且会让 UI 钻取层级过深。加载器需校验并拒绝 L3。
3. **P3 · "可独立辨析"才拆**：一个 L2 必须满足——(a) 题面上有稳定的**区分性关键词**
   （能被规则词典命中），(b) 教研上是一个**可独立布置练习**的专项。
   仅靠语感区分、无稳定题面特征的，不拆。
4. **P4 · 小初分治、就近挂靠**：初中内容不另起炉灶，优先挂到语义最近的现有 L1
   （如「一元二次方程」→ KP_EQUATION、「勾股定理」→ KP_GEOMETRY、
   「相似图形」→ KP_SHAPE）。挂靠明显别扭的只有「函数」（见 §7 Q1）。
5. **P5 · 规模可控**：L1 下挂 2–8 个 L2（几何/图形/初中重头戏可到 8），
   **总节点 93 个（13 L1 + 80 L2）**，落在 60–100 区间内。
   宁可某个 L2 稍宽，也不为了覆盖长尾把节点数吹到 200。
6. **P6 · 每个 L1 留一个"兜底子节点"或直接用 L1 兜底**：
   打标命中不到具体 L2 时，允许停在 L1（不强行猜），保证 recall 不掉。

---

## 2. grade 是否进 KP 节点？——**结论：不进**

### 2.1 结论

**知识点树是纯 topic 层级，不携带年级维度。** `grade` 保持在题目侧
（`Question.grade`，语义 = 升入年级，U1 已锁死）作为**正交维度**。
本 PRD 表格中的「适用学段」列**仅作教研参考，不写入 `knowledge_points.json`、不落库**。

### 2.2 理由

1. **同一知识点跨多个年级**。「口算与估算」1–6 年级都考，「平均数」小学四年级出现、
   初中继续用。硬塞 grade 只能存"范围"，而范围一旦落库就会被误当成过滤条件，
   出现"六年级学生看不到三年级的加减法薄弱项"这种反教研的 bug。
2. **维度正交是现成能力，不是缺口**。查询侧已经能做
   `WHERE kp_id = ? AND grade = ?`，二维交叉筛选一条 SQL 解决。
   把 grade 塞进 KP 反而把二维压成一维，丢信息。
3. **零迁移是硬约束**。`KnowledgePoint` 没有 grade 字段，加就要
   `grade_min` / `grade_max` 两列 + 一次 alembic 迁移 + 加载器改造 +
   93 个节点逐个填范围（教研工作量最大的一块）。**估算成本约 0.5–1 人日**，
   而收益（"按年级筛知识点"这一个场景）目前没有明确的产品需求承接。
4. **真要按年级过滤，有零成本替代**：从题目表反查即可——
   "某年级涉及哪些知识点" = `SELECT DISTINCT kp_id FROM questions WHERE grade = ?`。
   数据是活的，比手填的静态范围更准。

### 2.3 若架构师决定要落库（备选方案，不推荐）

需要：`KnowledgePoint` 新增 `grade_min: SmallInteger, nullable=True` /
`grade_max: SmallInteger, nullable=True`（初中用 7/8/9 表示，或单独加 `stage` 枚举），
1 次 alembic 迁移，`seed_db.py` 增 2 行，教研补 93 行数据。
**代价 0.5–1 人日，且引入"范围被误用为过滤器"的长期风险。** 见 §7 Q3。

---

## 3. 二级分类总表（13 L1 + 80 L2 = 93 节点）

### 3.1 读表说明

- `code` **全局唯一、大写、下划线**，长度 ≤ 32（受 `KnowledgePoint.code = String(32)` 限制，已逐个核对）。
- `parentCode`：L1 为 `null`，L2 填其 L1 的 code。
- `sort`：**同层内排序**。L1 沿用现有值（10/20/.../900，不改）；L2 在同一父节点下从 10 起步、步长 10。
  全局展示时排序键 = `(parent.sort, sort)`。
- `适用学段`：**教研参考列，不落库**（见 §2）。

### 3.2 KP_CALC 计算（7 个 L2）

| code | name | parentCode | sort | description | 适用学段 |
|---|---|---|---|---|---|
| KP_CALC | 计算 | null | 10 | 四则运算、口算笔算、简便运算与运算定律 | 1-6年级/初中 |
| KP_CALC_ADD | 加减法 | KP_CALC | 10 | 整数加减法、进位退位与竖式笔算 | 1-3年级 |
| KP_CALC_MUL | 乘除法 | KP_CALC | 20 | 表内乘除、多位数乘除法与有余数除法 | 2-4年级 |
| KP_CALC_MIXED_OP | 四则混合运算 | KP_CALC | 30 | 含小括号中括号的两三步混合运算与运算顺序 | 3-5年级 |
| KP_CALC_LAW | 运算定律 | KP_CALC | 40 | 交换律、结合律、分配律的辨识与字母表达 | 4年级 |
| KP_CALC_SIMPLE | 简便运算 | KP_CALC | 50 | 凑整、拆分、提取公因数等简算技巧 | 4-6年级 |
| KP_CALC_MENTAL | 口算与估算 | KP_CALC | 60 | 口算笔算训练、估算与近似计算 | 1-6年级 |
| KP_CALC_RATIONAL | 有理数运算 | KP_CALC | 70 | 有理数加减乘除、乘方与混合运算 | 初中 |

### 3.3 KP_EQUATION 方程（7 个 L2）

| code | name | parentCode | sort | description | 适用学段 |
|---|---|---|---|---|---|
| KP_EQUATION | 方程 | null | 20 | 用字母表示数、解方程与列方程解决问题 | 5-6年级/初中 |
| KP_EQ_LETTER | 用字母表示数 | KP_EQUATION | 10 | 字母表示数量关系、代数式书写与求值 | 5年级 |
| KP_EQ_PROPERTY | 等式性质 | KP_EQUATION | 20 | 等式基本性质、移项与同解变形 | 5年级/初中 |
| KP_EQ_LINEAR1 | 一元一次方程 | KP_EQUATION | 30 | 一元一次方程的解法、去分母去括号与检验 | 5年级/初中 |
| KP_EQ_SYSTEM | 二元一次方程组 | KP_EQUATION | 40 | 代入消元法、加减消元法与方程组应用 | 初中 |
| KP_EQ_QUADRATIC | 一元二次方程 | KP_EQUATION | 50 | 配方法、公式法、因式分解法与根的判别式 | 初中 |
| KP_EQ_INEQUALITY | 不等式 | KP_EQUATION | 60 | 一元一次不等式(组)的解集与数轴表示 | 初中 |
| KP_EQ_WORD | 列方程解应用题 | KP_EQUATION | 70 | 设未知数建模，行程/工程/浓度等方程应用 | 5-6年级/初中 |

### 3.4 KP_WORD 应用题（7 个 L2）

| code | name | parentCode | sort | description | 适用学段 |
|---|---|---|---|---|---|
| KP_WORD | 应用题 | null | 30 | 文字情境题，考察审题与数量关系建模 | 1-6年级/初中 |
| KP_WORD_SUM_DIFF | 和差倍问题 | KP_WORD | 10 | 和差、和倍、差倍关系的经典模型 | 2-4年级 |
| KP_WORD_TRAVEL | 行程问题 | KP_WORD | 20 | 相遇、追及、往返，速度时间路程三量关系 | 4-6年级/初中 |
| KP_WORD_WORK | 工程问题 | KP_WORD | 30 | 工作总量、工作效率与合作完成时间 | 6年级/初中 |
| KP_WORD_PRICE | 经济问题 | KP_WORD | 40 | 单价数量总价、成本利润与盈亏问题 | 3-6年级 |
| KP_WORD_AGE | 年龄问题 | KP_WORD | 50 | 年龄差不变、几年后几年前类问题 | 4-6年级 |
| KP_WORD_PLANT | 植树与间隔问题 | KP_WORD | 60 | 植树、锯木、上楼、方阵等间隔计数问题 | 4-5年级 |
| KP_WORD_GENERAL | 一般应用题 | KP_WORD | 70 | 未归入专项模型的常规文字应用题（兜底） | 1-6年级 |

### 3.5 KP_FRACTION 分数（6 个 L2）

| code | name | parentCode | sort | description | 适用学段 |
|---|---|---|---|---|---|
| KP_FRACTION | 分数 | null | 40 | 分数的意义、性质、四则运算与分数应用 | 3-6年级/初中 |
| KP_FRAC_MEANING | 分数的意义 | KP_FRACTION | 10 | 分数单位、真分数假分数带分数与分数意义 | 3-5年级 |
| KP_FRAC_PROPERTY | 分数基本性质 | KP_FRACTION | 20 | 约分、通分与分数大小比较 | 5年级 |
| KP_FRAC_ADD_SUB | 分数加减法 | KP_FRACTION | 30 | 同分母与异分母分数加减、带分数加减 | 5年级 |
| KP_FRAC_MUL_DIV | 分数乘除法 | KP_FRACTION | 40 | 分数乘法除法法则与倒数 | 6年级 |
| KP_FRAC_WORD | 分数应用题 | KP_FRACTION | 50 | 求一个数的几分之几、已知部分求整体 | 6年级 |
| KP_FRAC_EXPR | 分式 | KP_FRACTION | 60 | 分式的意义、约分通分、分式运算与分式方程 | 初中 |

### 3.6 KP_DECIMAL 小数（5 个 L2）

| code | name | parentCode | sort | description | 适用学段 |
|---|---|---|---|---|---|
| KP_DECIMAL | 小数 | null | 50 | 小数的意义、性质与小数四则运算 | 3-6年级 |
| KP_DEC_MEANING | 小数的意义 | KP_DECIMAL | 10 | 小数数位、读写、大小比较与小数性质 | 3-4年级 |
| KP_DEC_ADD_SUB | 小数加减法 | KP_DECIMAL | 20 | 小数点对齐的加减竖式计算 | 4年级 |
| KP_DEC_MUL_DIV | 小数乘除法 | KP_DECIMAL | 30 | 小数乘除法法则、小数点移动与商的近似值 | 5年级 |
| KP_DEC_APPROX | 近似数与循环小数 | KP_DECIMAL | 40 | 四舍五入、精确到、有限小数与循环小数 | 4-5年级 |
| KP_DEC_CONVERT | 分小百互化 | KP_DECIMAL | 50 | 分数小数百分数三者互化与大小比较 | 5-6年级 |

### 3.7 KP_PERCENT 百分数（5 个 L2）

| code | name | parentCode | sort | description | 适用学段 |
|---|---|---|---|---|---|
| KP_PERCENT | 百分数 | null | 60 | 百分数意义、折扣税率利率与百分数应用 | 6年级 |
| KP_PCT_MEANING | 百分数的意义 | KP_PERCENT | 10 | 百分数读写意义与合格率出勤率等常见率 | 6年级 |
| KP_PCT_DISCOUNT | 折扣与成数 | KP_PERCENT | 20 | 打折、成数、原价现价与省钱问题 | 6年级 |
| KP_PCT_INTEREST | 利率与纳税 | KP_PERCENT | 30 | 本金利率利息、税率与应纳税额 | 6年级 |
| KP_PCT_CHANGE | 百分数增减 | KP_PERCENT | 40 | 增加百分之几、减少百分之几与增长率 | 6年级/初中 |
| KP_PCT_WORD | 百分数应用题 | KP_PERCENT | 50 | 综合百分数情境问题（兜底） | 6年级 |

### 3.8 KP_RATIO 比例（5 个 L2）

| code | name | parentCode | sort | description | 适用学段 |
|---|---|---|---|---|---|
| KP_RATIO | 比例 | null | 70 | 比、比例、正反比例与按比分配 | 6年级/初中 |
| KP_RATIO_BASIC | 比的意义与化简 | KP_RATIO | 10 | 比、比值、前项后项与化简比 | 6年级 |
| KP_RATIO_ALLOC | 按比分配 | KP_RATIO | 20 | 按给定比分配总量的问题 | 6年级 |
| KP_RATIO_PROP | 比例的意义与解比例 | KP_RATIO | 30 | 比例的基本性质、内项外项与解比例 | 6年级 |
| KP_RATIO_DIRECT_INV | 正比例与反比例 | KP_RATIO | 40 | 正反比例关系的判断、图像与应用 | 6年级/初中 |
| KP_RATIO_SCALE | 比例尺 | KP_RATIO | 50 | 图上距离、实际距离与比例尺换算 | 6年级 |

### 3.9 KP_GEOMETRY 几何（7 个 L2，度量计算向）

| code | name | parentCode | sort | description | 适用学段 |
|---|---|---|---|---|---|
| KP_GEOMETRY | 几何 | null | 80 | 周长面积体积等度量计算 | 3-6年级/初中 |
| KP_GEO_PERIMETER | 周长 | KP_GEOMETRY | 10 | 长方形正方形三角形等平面图形周长 | 3-5年级 |
| KP_GEO_AREA_PLANE | 平面图形面积 | KP_GEOMETRY | 20 | 长方形正方形平行四边形三角形梯形面积 | 3-5年级 |
| KP_GEO_CIRCLE | 圆的周长与面积 | KP_GEOMETRY | 30 | 圆周率、圆的周长面积、扇形与圆环 | 6年级 |
| KP_GEO_SURFACE | 立体图形表面积 | KP_GEOMETRY | 40 | 长方体正方体圆柱表面积与展开图 | 5-6年级 |
| KP_GEO_VOLUME | 体积与容积 | KP_GEOMETRY | 50 | 长方体正方体圆柱圆锥体积容积与排水法 | 5-6年级 |
| KP_GEO_COMPOSITE | 组合图形 | KP_GEOMETRY | 60 | 组合图形面积、阴影部分与割补转化 | 5-6年级 |
| KP_GEO_PYTHAGORAS | 勾股定理 | KP_GEOMETRY | 70 | 直角三角形三边关系、逆定理与实际应用 | 初中 |

### 3.10 KP_SHAPE 图形（8 个 L2，认识/位置/变换向）

| code | name | parentCode | sort | description | 适用学段 |
|---|---|---|---|---|---|
| KP_SHAPE | 图形 | null | 90 | 图形的认识、位置与变换、观察物体 | 1-6年级/初中 |
| KP_SHAPE_PLANE | 平面图形的认识 | KP_SHAPE | 10 | 三角形四边形圆等平面图形的特征与分类 | 1-5年级 |
| KP_SHAPE_SOLID | 立体图形的认识 | KP_SHAPE | 20 | 长方体正方体圆柱圆锥特征与观察物体 | 1-6年级 |
| KP_SHAPE_ANGLE | 角与线 | KP_SHAPE | 30 | 线段射线直线、锐角直角钝角、角的度量、平行与垂直 | 2-4年级/初中 |
| KP_SHAPE_TRANSFORM | 平移旋转与对称 | KP_SHAPE | 40 | 轴对称、平移、旋转与图形变换 | 2-6年级 |
| KP_SHAPE_POSITION | 位置与方向 | KP_SHAPE | 50 | 上下左右前后、方向与距离、数对与平面直角坐标系 | 1-6年级/初中 |
| KP_SHAPE_TRIANGLE | 三角形与全等 | KP_SHAPE | 60 | 三角形三边关系内角和、全等三角形判定与性质 | 4年级/初中 |
| KP_SHAPE_SIMILAR | 相似图形 | KP_SHAPE | 70 | 比例线段、相似三角形判定性质与位似 | 初中 |
| KP_SHAPE_CIRCLE_PROP | 圆的性质 | KP_SHAPE | 80 | 垂径定理、圆心角圆周角、切线与位置关系 | 初中 |

### 3.11 KP_STATISTICS 统计（5 个 L2）

| code | name | parentCode | sort | description | 适用学段 |
|---|---|---|---|---|---|
| KP_STATISTICS | 统计 | null | 100 | 数据收集整理、统计图表与平均数 | 1-6年级/初中 |
| KP_STAT_COLLECT | 数据收集与整理 | KP_STATISTICS | 10 | 分类计数、调查记录与统计表 | 1-4年级 |
| KP_STAT_CHART | 统计图 | KP_STATISTICS | 20 | 条形图、折线图、扇形图的读图与绘制 | 3-6年级 |
| KP_STAT_AVERAGE | 平均数与统计量 | KP_STATISTICS | 30 | 平均数、中位数、众数与方差 | 4-6年级/初中 |
| KP_STAT_PROBABILITY | 可能性与概率 | KP_STATISTICS | 40 | 事件可能性大小判断与简单概率计算 | 4-6年级/初中 |
| KP_STAT_ANALYSIS | 数据分析与决策 | KP_STATISTICS | 50 | 依据统计结果做判断、预测与方案选择 | 5-6年级 |

### 3.12 KP_UNIT 单位换算（6 个 L2）

| code | name | parentCode | sort | description | 适用学段 |
|---|---|---|---|---|---|
| KP_UNIT | 单位换算 | null | 110 | 长度重量面积体积时间货币单位换算 | 1-6年级 |
| KP_UNIT_LENGTH | 长度单位 | KP_UNIT | 10 | 毫米厘米分米米千米的认识与换算 | 1-4年级 |
| KP_UNIT_AREA_VOL | 面积与体积单位 | KP_UNIT | 20 | 平方厘米平方米公顷、立方厘米立方米升毫升换算 | 3-6年级 |
| KP_UNIT_MASS | 质量单位 | KP_UNIT | 30 | 克千克吨的认识与换算 | 2-4年级 |
| KP_UNIT_TIME | 时间单位 | KP_UNIT | 40 | 时分秒、年月日与经过时间计算 | 1-3年级 |
| KP_UNIT_MONEY | 人民币与货币 | KP_UNIT | 50 | 元角分的认识与购物找零计算 | 1-2年级 |
| KP_UNIT_COMPOUND | 复名数与单位综合 | KP_UNIT | 60 | 复名数改写与跨类单位综合换算（兜底） | 3-6年级 |

### 3.13 KP_NUMBER_THEORY 数论（7 个 L2）

| code | name | parentCode | sort | description | 适用学段 |
|---|---|---|---|---|---|
| KP_NUMBER_THEORY | 数论 | null | 120 | 因数倍数、质数合数、公因数公倍数 | 1-6年级/初中 |
| KP_NT_INTEGER | 整数的认识 | KP_NUMBER_THEORY | 10 | 数位顺序、读写、大小比较、改写与近似数 | 1-4年级 |
| KP_NT_FACTOR | 因数与倍数 | KP_NUMBER_THEORY | 20 | 因数倍数的概念与找法 | 5年级 |
| KP_NT_PRIME | 质数与合数 | KP_NUMBER_THEORY | 30 | 质数合数的判断与分解质因数 | 5年级 |
| KP_NT_GCD_LCM | 公因数与公倍数 | KP_NUMBER_THEORY | 40 | 最大公因数、最小公倍数及其应用 | 5年级 |
| KP_NT_DIVISIBLE | 整除与奇偶 | KP_NUMBER_THEORY | 50 | 2/3/5 的整除特征、奇数偶数与整除性质 | 5年级 |
| KP_NT_NEGATIVE | 负数与有理数 | KP_NUMBER_THEORY | 60 | 负数意义、数轴、相反数与绝对值 | 6年级/初中 |
| KP_NT_ROOT | 平方根与实数 | KP_NUMBER_THEORY | 70 | 平方根立方根、无理数与实数分类 | 初中 |

### 3.14 KP_MIXED 综合（5 个 L2）

| code | name | parentCode | sort | description | 适用学段 |
|---|---|---|---|---|---|
| KP_MIXED | 综合 | null | 900 | 跨知识点综合题与思维拓展题 | 全学段 |
| KP_MIX_PATTERN | 找规律 | KP_MIXED | 10 | 数列规律、图形规律与周期问题 | 1-6年级 |
| KP_MIX_LOGIC | 逻辑推理 | KP_MIXED | 20 | 推理判断、排除法与信息整理 | 3-6年级 |
| KP_MIX_OPTIMIZE | 优化与统筹 | KP_MIXED | 30 | 烙饼排队等统筹问题、最优方案与鸡兔同笼 | 4-6年级 |
| KP_MIX_FUNCTION | 函数初步 | KP_MIXED | 40 | 变量关系、一次函数与反比例函数的图像性质（**挂靠存疑，见 Q1**） | 初中 |
| KP_MIX_OTHER | 其他综合 | KP_MIXED | 50 | 跨知识点综合题与未归类拓展题（全局兜底） | 全学段 |

### 3.15 节点数汇总

| L1 | L2 数 |
|---|---|
| KP_CALC | 7 |
| KP_EQUATION | 7 |
| KP_WORD | 7 |
| KP_FRACTION | 6 |
| KP_DECIMAL | 5 |
| KP_PERCENT | 5 |
| KP_RATIO | 5 |
| KP_GEOMETRY | 7 |
| KP_SHAPE | 8 |
| KP_STATISTICS | 5 |
| KP_UNIT | 6 |
| KP_NUMBER_THEORY | 7 |
| KP_MIXED | 5 |
| **合计** | **L1 13 + L2 80 = 93** |

> code 唯一性已逐项核对：13 个 L2 前缀（`KP_CALC_` / `KP_EQ_` / `KP_WORD_` / `KP_FRAC_` /
> `KP_DEC_` / `KP_PCT_` / `KP_RATIO_` / `KP_GEO_` / `KP_SHAPE_` / `KP_STAT_` /
> `KP_UNIT_` / `KP_NT_` / `KP_MIX_`）互不重叠，最长 code 为
> `KP_SHAPE_CIRCLE_PROP`（20 字符）与 `KP_RATIO_DIRECT_INV`（19 字符），均 < 32。

---

## 4. knowledge_points.json 目标结构

### 4.1 结论：只加一个字段 `parentCode`

| 字段 | 类型 | 说明 | 是否新增 |
|---|---|---|---|
| `code` | string | 全局唯一编码，大写下划线，≤32 字符 | 已有 |
| `name` | string | 节点名，≤64 字符 | 已有 |
| `sort` | int | **同层内**排序权重 | 已有（语义细化：从全局排序变为同层排序） |
| `description` | string | 一行描述，会进方舟兜底 prompt，写得越准分类越准 | 已有 |
| `parentCode` | string \| null | **新增**。L1 固定为 `null`；L2 填父 L1 的 code | ✅ 新增 |

**不加 `level` 字段**——层级可由 `parentCode` 是否为 null 推导，多存一份就多一处会不一致的真相源。
**不加 `stage` / `grade` 字段**——见 §2 结论。

### 4.2 完整 JSON 示例（L1 + L2）

```json
{
  "version": "2.0.0",
  "knowledgePoints": [
    {
      "code": "KP_CALC",
      "name": "计算",
      "parentCode": null,
      "sort": 10,
      "description": "四则运算、口算笔算、简便运算与运算定律"
    },
    {
      "code": "KP_CALC_SIMPLE",
      "name": "简便运算",
      "parentCode": "KP_CALC",
      "sort": 50,
      "description": "凑整、拆分、提取公因数等简算技巧"
    },
    {
      "code": "KP_EQ_LINEAR1",
      "name": "一元一次方程",
      "parentCode": "KP_EQUATION",
      "sort": 30,
      "description": "一元一次方程的解法、去分母去括号与检验"
    }
  ]
}
```

> 建议同时把顶层 `version` 从无 → `"2.0.0"`，方便加载器和运维识别种子数据代次。

### 4.3 加载器（seed_db.py）需要的改动

现状：`seed_db.py:57-66` 直接 `s.merge(KnowledgePoint(id/code/name/description/sort))`，
**没有设 `parent_id`**。改动点（预计 ≤ 20 行）：

1. **两趟写入**：先写 `parentCode is None` 的 L1，`s.flush()`，再写 L2。
   （`id` 是 `uuid5(NS, "aicoach:kp:" + code)` 的确定性值，
   所以 `parent_id = kp_uuid(parentCode)` 可以直接算出来，
   但为了满足外键约束，父行必须先落库。）
2. **校验三条**（任一不过就 fail fast，别让脏数据静默入库）：
   - `parentCode` 必须存在于本文件的 code 集合中；
   - `parentCode != code`（禁自环）；
   - 父节点自身的 `parentCode` 必须为 `null`（**禁 L3**，对应原则 P2）。
3. **幂等**：仍用 `s.merge()` + 确定性 uuid5，重复执行不产生重复节点；
   已存在的节点会被更新 `parent_id`（这正是从 v1 平铺升级到 v2 树的迁移路径——**不需要 alembic**）。

伪代码（供架构师参考，实现由工程师定）：

```python
kp_raw = load_json("knowledge_points.json")["knowledgePoints"]
codes = {kp["code"] for kp in kp_raw}

# 校验
for kp in kp_raw:
    pc = kp.get("parentCode")
    if pc is None:
        continue
    assert pc in codes, f"parentCode 不存在: {kp['code']} -> {pc}"
    assert pc != kp["code"], f"自环: {kp['code']}"

parent_of = {kp["code"]: kp.get("parentCode") for kp in kp_raw}
for kp in kp_raw:
    pc = parent_of[kp["code"]]
    assert pc is None or parent_of[pc] is None, f"树深超过 2 层: {kp['code']}"

# 两趟：L1 先落库，再落 L2
for pass_l1 in (True, False):
    for kp in kp_raw:
        if (kp.get("parentCode") is None) != pass_l1:
            continue
        s.merge(KnowledgePoint(
            id=kp_uuid(kp["code"]),
            code=kp["code"],
            name=kp["name"],
            description=kp.get("description"),
            sort=kp.get("sort", 0),
            parent_id=kp_uuid(kp["parentCode"]) if kp.get("parentCode") else None,
        ))
    s.flush()
```

---

## 5. 回填（backfill）策略：kp_keywords.json 从 13 桶扩到 L2

### 5.1 现状与最小改动原则

`backfill_kp.py:101-157` 的 `classify_by_rules()` 逻辑是：
统计每条规则的命中词数 → 按 `(priority desc, hits desc)` 排序 →
Top1 严格优于 Top2 才返回，否则返回 `None` 交方舟兜底。

**这套逻辑不用改代码就能支持 L2**——只要用 `priority` 数值分层，让 L2 天然压过 L1。

### 5.2 词典结构：L1 兜底 + L2 覆盖（更具体者胜）

`kp_keywords.json` 从 13 条规则扩到约 93 条：

| 层 | 规则数 | priority 区间 | 角色 |
|---|---|---|---|
| L1 | 13（沿用现有，priority 不改：0–30） | 0 – 30 | **兜底**。L2 全不命中时，保证至少落到粗桶，recall 不退化 |
| L2 | 80（新增） | 100 – 199 | **覆盖**。只要有 L2 命中，`priority` 必然高于任何 L1，直接胜出 |

L2 内部再拉梯度（同一父下，越"窄"的越高），避免两个兄弟 L2 同分导致
`classify_by_rules` 返回 `None`：
- 专有名词型（比例尺、勾股定理、鸡兔同笼）：`priority = 180`
- 明确运算型（分数乘除法、小数加减法）：`priority = 150`
- 宽泛型 / 兜底子节点（一般应用题、其他综合）：`priority = 110`

### 5.3 关键词编写规范（**教研必须遵守**）

1. **L2 只写区分性词，不写父桶的通用词。**
   反例：`KP_GEO_SURFACE` 和 `KP_GEO_VOLUME` 都写「圆柱」→ 两者同分 → 返回 None → 白白掉进方舟。
   正解：`KP_GEO_SURFACE` 写「表面积/侧面积/展开图/贴纸/涂漆」，
   `KP_GEO_VOLUME` 写「体积/容积/容纳/排水/浸没」，「圆柱」留在 L1。
2. **优先用多字长词**，`in` 是子串匹配，「和」「差」「积」「商」这种单字会疯狂误命中
   （现有 `KP_CALC` 词表里就有，属遗留问题，本次一并收紧）。
3. 每个 L2 建议 6–12 个关键词，宁缺毋滥。

### 5.4 三个具体示例

**示例 1 —— "解方程：3x + 5 = 20"**

| 命中规则 | 层 | priority | hits |
|---|---|---|---|
| KP_EQUATION（方程/解方程） | L1 | 20 | 2 |
| KP_EQ_LINEAR1（解方程/一元一次方程/设x/未知数） | L2 | 150 | 1 |

→ 排序后 Top1 = `KP_EQ_LINEAR1`（priority 150 > 20），**结果：KP_EQ_LINEAR1** ✅

**示例 2 —— "用简便方法计算：25 × 32 × 125"**

| 命中规则 | 层 | priority | hits |
|---|---|---|---|
| KP_CALC（乘/简便运算） | L1 | 10 | 2 |
| KP_CALC_SIMPLE（简便方法/简便运算/凑整/拆分） | L2 | 150 | 2 |
| KP_CALC_MUL（乘法/乘） | L2 | 150 | 1 |

→ `KP_CALC_SIMPLE` 与 `KP_CALC_MUL` priority 同为 150，但 hits 2 > 1，
Top1 严格优于 Top2，**结果：KP_CALC_SIMPLE** ✅
（若把「乘」这种单字词从 `KP_CALC_MUL` 删掉，会更干净——见 §5.3 规范 2。）

**示例 3 —— "在比例尺为 1:5000 的地图上，量得两地图上距离 8 厘米，求实际距离"**

| 命中规则 | 层 | priority | hits |
|---|---|---|---|
| KP_RATIO（比例/比例尺） | L1 | 25 | 2 |
| KP_UNIT（厘米） | L1 | 30 | 1 |
| KP_RATIO_SCALE（比例尺/图上距离/实际距离/地图） | L2 | 180 | 4 |

→ **结果：KP_RATIO_SCALE** ✅
注意这题在旧的 13 桶方案里会被 `KP_UNIT`（priority 30）抢走误判成"单位换算"，
L2 分层顺带修掉了这个存量 bug。

### 5.5 方舟兜底需要配套调整（给架构师的实现提醒）

1. **两段式选项，别把 93 个选项一次塞给模型。**
   `classify_by_ark()` 现在把全部 KP 列成封闭选项。93 选 1 会让 prompt 变长、准确率下降。
   建议改成：第一段 13 选 1 选出 L1 → 第二段在该 L1 的 `children` 里选 L2（≤8 选 1）。
   两次调用，每次选项都很短。
2. **修掉编码模糊匹配的前缀歧义（必做）。**
   `backfill_kp.py:221-223` 用 `if code in reply` 做模糊匹配。
   L2 上线后 `KP_CALC` 是 `KP_CALC_ADD` 的**前缀**，模型返回
   `"KP_CALC_ADD"` 会被 `KP_CALC` 抢先匹配（`valid_codes` 是 set，遍历顺序不确定
   → **结果不可复现**）。修法：改为**最长匹配优先**
   （`sorted(valid_codes, key=len, reverse=True)`）。
   同类前缀对还有 `KP_WORD` / `KP_RATIO` / `KP_SHAPE` / `KP_UNIT`。

---

## 6. 已有数据处理

### 6.1 现状盘点

- **909 道 seed 题 + 1265 道 docx 题**，`kp_id` 目前指向 13 个 L1 之一，或为空。
- `Mistake.kp_id` 从 `Question.kp_id` 继承（`backfill_kp.py:309-318`）。

### 6.2 处理原则：不失效、可细化、幂等

1. **旧标签一律不失效。** L1 节点在树里继续作为根存在，`kp_id` 指向它们完全合法。
   升级后即使一次 backfill 都不跑，系统也能正常工作——只是粒度还停在 L1。
2. **建议重跑 backfill 拿细度。** 但注意——**现有脚本重跑抓不到这批题**：
   `backfill_kp.py:259` 的筛选条件是 `WHERE Question.kp_id IS NULL`，
   已经打到 L1 的题**不会被重新处理**。
   → 建议给脚本加 `--relabel-l1` 开关，把筛选条件扩展为：
   ```sql
   WHERE kp_id IS NULL
      OR (kp_id IN (13 个 L1 的 id) AND kp_verified = 0)
   ```
3. **红线：绝不覆盖 `kp_verified = 1`。** 人工确认过的归因是可归因池的地基，
   任何自动流程都不许动。
4. **幂等性**：规则层是纯确定性字符串匹配，同样输入必然同样输出，可无限重跑。
   方舟兜底层非确定性——建议 `--relabel-l1` 模式下**只跑规则层**（加 `--no-ark`），
   方舟单独跑一轮处理剩余空值，避免每次重跑都产生抖动。
5. **错题表同步**：现有二次回填 SQL 是 `WHERE mistakes.kp_id IS NULL`，
   重打标后 `questions.kp_id` 升级到 L2 而 `mistakes.kp_id` 仍停在 L1，两边会漂移。
   建议扩展为"当 `mistakes.kp_id` 是 L1 且该错题未人工确认时，同步为题目的新 kp_id"。
   （具体 SQL 与是否需要 `mistakes.kp_verified` 字段，交架构师判断。）

### 6.3 产品侧重要提醒：统计口径要 rollup

桶从 13 个变成 93 个后，**单桶样本量骤降约 6 倍**。
掌握度雷达图、薄弱知识点 Top N 这类功能如果直接按 `kp_id` 分组，
会出现"每个知识点只有 1–2 道题"的稀疏噪声，推荐结果不可信。

**建议**：
- 概览/雷达图层：按 **L1 聚合**（`COALESCE(kp.parent_id, kp.id)` 作为分组键），维持 13 桶视图；
- 详情/钻取层：点开某个 L1 才展开它的 L2 明细；
- 练习推荐：L2 样本数 ≥ 5 才用 L2 出题，否则回退到 L1 出题。

### 6.4 上线步骤（建议）

1. 更新 `knowledge_points.json`（93 条，带 `parentCode`）
2. 改 `seed_db.py` 加载器（两趟 + 三条校验）
3. 跑 `python app/scripts/seed_db.py`（**不加 `--reset`**，靠 merge 幂等升级；
   `--reset` 会 drop 全库，生产禁用）
4. 校验：`SELECT COUNT(*) FROM knowledge_points` = 93；`parent_id IS NOT NULL` = 80
5. 更新 `kp_keywords.json`（93 条规则）
6. `python app/scripts/backfill_kp.py --dry-run --relabel-l1` 看归因分布
7. 分布合理后去掉 `--dry-run` 正式落库

---

## 7. 待确认问题（交架构师裁决）

| # | 问题 | PM 建议 | 影响面 |
|---|---|---|---|
| **Q1** | **初中代数是否单独成 L1？** 当前方案把「函数初步」硬塞进 `KP_MIXED`、把「不等式」「一元二次方程」塞进 `KP_EQUATION`，语义偏挤。可选新增 2 个 L1：`KP_ALGEBRA`（代数式/整式/因式分解，目前**完全没覆盖**）+ `KP_FUNCTION`（函数）。 | **建议新增**。L1 从 13 → 15，总节点 93 → 约 100（`KP_MIX_FUNCTION` 迁出并拆为 3–4 个函数 L2）。代价只是种子数据，仍**无需迁移**；收益是初中题归因不再挤在"综合"里。若判定初中优先级低，则维持现状。 | 节点表、词典；不涉及代码 |
| **Q2** | **`--relabel-l1` 要不要做？** 不做的话，2174 道旧题永远停在 L1 粒度，L2 只对未来新题生效。 | **建议做**，且只跑规则层（`--no-ark`）保证幂等。工作量约 20 行。 | `backfill_kp.py` |
| **Q3** | **年级范围要不要落库？** PM 结论是不落（§2）。 | **建议不落**。若要落需加 `grade_min`/`grade_max` 两列 + 1 次 alembic 迁移 + 93 行教研数据，**约 0.5–1 人日**，且当前没有承接需求。 | `content.py` 模型 + 迁移 |
| **Q4** | **`KP_MIXED` 是否细分？** 当前拆了 5 个子节点（找规律/逻辑推理/优化统筹/函数初步/其他综合）。 | **建议保留细分**。「找规律」「鸡兔同笼」在青岛版衔接卷里是高频专项，值得单列；`KP_MIX_OTHER` 承担全局兜底。若 Q1 通过，`KP_MIX_FUNCTION` 迁出，剩 4 个。 | 节点表 |
| **Q5** | **统计口径是否统一改为 L1 rollup？**（§6.3） | **建议改**，且这是 L2 上线的**前置条件**——不改的话上线当天掌握度报表就会因样本稀疏而失真。 | 报表/推荐相关 service |

**Top 3 优先级**：Q1（决定节点表最终形态）> Q2（决定旧数据能否吃到细度）> Q5（决定上线是否会打脸报表）。

