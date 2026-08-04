# AI数学教练 V4.1 · 工程任务分解清单

> 架构师：高见远 ｜ 上游：`docs/v4-architecture.md`（选型/目录/6Agent）、`docs/v4-db-api.md`（11 表 DDL / API 清单 / Agent 协议）
> 本文**不重写架构与数据库**，只给可直接开工的任务分解。表结构一律以 v4-db-api.md §A 为准，接口以 §B 为准。

## 0. 老板拍板落地 + 架构师补充裁定

| 项 | 结论 | 对工程的影响 |
|---|---|---|
| 学段 | 全小学 1-6 | `grade CHECK 1-6`；初中 16 题入库置 `status='archived'` |
| 定价 | 不定价 | 无 orders/subscriptions；限流阈值走**常量** `FREE_DAILY_AI=20` |
| 合规 | 无监护人弹窗 | T02 注册流程直连，省 1 页 |
| 家长端 | Web H5 | 独立 `parent-web/`，非小程序 |
| Redis | 不上 | 限流用 `attempts`/`ai_sessions` 的 `count(*) where created_at>今日0点`；短期记忆用 `ai_messages` 倒排 N 条 |
| 对象存储 | 本地 FS | `backend/storage/` + `/api/file/{key}` 签名下发；`STORAGE_DRIVER=local\|cos` 一个开关 |
| Agent | 在线4+离线2 | 在线在 `app/ai/orchestrator.py`；离线用 **APScheduler**（无 Redis 不用 Celery） |
| **补充裁定①** | **前端 Vue3 + Vite + TS + Tailwind**（v4-db-api.md 末"老板指定 Vue3" > 架构初稿 React）；后台用 **Element Plus** 替代 MUI（MUI 仅 React） | 三应用独立仓目录，非 pnpm monorepo |
| **补充裁定②** | **后端 Python FastAPI 单体 + Docker**，替代 CloudBase Node 云函数 | 与老板"部署 Docker"一致，本机可跑 |

---

## T01 · 云底座（脚手架 + 11 表 + 配置）— 无依赖，P0

**目标**：`docker compose up` 后 `GET /api/health` 返回 `{"code":0,"msg":"ok","data":{"db":"postgres"}}`，三前端各自 `pnpm dev` 出白板页。

| # | 任务名 | 产出文件 | 依赖 | 验收标准 |
|---|---|---|---|---|
| 1.1 | 仓库与容器编排 | `docker-compose.yml`、`.env.example`、`.gitignore`、`README.md`、`scripts/dev-up.ps1` | — | `docker compose up -d db` 起 `pgvector/pgvector:pg16`，映射 **5433:5432**（避开本机占用）；README 含 Windows 双路径启动步骤 |
| 1.2 | 后端骨架与统一约定 | `backend/Dockerfile`、`backend/requirements.txt`、`backend/app/main.py`、`backend/app/core/{config,response,security,deps}.py`、`backend/app/schemas/common.py`、`backend/app/api/__init__.py`、`backend/app/api/health.py` | 1.1 | 任意异常经全局 handler 转 `{code,msg,data}`；`/docs` 可打开；`create_token/verify_token` 单测通过 |
| 1.3 | **DB 方言兼容层**（★最高风险） | `backend/app/db.py`、`backend/app/models/base.py` | 1.2 | `GUID`/`JSONType`/`UUIDArray` 三个 `TypeDecorator` 在 PG 落 `UUID/JSONB/uuid[]`、在 SQLite 落 `CHAR(36)/TEXT-JSON/JSON`；同一份 models 两方言均能 `create_all()` |
| 1.4 | 11 张表 ORM + DDL | `backend/app/models/__init__.py`、`user.py`(users,students)、`content.py`(knowledge_points,questions,answers)、`learning.py`(mistakes,attempts,student_profile,learning_tasks)、`ai.py`(ai_sessions,ai_messages)、`backend/migrations/001_init.sql`、`backend/migrations/001_init_sqlite.sql`、`backend/scripts/init_db.py` | 1.3 | 11 表齐全、外键/唯一键/索引与 v4-db-api §A 逐字段一致；**`student_profile.long_term_vector` V4.1 不建**（留 V4.2）；`attempts` 加 `client_event_id UNIQUE` 幂等列 |
| 1.5 | 三前端脚手架 | `student-web/`、`parent-web/`、`admin-web/` 各 9 文件：`package.json`、`vite.config.ts`、`tsconfig.json`、`tailwind.config.js`、`postcss.config.js`、`index.html`、`src/main.ts`、`src/App.vue`、`src/api/http.ts` | 1.2 | `http.ts` 统一拦截：注入 `Authorization`、拆 `{code,msg,data}`、401 跳登录；vite `proxy: {'/api': 'http://localhost:8000'}` |
| 1.6 | 冒烟测试 | `backend/tests/conftest.py`、`backend/tests/test_health.py`、`backend/pytest.ini` | 1.4 | `pytest` 在 SQLite 与 PG 两套 `DATABASE_URL` 下**均绿** |

**T01 文件总数：54**（根级基建 5 + 后端 22 + 前端 27）

### 本地可运行方案（Windows 构建环境必读）

| 路径 | 命令 | `DATABASE_URL` | 用途 |
|---|---|---|---|
| **A. Docker（推荐，QA/联调/上线前验证）** | `docker compose up -d db && python backend/scripts/init_db.py` | `postgresql+psycopg://coach:coach@localhost:5433/coach` | 真 PG16+pgvector，跑 `001_init.sql` |
| **B. SQLite 回退（本机无 Docker 也能开工）** | `set DB_DIALECT=sqlite && python backend/scripts/init_db.py` | `sqlite:///./dev.db` | 走 `metadata.create_all()`，零外部依赖 |

**唯一切换点 = `.env` 里的 `DATABASE_URL`**。感知方言的代码只允许出现在 `app/db.py` 与 `app/models/base.py` 两个文件。
**硬约束**：业务层禁止裸 SQL 中的 PG 专属语法（`->>`、`@>`、`ARRAY[]`、`gen_random_uuid()`、`WITH RECURSIVE`）；知识点树递归改用 Python 侧内存递归（V4.1 知识点 < 300 条，无性能问题）。CI 双跑两方言防漂移。上线切 PG 时**只改环境变量，不改代码**。

---

## T02 · 账号与数据层 — 依赖 T01，P0

| # | 任务名 | 产出文件 | 依赖 | 验收标准 |
|---|---|---|---|---|
| 2.1 | 手机验证码 + JWT 登录 | `backend/app/api/auth.py`、`backend/app/services/auth_service.py`、`backend/app/services/sms.py` | T01 | `POST /api/auth/login` 返回 access(2h)/refresh(30d)；**dev 环境万能码 `000000`**（无短信也能测）；无 token 访问受保护接口返 401 |
| 2.2 | 学生档案 | `backend/app/api/student.py`、`backend/app/schemas/student.py` | 2.1 | `GET/PUT /api/student/profile` 通过；`grade` 越界（0/7）返 code=400 |
| 2.3 | **893 题 seed（复用资产）** | `backend/scripts/extract_bank.py`、`backend/seed/knowledge_points.json`、`backend/seed/questions.json`、`backend/scripts/seed_db.py` | T01 | 从 `index.html:2737` 的 `QUESTION_BANK` 抽取小学 6 年级 **893 题**（初中 16 题 archived）；`tags.knowledge`(计算/方程/应用题/分数/小数/百分数/比例/几何/图形/综合) → `knowledge_points`；`difficulty` 易中难→1/3/5；`a` 字段 → `answers.correct_answer`；`options` → `questions.options`。**幂等**：重跑不产生重复 |
| 2.4 | 登录页 + 档案页 | `student-web/src/views/Login.vue`、`src/views/Profile.vue`、`src/stores/auth.ts`、`src/router/index.ts` | 2.1 | 登录→存 token→路由守卫生效；刷新不掉登录态 |

---

## T03 · AI 网关与教学闭环 — 依赖 T02，P0（V4.1 核心）

| # | 任务名 | 产出文件 | 依赖 | 验收标准 |
|---|---|---|---|---|
| 3.1 | **Prompt 资产迁移（复用）** | `backend/app/ai/prompts/{ocr,tutor,exercise,summary,practice_guide,practice_judge}.py` | T02 | 原样迁 `index.html` 的 `OCR_PROMPT`(556) / `SYSTEM_PROMPT` Socratic 6 阶段(564) / `WORKSHEET_PROMPT`(619) / `SUMMARY_PROMPT`(630) / `PRACTICE_GUIDANCE_PROMPT`(648) / `PRACTICE_JUDGE_PROMPT`(663)；每个带 `VERSION` 常量写入 `ai_sessions` |
| 3.2 | **方舟客户端（复用中转模式）** | `backend/app/ai/ark_client.py` | T01 | 把 `server.js` / `cloud-functions/api/chat.js` 的 `/api/v3/responses` 透传逻辑改为服务端持 Key（`ARK_API_KEY` 走 .env）；**前端不再出现 Key 输入框**；超时 60s + 重试 1 次 |
| 3.3 | 4 个在线 Agent 编排器 | `backend/app/ai/orchestrator.py`、`backend/app/ai/agents/{question,diagnose,tutor,exercise}.py`、`backend/app/ai/guards.py` | 3.1,3.2 | 按 v4-db-api §C.2 走通 Step3→Step9；`guards.py` 用 DB 计数限流，超 20 次/日返 **429**；每轮落 `ai_sessions`+`ai_messages` |
| 3.4 | 教学与错题 API | `backend/app/api/chat.py`、`backend/app/api/mistake.py`、`backend/app/api/exercise.py`、`backend/app/services/learning_service.py`、`backend/app/api/file.py` | 3.3 | `/api/chat`、`/api/chat/{id}/message`、`/api/mistake`、`/api/mistake/list`、`/api/exercise/gen` 全通；图片存 `backend/storage/`；重复 `client_event_id` 只写一条 attempt |
| 3.5 | 学生端 AI 老师页 + 错题本 | `student-web/src/views/AiTeacher.vue`、`src/views/Mistakes.vue`、`src/components/ChatBubble.vue`、`src/components/QuestionCard.vue`、`src/api/chat.ts` | 3.4 | 拍照→引导→练习→小结全流程可走完；错题本三态（未掌握/练习中/已掌握）筛选正确 |

---

## T04 · 画像引擎与学生端主界面 — 依赖 T03，P1

| # | 任务名 | 产出文件 | 依赖 | 验收标准 |
|---|---|---|---|---|
| 4.1 | 画像重算（EWMA） | `backend/app/services/profile_service.py`、`backend/scripts/replay_attempts.py` | T03 | 由 `attempts` 全量回放可重建 `student_profile.mastery`，与增量结果误差 < 1 |
| 4.2 | 规划 Agent（离线） | `backend/app/ai/agents/planner.py`、`backend/app/jobs/scheduler.py`、`backend/app/jobs/daily_plan.py` | 4.1 | APScheduler 每日 05:00 生成 `learning_tasks`；复用 `PLAN_PROMPT`(index.html:2252) 与 `PLAN_TOTAL=5` 规则；手动触发接口可测 |
| 4.3 | 能力地图 / 任务 / 成就 API | `backend/app/api/{ability_map,task,achievement}.py` | 4.1 | `/api/ability-map` 返回 `ABILITY_ORDER`(2603) 九维；`/api/task/today`、`/api/achievement` 通 |
| 4.4 | 学生端 4 页（复用图表逻辑） | `student-web/src/views/{Home,Daily,AbilityMap,Achievement}.vue`、`src/components/BarChart.vue` | 4.3 | 迁 `renderMap()`(2678) 条形图逻辑；移动端 375px 无横向滚动 |

---

## T05 · 家长端 H5 + 后台 + 上线 — 依赖 T03/T04，P1

| # | 任务名 | 产出文件 | 依赖 | 验收标准 |
|---|---|---|---|---|
| 5.1 | 家长报告 Agent（离线） | `backend/app/ai/agents/reporter.py`、`backend/app/jobs/weekly_report.py`、`backend/app/api/parent.py` | T04 | 每周一 08:00 生成周报；复用 `REPORT_PROMPT`(677) + `WEEKLY_PROMPT`(2136)；**家长越权取他人孩子返 403**（服务端强校验，V4.1 无 parents 表则以 `students.user_id` 兜底） |
| 5.2 | 家长端 H5 四页 | `parent-web/src/views/{Home,Report,Weakness,Advice}.vue`、`src/router/index.ts` | 5.1 | 微信内置浏览器 + Safari 渲染正常；只读，无写操作入口 |
| 5.3 | 管理后台 | `admin-web/src/views/{Users,Questions,KnowledgeGraph,AiQuality,Metrics}.vue`、`backend/app/api/admin.py` | T03 | Element Plus Table 分页；`/api/admin/*` 非 admin 角色返 403 |
| 5.4 | 部署与灰度 | `docker-compose.prod.yml`、`backend/Dockerfile`(多阶段)、`nginx.conf`、`docs/deploy.md` | 5.1,5.2,5.3 | **只改 `DATABASE_URL` 即从 SQLite 切 PG**，无代码改动；三前端构建产物由 nginx 托管，`/api/*` 反代后端 |

---

## 依赖与共享约定

**T01 `backend/requirements.txt`**
```
fastapi==0.115.*        uvicorn[standard]==0.32.*   sqlalchemy>=2.0.36
psycopg[binary]==3.2.*  pydantic>=2.9   pydantic-settings>=2.6
PyJWT==2.9.*            passlib[bcrypt]==1.7.4      python-multipart==0.0.12
httpx==0.27.*           APScheduler==3.10.*         orjson==3.10.*
pytest==8.3.*           pytest-asyncio==0.24.*
```
> 用 **PyJWT** 而非 python-jose（后者拖 cryptography，Windows 易编译失败）；用 **psycopg[binary]** 而非 psycopg2（免 Windows 编译 PG 头文件）。

**跨模块硬约定**
1. 响应恒为 `{"code":0,"msg":"ok","data":{}}`；HTTP 状态码与 `code` 同步（401/403/429/500），成功为 200。
2. 错误码：`0` 成功 ／ `401` 未登录或 token 失效 ／ `403` 越权 ／ `429` 超配额 ／ `500` 服务错误 ／ `400` 参数非法。
3. 鉴权：`Authorization: Bearer <access>`，HS256，payload `{sub:user_id, role, sid:student_id, exp}`；access 2h、refresh 30d。**家长/管理员权限一律服务端校验，前端不可信**。
4. 命名：DB `snake_case`，API 出入参 `camelCase`（Pydantic `alias_generator`）；时间 ISO 8601 UTC 存储、`Asia/Shanghai` 渲染。
5. 幂等：所有写事件带前端生成的 `clientEventId`，DB 唯一索引兜底。
6. Prompt 只允许存在于 `backend/app/ai/prompts/`，前端零硬编码。

---

## T01–T05 顺序表（甘特式）

```
        W1        W2        W3        W4        W5        W6
T01 ████████                                              (无依赖，起点)
T02          ████████                                     (依赖 T01)
T03                   ████████████                        (依赖 T02·核心)
T04                             ████████                  (依赖 T03)
T05                                  ████████████         (依赖 T03+T04)
```

| 里程碑 | 依赖 | 可否并行 |
|---|---|---|
| T01 | — | 内部：1.5 三前端脚手架可与 1.2–1.4 后端**并行** |
| T02 | T01 | 2.3 seed 脚本可与 2.1/2.2 **并行**（只依赖 T01 建表） |
| T03 | T02 | 3.1 Prompt 迁移可在 T02 期就**提前启动**（纯文本搬运） |
| T04 | T03 | 4.4 前端页面可用 mock 数据与 4.1–4.3 **并行** |
| T05 | T03, T04 | 5.2 家长端 / 5.3 后台**两条线完全并行**；5.4 收口 |

```mermaid
graph LR
T01[T01 云底座] --> T02[T02 账号与数据层] --> T03[T03 AI网关与教学闭环]
T03 --> T04[T04 画像与学生端] --> T05[T05 家长端+后台+上线]
T03 --> T05
```

> **T01–T03 完成即是可用的 V4.1**：账号 + 真实 AI + 数据闭环，可交付内测。T04/T05 是体验与运营补齐。
