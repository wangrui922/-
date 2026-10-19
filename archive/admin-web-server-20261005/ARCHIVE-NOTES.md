# admin-web 服务器版存档（2026-10-05 快照）

> 本文件为**归档说明**；快照自带的 Geeker-Admin 模板说明见同目录 `README.md`（原样保留，未改动）。

## 这是什么

生产服务器上运行的运营后台 admin-web 的**原样存档**。本目录是 2026-10-05
从生产宿主机拉取的完整快照，除构建产物外未做任何代码改动，作为历史记录永久保留。

## 来源与规模

- **来源**：生产服务器快照（`.work/srv-src/admin-web`，快照日期 2026-10-05），
  由 srv-scout 对账时获取。
- **规模**：283 个源文件（Geeker-Admin 模板，Vue3 + ElementPlus + Vite），
  约 6.5 MB。
- **基础模板**：Geeker-Admin（Vue3 + TypeScript + ElementPlus + Pinia + Vite），
  在其上叠加了「AI 数学教练」运营页面。

## 为何归档

这套代码**从未进入 git 版本控制**，却承载生产运营依赖：

- 激活码发放 / 查询（`src/views/activation/`）
- 支付确认（`src/views/payments/`，调用 `/billing/pending`、`/billing/confirm`）
- 系统配置管理（`src/views/config/`，调用 `/admin/configs`）
- 运营看板（`src/views/dashboard/`，调用 `/admin/dashboard/overview`、
  `/admin/dashboard/members`）
- 用户管理、错题本、掌握度、AI 会话查看等运营页面

「只活在服务器上」的代码有丢失与分叉风险。本次入库存档的目的是
**消除野生代码风险**——先让它在 git 里有据可查，演进决策另行讨论。

## 状态：历史存档，不再演进

本目录是冻结快照，**不要从本目录继续开发**。需要改动运营后台时，
请在活跃代码库上开发（见下节）。

## 与其他代码的关系

| 代码 | 位置 | 状态 |
|---|---|---|
| 本存档（服务器版） | `archive/admin-web-server-20261005/` | 历史存档，不再演进 |
| 活跃 admin-web fork | `/f/错题教练/admin-web` | Geeker-Admin fork，活跃开发中（质量监控页面等由 admin-quality-dev 维护） |

注意：本存档与仓库根的 `admin-web` 是**两份独立代码**，请勿混用或互相覆盖。

**与仓库后端的关系**：快照时其调用的以下端点在仓库后端中尚不存在
（后端能力由 #48 短信 Provider、#49 `/admin/configs` 等任务分批合并补齐，
与本存档无代码依赖）：

- `POST /billing/pending`
- `POST /billing/confirm`
- `GET /admin/dashboard/overview`
- `GET /admin/dashboard/members`
- `GET/PUT /admin/configs`

因此本存档的后端调用链**不能直接对当前 develop 后端运行**，仅供历史查阅。

## 入库时排除项

- `node_modules/`、`dist/`、`.git/`、各类构建产物（快照拉取时已不含）。
- 其余原样保留：`src/`、`public/`、`tsconfig.json`、`vite.config.ts`、
  `package.json`、`package-lock.json`、各 lint 配置、`.env*` 四个文件、
  `.vscode/` 工程配置。

## 凭据扫描结论

四个 `.env*` 文件仅含 `NODE_ENV` / `VITE_API_URL`（`/api`、测试域、
开发环境指向服务器 IP），**无密钥/密码/Token 值**（grep 核验通过）。
全树补扫亦无字面密钥（命中的 password/token 均为登录表单、JWT 存取、
axios CancelToken 等 UI/运行时机制）。
`.env` 与 `.vscode/` 虽命中仓库根 `.gitignore` 规则，因已确认无敏感内容，
按「原样入库」要求强制纳入并在此声明。JWT/COS 等凭据均运行时注入，
不在快照内。

## 如何查阅/构建（历史记录，仅存档参考）

```bash
cd archive/admin-web-server-20261005
npm install        # 依赖以 package-lock.json 为准
npm run dev        # 开发预览
npm run build      # 生产构建（Dockerfile.production + nginx.production.conf）
```

由于上述后端端点缺口，dev 环境登录后的业务请求会失败，属预期
（见「与仓库后端的关系」）。
