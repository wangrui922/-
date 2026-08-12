# 部署说明（AI 数学教练 V4.1）

> 目标读者：非技术背景的主理人。本文只给"照着做就能跑起来"的最简步骤。

---

## 一、本机启动（推荐，现在就用）

### 1. 双击启动器

打开 `backend` 文件夹，**双击 `start_server.bat`**。

它会自动：
- 启动后端服务（端口 `8000`）
- 崩溃后 **3 秒自动重启**（不用你管）
- 每 **6 小时**定时重启一次（防止 uvicorn 跑久了内存变脏报 500，这是之前登录失败的根因）
- 日志写到 `backend\logs\server_日期时间.log`

### 2. 启动前端

打开 `student-web` 文夹，在地址栏输入 `cmd` 回车，执行：

```
npm run dev
```

然后浏览器打开 **http://127.0.0.1:5173/practice**

> 前端代码有改动时，执行 `npm run build` 后刷新浏览器才生效。

### 3. 怎么停

**关掉 `start_server.bat` 那个黑窗口**即可。后端会随窗口关闭而停止。

### 4. 怎么看后端是否活着

浏览器打开 **http://127.0.0.1:8000/api/health**

看到 `{"code":0,"msg":"ok","data":{"db":"sqlite"}}` 就是正常的。

### 5. 后端报 500 怎么办

启动器已经能自动重启，正常情况不会卡死。如果真遇到 500：
1. 关掉黑窗口
2. 重新双击 `start_server.bat`
3. 一般就好了（内存脏已被定时重启根治）

---

## 二、上云部署（将来商业化用）

当你要给真实用户用、需要稳定 7×24 运行时，用 Docker。需要一台 Linux 服务器（阿里云/腾讯云轻量即可）。

### 1. 装好 Docker

服务器上装好 Docker 和 Docker Compose（云厂商控制台一般有一键装）。

### 2. 把 backend 文件夹传到服务器

把整个 `backend` 文件夹上传到服务器（用 scp / 宝塔面板 / 云厂商文件管理都行）。

### 3. 启动

```
cd backend
mkdir data          # 第一次需要建这个目录，数据库会自动建在里面
docker compose up -d --build
```

完成后：
- 后端跑在服务器 `8000` 端口
- **崩溃自动重启**（`restart: unless-stopped`）
- 每个 worker 处理 1000 个请求后自动回收（`--max-requests`），**根治内存脏**
- 健康检查每 30 秒打一次 `/api/health`，挂了 Docker 会自动重启容器

### 4. 看日志

```
docker compose logs -f
```

### 5. 配域名 + HTTPS（对外服务必须）

在服务器上装 Nginx，把域名指向 `127.0.0.1:8000`，再用 `certbot` 免费申请 HTTPS 证书。这一步建议找懂运维的人配，或用云厂商的"负载均衡/HTTPS 监听"一键搞定。

### 6. 数据备份

数据库在 `backend/data/dev.db`。定期复制这个文件到别处就是完整备份。建议每天一次（可写个定时任务）。

---

## 三、关键端口/路径速查

| 用途 | 地址 |
|---|---|
| 前端练习页 | http://127.0.0.1:5173/practice |
| 后端健康检查 | http://127.0.0.1:8000/api/health |
| 后端接口文档 | http://127.0.0.1:8000/docs |
| 后端日志 | backend/logs/server_*.log |

---

## 四、常见问题

**Q：双击 bat 闪退？**
A：多半是 `.venv` 没装好。在 `backend` 目录打开 cmd，执行 `python -m venv .venv` 然后 `.venv\Scripts\pip install -r requirements.txt`，再双击 bat。

**Q：前端打不开？**
A：先确认后端活着（看健康检查），再确认前端 `npm run dev` 在跑。两个都得开着。

**Q：数据丢了？**
A：本机模式下数据在 `backend/dev.db`，不会丢。上云模式数据在 `backend/data/dev.db`，只要不删 `data` 目录就不会丢。
