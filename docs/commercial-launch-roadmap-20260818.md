# 商业落地「三座大山」可执行步骤清单

> 项目：AI 数学教练 / 错题小老师（cuotixiaolaoshi）
> 目标：把现在「本机跑、手机丢进度、未备案」的 Demo，变成「公网可访问、跨设备续做、合规上线」的可收费产品
> 底座：EdgeOne Makers（腾讯，国内可通，已验证能部署）——一个项目同时托管**静态前端 + `/api` 云函数（同域名免 CORS）+ Blob 用户存储**

---

## 0. 现状 vs 终态（一句话对齐）

| 维度 | 现状（Demo） | 终态（可收费） |
|---|---|---|
| 后端 | 本机 uvicorn `127.0.0.1:8000`，关电脑即停 | EdgeOne 云函数常驻，公网 `https://你的域名/api` |
| 数据 | 本地 SQLite，手机端进度存浏览器 localStorage | 题库打包只读 + 用户进度/错题存云端 Blob，账号级同步 |
| 域名 | `*.edgeone.cool` 预览域名（未备案，分享即 401） | 自有备案域名 + 免费 HTTPS |
| 登录 | 手机+验证码 `000000`（假） | 真手机短信 / 微信登录，JWT 鉴权 |
| 进度 | 换设备/清缓存归零 | 手机/平板/电脑登录同一账号无缝续做 |

---

## 1. 目标架构（MVP，最少外部依赖）

```
                         ┌─────────────────────────────────────────┐
   学生(手机/平板/电脑)──▶│  EdgeOne Makers 项目（同一域名）          │
   微信/浏览器           │                                          │
                         │  ┌──────────────┐   ┌────────────────┐  │
                         │  │ 静态前端      │   │ 云函数 /api     │  │
                         │  │ student-web  │◀─▶│ FastAPI(ASGI)  │  │
                         │  │ (Vue3)       │   │ 题库=只读打包   │  │
                         │  └──────────────┘   └────────┬───────┘  │
                         │                              │           │
                         │                     ┌────────▼───────┐  │
                         │                     │ Blob 存储       │  │
                         │                     │ users/{uid}/    │  │
                         │                     │  progress.json │  │
                         │                     │  mistakes.json │  │
                         │                     └────────────────┘  │
                         └───────────────────────────┬────────────┘
                                                     │ HTTPS
                                              ┌──────▼──────┐
                                              │ DeepSeek API │（讲题/出题，国内可通）
                                              └─────────────┘
```

**为什么这样最省事**：前端和 `/api` 在同一个 Makers 项目、同一个域名下，浏览器请求 `/api/...` 是同源的，**不用配 CORS、不用管跨域**；用户数据走 Blob（腾讯官方推荐替代「云函数本地写文件」的方案），**不用单独买数据库实例**；只有「域名+备案」是绕不开的外部动作。

---

## 2. 山一：后端上云（FastAPI → 云函数）

### 方案选型（请你拍板，推荐 A）

**方案 A｜Serverless 极简（推荐 MVP）**
- 题库：把 `backend/dev.db` 作为**只读包**打进云函数（~4MB，128MB 上限内），冷启动加载。
- 用户动态数据（进度/错题/会话）：改用 **EdgeOne Blob** 按 `users/{userId}/xxx.json` 存，不再写 SQLite。
- 外部依赖仅：域名+备案（本来就跑不掉）。**不买数据库。**
- 工作量：中（需把 `practice_service`/`answers`/`mistakes` 的写操作从 SQLite 切到 Blob）。

**方案 B｜标准托管（进阶）**
- 把 SQLite 迁移到 **腾讯云 TencentDB（MySQL/PostgreSQL）**，FastAPI 云函数直连。
- 更稳、更适合大并发，但需你开腾讯云账号 + 买 DB 实例（额外费用 + 配置）。

> 建议：先用 A 跑通收费闭环，用户过万再平滑切 B。

### 我直接能执行的步骤（不用等备案）

1. **项目结构重组**（把前端+后端并入同一个 Makers 项目根）：
   ```
   cuotixiaolaoshi/makers-prod/
   ├── student-web/          # 现有前端（改 API base 为相对 /api）
   ├── cloud-functions/
   │   ├── api/
   │   │   ├── index.py      # FastAPI ASGI 入口：from app.main import app
   │   │   ├── lib/
   │   │   │   ├── blob_store.py   # Blob 读写封装（进度/错题）
   │   │   │   └── auth.py         # JWT 签发/校验
   │   │   ├── data/questions.db   # 只读题库（从 dev.db 复制）
   │   │   └── requirements.txt     # fastapi, sqlalchemy, pyjwt, httpx...
   │   └── ...
   └── edgeone.json          # 由首次 deploy 自动生成
   ```
2. **FastAPI 入口适配**（云函数 ASGI 模式要求 `app = FastAPI()` 实例在 `cloud-functions/api/index.py`）：
   ```python
   # cloud-functions/api/index.py
   import sys, os
   sys.path.insert(0, os.path.dirname(__file__))
   from app.main import app   # 现有 backend/app 整体复用
   ```
3. **写操作切 Blob**：新增 `lib/blob_store.py`，用 `@edgeone/pages-blob`（npm，云函数内可用）实现：
   - `get_progress(uid)` / `save_progress(uid, data)`
   - `get_mistakes(uid)` / `append_mistake(uid, item)`
4. **前端 API base 改造**：`student-web` 里把固定的 `127.0.0.1:8000` 改为：
   - 开发：`http://127.0.0.1:8000`（本地 uvicorn，照旧调试）
   - 生产：相对路径 `/api`（同源，部署后自动生效）
   - 用 `import.meta.env.MODE` 或 `.env` 切换。
5. **部署命令**（本机已装好 CLI，路线照旧）：
   ```bash
   export PAGES_SOURCE=skills
   cd cuotixiaolaoshi/makers-prod
   edgeone makers deploy -n ai-math-coach-prod --json
   ```
   部署成功后，`/api/health` 应返回 200，题库可从 `/api/questions` 拉到。

### 你需要做的
- 选方案 A 还是 B（默认 A，直接回「A」即可）。
- 提供云函数需要的环境变量（我代填）：`DEEPSEEK_API_KEY`、`JWT_SECRET`、`CORS_ORIGINS`（同域可留空）。

---

## 3. 山二：备案（ICP）—— 绕不开的合规门槛

> 现状根因：你现在用 `*.edgeone.cool` 预览域名，腾讯对未备案域名在大陆访问会拦（401）。**不备案 = 产品上不了线**，跟 UI 多好看无关。

### 流程（你主导，我辅助填技术信息）

| 步骤 | 谁做 | 动作 | 耗时 |
|---|---|---|---|
| 1. 注册域名 | 你 | 在腾讯云 DNSPod / 阿里云万网 买一个域名（如 `aimathcoach.cn`），年费 ~50-100 元 | 10 分钟 |
| 2. 实名认证 | 你 | 域名实名（身份证/营业执照）+ 腾讯云账号实名 | 1-2 天 |
| 3. ICP 备案 | 你 | 通过域名服务商备案系统提交：**个人备案**（简单，但不能带经营/教育收费属性）或 **企业备案**（你有营业执照则选这个，可合规做收费教育产品） | 1-3 周 |
| 4. 绑域名到 EdgeOne | 我 | 在 EdgeOne 控制台把备案域名 CNAME 到 Makers，平台**自动签发免费 SSL 证书** | 我 10 分钟 |
| 5. 切生产 | 我 | 前端 `baseURL` 指向 `https://你的域名/api`，重部署 | 我 10 分钟 |

### 关键坑（提前知道）
- **个人备案不能做经营性/收费教育内容**，你这产品要收费 → 走**企业备案**（用营业执照）。没有营业执照就先去办（个体户也行，几天搞定）。
- 备案期间域名**无法访问**，期间可用 `*.edgeone.cool` 预览域名继续开发测试，不影响进度。
- K12 教育若涉及「在线培训」，部分地区还要**教育备案/办学许可**——那是规模化后的事，MVP 先用企业 ICP 备案跑通。

---

## 4. 山三：跨端进度同步（账号云同步）

### 改造点（前后端各一半）

**后端（云函数内，我执行）**
1. 加鉴权：把现在的「手机+`000000`」换成真验证码。MVP 两种选法：
   - **真短信**：接腾讯云 SMS（需你开 SMS 服务 + 短信签名/模板，约 0.04 元/条）
   - **微信登录**：接微信开放平台（需你注册开放平台 + 移动应用 appid，最贴合「学生用微信打开」场景，推荐）
   - 先期可保留「验证码任意/开发模式」以便联调，上线前切真。
2. 加进度接口（落到 Blob）：
   - `POST /api/progress/save` 存进度
   - `GET  /api/progress`   取进度
   - `GET/POST /api/mistakes` 错题本云端化
3. JWT：登录发 token，前端带 `Authorization: Bearer` 调接口；中间件可做统一鉴权守卫。

**前端（student-web，我执行）**
4. 把 `stores/practice.ts` 的 `localStorage` 持久化，改成：
   - 登录后 `GET /api/progress` 拉云端进度
   - 每次做题变化 `POST /api/progress/save` 落云端
   - 保留 localStorage 作为**离线兜底**（断网先存本地，联网再合并），避免体验回退
5. 登录页接微信/短信登录组件，替换当前假验证码。

### 你需要做的
- 决定登录方式：**微信登录（推荐）** / 手机短信 / 两者都要。
- 若微信：提供微信开放平台 AppID/AppSecret（我代填环境变量）。
- 若短信：开通腾讯云 SMS 并提供 SDK 凭证。

---

## 5. 排期与依赖（先做什么后做什么）

```
第 1 阶段（现在就能干，不等备案）───────────── 我执行
  ├─ 重组 makers-prod 项目结构
  ├─ FastAPI 搬进云函数 + 题库只读打包
  ├─ 进度/错题写操作切 Blob
  ├─ 前端 API base 改同源 /api
  └─ 部署验证 /api/health + /api/questions
         │
第 2 阶段（你跑外部流程，我并行）──────────── 你主导 + 我辅助
  ├─ 买域名 + 实名
  ├─ 企业 ICP 备案（1-3 周，期间继续开发）
  └─ 选登录方式 + 开对应服务（微信/SMS）
         │
第 3 阶段（备案下来后，2-3 天）────────────── 我执行
  ├─ 接真登录（微信/SMS）+ JWT 守卫
  ├─ 前端进度同步全链路联调
  ├─ 绑备案域名 + 免费 SSL + 切生产
  └─ 全量回归测试 + 上线
```

**关键洞察**：第 1 阶段（后端上云+同步机制）我现在就能动手，**完全不依赖备案**；备案的 1-3 周空窗期正好用来把功能做扎实。等你备案号下来，2-3 天就能切生产。

---

## 6. 风险与决策点（需要你拍板的）

| # | 决策点 | 选项 | 我的建议 |
|---|---|---|---|
| 1 | 数据层方案 | A: Blob 极简 / B: TencentDB | **A**（先跑通收费） |
| 2 | 登录方式 | 微信 / 短信 / 双 | **微信**（学生场景最顺） |
| 3 | 备案主体 | 个人 / 企业 | **企业**（要收费必须） |
| 4 | 是否现在就让我动手第 1 阶段 | 是 / 先只出方案 | 建议**是**，空窗期不浪费 |

---

## 7. 我下一步可立刻执行的（你回一句即可）

只要你回 **「A + 微信 + 企业 + 动手」**，我马上：
1. 在 `cuotixiaolaoshi/` 下建 `makers-prod/` 项目骨架
2. 把 FastAPI 搬进 `cloud-functions/api/`，题库只读打包
3. 写 `blob_store.py` 把进度/错题改存云端
4. 改前端 `API base` 为同源 `/api`
5. `edgeone makers deploy` 部署验证

（所有改动走本地 commit 路线 A 不 push、`.git` 冷备，跟你之前的纪律一致。）
