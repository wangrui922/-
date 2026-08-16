# 公网托管最小运行手册（上线就绪核对）

> 适用：cuotixiaolaoshi v4.1「AI 数学教练 / 错题小老师」。
> 现状：开发机**无外网**，无法真部署；代码已"部署就绪"。本文件是你在有网环境照做的最小步骤。完整版见 `DEPLOY.md`。

## 一、就绪度核对（已具备，无需改代码）

- [x] 后端 `backend/Dockerfile`：生产镜像（gunicorn + UvicornWorker + HEALTHCHECK），可直接 build
- [x] 后端配置生产校验：`config.jwt_secret_safe` 要求 `JWT_SECRET` 非默认值且 ≥16 位，否则启动被拦
- [x] 前端可注入后端地址：构建时 `VITE_API_BASE` 写死（见 `DEPLOY.md` §2）
- [x] 前后端分离架构（前端纯静态 / 后端长驻进程，密钥只在后端）
- [ ] **真部署**：需你提供 ① 有公网 IP 的服务器（如腾讯云 Lighthouse）② 一台有网机器执行部署命令

## 二、你要做的（两步，缺一不可）

### A. 后端托管（有公网 IP 的服务器）

1. 装 Docker。
2. 把 `backend/` 目录传上去，构建镜像：
   ```bash
   cd backend && docker build -t ai-math-coach-backend .
   ```
3. 起服务（`dev.db` **必须挂 volume**，否则容器重启数据全丢）：
   ```bash
   docker run -d --name coach-be -p 8000:8000 \
     -e APP_ENV=prod \
     -e JWT_SECRET=<随机串,>=16位> \
     -e CORS_ORIGINS=https://你的前端域名 \
     -e PUBLIC_BASE_URL=https://你的前端域名 \
     -e ARK_API_KEY=<模型网关Key> \
     -v $(pwd)/dev.db:/app/dev.db \
     ai-math-coach-backend
   ```
4. 验证：`curl https://你的服务器域名或IP:8000/api/health` 返回 200。
   > 建议用 Nginx/Caddy 反代 + HTTPS + 域名，对外只暴露 443；SQLite 适应当前量，量大再切 PostgreSQL（只改 `DATABASE_URL`）。

### B. 前端部署（在有网机器上）

1. 装 Node + EdgeOne CLI（≥1.6.0）：`npm install -g edgeone@latest`
2. 登录：`edgeone login --token <你的API_TOKEN>`
3. 用生产后端地址构建并部署：
   ```bash
   export PAGES_SOURCE=skills
   VITE_API_BASE=https://你的后端域名 \
     edgeone makers deploy -n ai-math-coach --json
   ```
4. 拿到线上地址（**带 `?` 及后面参数**），发给用户即可在手机/电脑打开。

## 三、上线验证清单

- [ ] 前端线上地址能打开
- [ ] 能注册/登录（CORS 已放行前端域名）
- [ ] 抽题 / AI 讲题能跑（ARK_API_KEY 生效）
- [ ] 后端 `/api/health` 返回 200
- [ ] 重启后端容器后题库/错题仍在（volume 挂载成功）

## 四、注意

- `JWT_SECRET` 必须随机且 ≥16 位，否则生产启动会被 `jwt_secret_safe` 拦下。
- `ARK_API_KEY` 只在后端环境变量，**绝不进前端 / 仓库**。
- 本机日常联调仍用：后端 `uvicorn`（见 `backend/start_server.bat`）+ 前端 `npm run dev`；临时对外试用用 `start-public-trial.bat`（cpolar 穿透，电脑需常开）。
- 数据量大 / 并发上来再切 PostgreSQL：只改 `DATABASE_URL`，业务代码不动（`README.md` §5）。
