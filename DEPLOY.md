# AI 数学教练 · 上线部署手册（EdgeOne Makers）

> 适用版本：V4.1（前端 `student-web` Vue3+Vite / 后端 `backend` FastAPI+SQLite）
> 最近更新：2026-08-13（#68 部署就绪化：前端可注入后端地址 + 后端 CORS 环境白名单）

---

## 0. 架构先说清楚（很重要）

本产品是**前后端分离**：

| 部分 | 技术 | 上线方式 |
|------|------|----------|
| 前端（学生端网页） | Vue3 + Vite 静态产物 `dist/` | ✅ 直接丢到 **EdgeOne Makers** 静态托管（免费、自动 HTTPS、全球 CDN） |
| 后端（题库/AI 讲题/登录） | FastAPI + **SQLite** | ⚠️ **不能**跑在 Makers 静态环境里，必须**独立托管**（见 §3） |

为什么后端要单独托管？
- Makers 静态托管只跑前端静态文件，**没有长驻 Python 进程**，也**没有可写持久盘**；
- 后端持有 AI Key（Ark/DeepSeek），**必须放服务端**，绝不能进前端；
- SQLite 需要稳定的磁盘文件，serverless 函数重启会丢数据。

✅ 推荐组合：**前端 → EdgeOne Makers（免费）** ＋ **后端 → 任意一台有公网 IP 的服务器**（腾讯云 Lighthouse 轻量应用服务器最省事，几十块/月）。

---

## 1. 上线前置条件

|| 是否必须 | 说明 |
|---|---|---|
| 能联网的电脑（装 CLI 用） | ✅ | 本机目前**无外网**，需在有网的机器上执行部署命令 |
| EdgeOne Makers **API Token** | ✅ | 控制台 → Settings → API Token → 创建。账号级权限，**别提交进仓库** |
| 一台托管后端的服务器 | ✅ | 见 §3，给后端一个公网 URL（如 `https://api.yourdomain.com`） |
| 自有域名（可选） | ⬜ | 想用自己域名需 ICP 备案；先用 Makers 免费子域名也能跑 |

> 中国站控制台：https://console.cloud.tencent.com/edgeone/pages?tab=settings
> 国际站控制台：https://console.intl.cloud.tencent.com/edgeone/pages?tab=settings

---

## 2. 一键部署前端到 EdgeOne Makers

在**有网**的机器上（项目根目录 `cuotixiaolaoshi/student-web/`）：

```bash
# 0) 设置技能来源环境变量（每次部署前都要）
export PAGES_SOURCE=skills

# 1) 装 CLI（>= 1.6.0，低版本会卡死）
npm install -g edgeone@latest
edgeone -v          # 确认 >= 1.6.0

# 2) 登录（二选一）
#   浏览器登录（桌面环境）：
edgeone login --site china        # 或 global
#   或 Token 登录（无界面/远程，推荐）：
edgeone login --token <你的API_TOKEN>

# 3) 用生产后端地址构建并部署
#    VITE_API_BASE 会在构建时写死进前端，指向你托管好的后端
VITE_API_BASE=https://api.yourdomain.com \
  edgeone makers deploy -n ai-math-coach --json
```

成功后终端最后一行是 JSON，形如：
```json
{"status":"success","url":"https://ai-math-coach-xxxx.edgeone.cool?<auth_query>","projectId":"makers-xxxx","consoleUrl":"https://console.cloud.tencent.com/edgeone/pages/project/makers-xxxx/deployment/xxxx"}
```
🌐 **线上地址** = 上面 `url` 字段（**带 `?` 及后面参数**，缺了打不开）。

> 预览环境：`edgeone makers deploy -e preview`
> 换项目名冲突：`edgeone makers deploy -n <别的名字>`
> 构建失败先看日志，通常是缺依赖或 build 脚本错。

---

## 3. 后端独立托管（必做）

后端需要一台**有公网 IP、能跑 Python、磁盘可持久**的服务器。

### 3.1 起服务（Docker 最省心，已备好 `backend/Dockerfile`）

```bash
cd backend
# dev.db 必须挂 volume，否则容器重启数据全丢
docker build -t ai-math-coach-backend .
docker run -d --name coach-be \
  -p 8000:8000 \
  -e APP_ENV=prod \
  -e JWT_SECRET=<随机>=长度>=16 \
  -e CORS_ORIGINS=https://ai-math-coach-xxxx.edgeone.cool \
  -e PUBLIC_BASE_URL=https://ai-math-coach-xxxx.edgeone.cool \
  -e ARK_API_KEY=<你的模型Key> \
  -v $(pwd)/dev.db:/app/dev.db \
  ai-math-coach-backend
```

确认健康：`curl https://你的后端域名/api/health` 返回 200。

### 3.2 关键环境变量（后端 `backend/.env` 或 `-e` 传入）

| 变量 | 示例 | 作用 |
|------|------|------|
| `APP_ENV` | `prod` | 触发生产校验（JWT secret 必须安全） |
| `JWT_SECRET` | `随机串>=16位` | 登录令牌密钥，**必改默认值** |
| `CORS_ORIGINS` | `https://xxx.edgeone.cool` | 允许跨域的前端域名（逗号分隔，`*` 放开） |
| `PUBLIC_BASE_URL` | `https://xxx.edgeone.cool` | 家长周报分享链接前缀 |
| `ARK_API_KEY` | `xxx` | 模型网关 Key（**仅服务端**，前端看不到） |
| `DATABASE_URL` | `sqlite:///./dev.db` | 换 Postgres 只需改这里（见 §5） |

> 🔐 安全提醒：`ARK_API_KEY` 只存在于后端环境变量，**绝不要**写进前端或提交仓库。
> 后端健康接口：`GET /api/health`（Dockerfile 已配 HEALTHCHECK）。

---

## 4. 备选：用 EdgeOne Pages Functions 反代（不单独开服务器时）

如果你**暂时不想单独租服务器**，可让 Makers 用 serverless 函数把 `/api/*` 反代到任意已有的后端地址（后端仍得有个公网 URL，函数只做转发）：

> ⚠️ 以下为参考片段，部署前请按 EdgeOne Pages Functions 最新文档核对路由语法（catch-all 文件名/参数名可能随版本调整）。

`student-web/functions/api/[...].js`：
```js
export async function onRequest(context) {
  const { request, env } = context
  const target = (env.BACKEND_URL || 'https://你的后端域名').replace(/\/$/, '')
  const url = new URL(request.url)
  const path = url.pathname.replace(/^\/api/, '') + url.search
  return fetch(target + path, {
    method: request.method,
    headers: request.headers,
    body: request.method === 'GET' ? undefined : await request.arrayBuffer(),
  })
}
```
设置函数环境变量 `BACKEND_URL` 即可。注意：函数**不持有状态、不能写 SQLite**，后端数据仍须落在别处。

---

## 5. 上线后切换数据库（可选，量大时）

当前 `DATABASE_URL=sqlite:///./dev.db` 适合轻量。数据量/并发上来后换 Postgres：

```bash
# 仅改后端环境变量，代码不动
DATABASE_URL=postgresql+psycopg://user:pass@host:5432/coach
```

`backend/app/core/config.py` 已把 `DATABASE_URL` 作为唯一切换点，无需改业务代码。

---

## 6. 上线验证清单

- [ ] 前端能打开线上地址（带 `?` 参数）
- [ ] 能注册/登录（后端 CORS 已放行前端域名）
- [ ] 拍照识题 / AI 讲题能跑（后端 `ARK_API_KEY` 生效）
- [ ] 家长周报分享链接域名正确（`PUBLIC_BASE_URL`）
- [ ] 后端 `/api/health` 返回 200
- [ ] 数据持久：重启后端服务后题库/错题仍在（volume 挂载成功）

---

## 7. 本机当前状态（给维护者）

- 本机**无外网** → `npm install -g edgeone` 装不了，真部署需换有网机器。
- 本机无 EdgeOne Token → 无法 `edgeone makers deploy`。
- 代码已"部署就绪"：前端支持 `VITE_API_BASE` 注入、后端 CORS 走 `CORS_ORIGINS` 环境变量。
- 本地日常运行仍用：后端 `uvicorn`（见 `backend/start_server.bat`）+ 前端 `npm run dev`（代理 `localhost:8000`）。
