# SCF 云函数部署说明 — 错题教练每日同步触发器

## 架构说明

生产库是**服务器本地 SQLite**（`/home/ubuntu/coach/runtime/data/coach.db`），云函数无法直连文件型数据库。
因此采用 **HTTP 触发模式**：

```
腾讯云 SCF (Timer cron: 0 5 2 * * * *)
    │  POST /api/cron/daily-sync  (Header: X-Cron-Token)
    ▼
服务器 backend 容器 (deploy-backend-1)
    │  执行 cron_jobs.daily_sync() + cleanup_old_sessions()
    ▼
掌握度刷新 / 周日周报 / 30 天前会话清理
```

与服务器上 APScheduler（02:00 / 03:00）逻辑一致，SCF 定时 02:05 触发，
作为 APScheduler 的云端兜底，防止 backend 重启窗口内漏跑。

## 已部署内容（本次完成）

1. **服务器端 cron 端点** `POST /api/cron/daily-sync`
   - 校验 `X-Cron-Token` 请求头，不匹配返回 403
   - 执行 `cron_jobs.daily_sync()` + `cleanup_old_sessions()`
   - 已上传并重建 backend 容器，实测三层鉴权全部通过：
     - 无 token → 403
     - 错 token → 403
     - 对 token → 200 `{"code":0,"data":{"mastery_updated":0,...}}`
2. **`.env.production` 已注入**：`CRON_TOKEN=***REMOVED***`
   （`CRON_URL` 是 SCF 函数侧的环境变量，配置在腾讯云控制台，见下方步骤 3）
3. **SCF 部署包**：`deploy/scf/scf-daily-sync.zip`（纯标准库，Python3.9，入口 `main_handler`，无需 requirements）

## 剩余步骤（需在腾讯云控制台完成）

1. 登录腾讯云控制台 → 云函数 SCF → 新建函数
   - 创建方式：**从头开始** / 上传 zip（本包仅 2.8KB）
   - 运行环境：**Python 3.9**（或 3.10/3.11）
   - 入口函数：`main_handler`
   - 内存：64MB，超时：60 秒
2. 函数代码上传 `deploy/scf/scf-daily-sync.zip`
3. 环境变量（函数配置 → 环境变量）：
   - `CRON_URL` = `https://118.25.44.126/api/cron/daily-sync`
   - `CRON_TOKEN` = `***REMOVED***`
4. 创建定时触发器：
   - 触发方式：**定时触发**
   - 周期：**自定义** cron 表达式
   - cron：`0 5 2 * * * *`（每天 02:05，错开服务器 APScheduler 的 02:00）
5. 保存后点「测试」验证一次，控制台日志应出现 `server responded 200`。

## 验证方式

```bash
# 服务器侧（任一）
curl -s -X POST https://118.25.44.126/api/cron/daily-sync \
  -H "X-Cron-Token: ***REMOVED***"
# → {"code":0,"msg":"ok","data":{...}}

# 错误 token（应 403）
curl -s -o /dev/null -w '%{http_code}' -X POST https://118.25.44.126/api/cron/daily-sync \
  -H "X-Cron-Token: wrong"
# → 403
```

## 安全说明

- CRON_TOKEN 已写入服务器 `.env.production`（backend/worker 共用），勿在代码中硬编码。
- 该 token 只允许触发无害的统计同步，不涉及管理权限。
- 若需更换 token：修改 `.env.production` 后 `docker compose up -d backend`，并在 SCF 环境变量同步更新。