# AI数学教练 · V4.1

小学 1-6 年级 AI 数学辅导系统。V4.1 从「单文件 HTML + localStorage」升级为
**FastAPI 后端 + PostgreSQL16(pgvector) / SQLite 双方言 + Vue3 三前端** 的云端商业系统。

---

## 1. 目录结构（T01 已落地部分）

```
cuotixiaolaoshi/
├─ docker-compose.yml          # pgvector/pgvector:pg16，宿主 5433 -> 容器 5432
├─ .env.example                # 复制成 .env；唯一切换点 = DATABASE_URL
├─ .gitignore
├─ README.md
├─ scripts/
│  └─ dev-up.ps1               # Windows 一键：起库 -> 建表 -> 跑测试 -> 起服务
└─ backend/
   ├─ Dockerfile
   ├─ requirements.txt
   ├─ pytest.ini
   ├─ app/
   │  ├─ main.py               # FastAPI 入口 + 全局异常 -> {code,msg,data}
   │  ├─ db.py                 # ★方言感知点之一：Engine/Session/DIALECT
   │  ├─ core/
   │  │  ├─ config.py          # pydantic-settings 读 .env
   │  │  ├─ response.py        # 统一响应与错误码 0/400/401/403/429/500
   │  │  ├─ security.py        # JWT HS256 create_token / verify_token
   │  │  └─ deps.py            # get_db / get_current_user / require_roles
   │  ├─ schemas/common.py     # CamelModel（DB snake_case ↔ API camelCase）
   │  ├─ api/
   │  │  ├─ __init__.py        # api_router，prefix=/api
   │  │  └─ health.py          # GET /api/health
   │  └─ models/
   │     ├─ base.py            # ★方言感知点之二：GUID / JSONType / UUIDArray
   │     ├─ user.py            # users, students
   │     ├─ content.py         # knowledge_points, questions, answers
   │     ├─ learning.py        # mistakes, attempts, student_profile, learning_tasks
   │     └─ ai.py              # ai_sessions, ai_messages
   ├─ migrations/
   │  ├─ 001_init.sql          # PostgreSQL 16 DDL（含 pgcrypto/pg_trgm/vector）
   │  └─ 001_init_sqlite.sql   # SQLite 等价 DDL（参照/离线建库用）
   ├─ scripts/init_db.py       # 建表入口：SQLite 走 create_all，PG 走 001_init.sql
   └─ tests/                   # conftest.py / test_health.py / test_security.py
```

---

## 2. Windows 快速启动（两条路径，任选其一）

> 托管 Python 解释器（本机已装）：
> `C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe`

### 路径 A：Docker + PostgreSQL（推荐，QA / 联调 / 上线前验证）

```powershell
cd D:\制作网页\cuotixiaolaoshi
copy .env.example .env

# 1) 起数据库（pgvector/pgvector:pg16，宿主机端口 5433）
docker compose up -d db
docker compose ps                      # 等 db 变成 healthy

# 2) 建 venv 装依赖
& "C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

# 3) 建 11 张表（执行 backend/migrations/001_init.sql）
$env:DATABASE_URL = "postgresql+psycopg://coach:coach@localhost:5433/coach"
.\.venv\Scripts\python.exe backend\scripts\init_db.py

# 4) 起后端
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000 --app-dir backend
```

### 路径 B：SQLite 回退（本机没有 Docker 也能开工）

```powershell
cd D:\制作网页\cuotixiaolaoshi
$env:DATABASE_URL = "sqlite:///./dev.db"
.\.venv\Scripts\python.exe backend\scripts\init_db.py
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000 --app-dir backend
```

### 验证

```powershell
curl http://localhost:8000/api/health
# {"code":0,"msg":"ok","data":{"db":"postgres", ...}}   或 "db":"sqlite"
```

Swagger 文档：<http://localhost:8000/docs>

### 一键脚本

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1            # 默认 sqlite
powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1 -Mode pg   # Docker + PG
```

---

## 3. 跑测试

```powershell
cd D:\制作网页\cuotixiaolaoshi\backend
$env:DATABASE_URL = "sqlite:///./dev.db"
..\.venv\Scripts\python.exe -m pytest -q

# PG 方言同一套用例再跑一遍（防方言漂移）
$env:DATABASE_URL = "postgresql+psycopg://coach:coach@localhost:5433/coach"
..\.venv\Scripts\python.exe -m pytest -q
```

---

## 4. 工程硬约定（全员必守）

| 项 | 约定 |
|---|---|
| 统一响应 | 恒为 `{"code":0,"msg":"ok","data":{}}`；HTTP 状态码与 `code` 同步 |
| 错误码 | `0` 成功 / `400` 参数非法 / `401` 未登录 / `403` 越权 / `429` 超配额 / `500` 服务错误 |
| 鉴权 | `Authorization: Bearer <access>`，HS256，payload `{sub,role,sid,exp}`；access 2h、refresh 30d |
| 命名 | DB `snake_case`；API 出入参 `camelCase`（Pydantic `alias_generator`） |
| 时间 | 一律 UTC ISO 8601 存储，`Asia/Shanghai` 仅在渲染层转换 |
| **方言** | 业务层**禁止** PG 专属语法（`->>`、`@>`、`ARRAY[]`、`gen_random_uuid()`、`WITH RECURSIVE`）。知识点树递归用 Python 内存递归。方言差异只允许出现在 `app/db.py` 与 `app/models/base.py` |
| 幂等 | 写事件带 `clientEventId`，DB 唯一索引兜底（`attempts.client_event_id`） |
| Prompt | 只允许存在于 `backend/app/ai/prompts/`，前端零硬编码 |

**上线切 PG 只改 `DATABASE_URL`，不改一行代码。**

---

## 5. V4.1 数据表（11 张）

`users` `students` `knowledge_points` `questions` `answers`
`mistakes` `attempts` `student_profile` `learning_tasks` `ai_sessions` `ai_messages`

留后续版本：`parents` / `ai_feedback`（V4.2）、`orders` / `subscriptions`（V4.3）、
`student_profile.long_term_vector`（V4.2，pgvector 长期记忆）。
