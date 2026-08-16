# 错题小老师 v4.1 · 全面体检与优化清单

> 审计日期：2026-08-16
> 范围：cuotixiaolaoshi 后端（FastAPI+SQLite/PG）+ 前端（Vue3+Vite）+ 数据 + 部署
> 方法：静态代码审计 + dev.db 实测 + 既有质量审计脚本复查

---

## 结论速览（一句话）

**数据质量过硬**（3172 题，双不变式 100% 成立），真正的短板是：
① **产品无法公网可用**（后端托管缺口，决定能否产生收入）；
② **仓库卫生与安全**（密钥/调试残留未 gitignore）；
③ **手机端练习进度易丢**（无 token 续期 + 状态全内存）。

---

## 维度一 · 业务命门：能不能真正上线

| 级别 | 问题 | 影响（人话） | 建议 |
|---|---|---|---|
| **P0** | 后端无公网托管方案。DEPLOY.md §0 已写明后端必须独立服务器，§7 承认“本机无外网、无法真部署” | 产品目前只能你本机 `uvicorn` 跑，外地/手机用户打不开，等于还没真正“上线” | 租一台轻量服务器（DEPLOY §3 已备好 Dockerfile），或用你已有方案；这是决定收入的前置条件 |
| P1 | backend 缺独立 DEPLOY 文档与启动脚本，`start_server` 仅绑 `127.0.0.1:8000` | 换人维护/换环境易踩坑 | 补 backend DEPLOY 说明 + 容器/PG 部署步骤 |

---

## 维度二 · 工程卫生与安全

| 级别 | 问题 | 影响 | 建议 |
|---|---|---|---|
| **P1** | 后端 `.gitignore` 仅忽略 `.tmp_exam_pages/`；`dev.db`(4.3MB)、6 个 `dev.db.bak-*`、`repro_500.py`、`_regression_driver.py`、`scripts/_audit_*.py` 均可被提交 | 数据库/密钥残留可能被误提交，泄露或膨胀仓库 | `.gitignore` 追加 `*.db*`、`_*.txt`、`_*.log`、`repro_*.py`、`scripts/_audit_*.py` |
| **P1** | 前端根目录残留 `.probe2.txt` `.probe3.txt` `_fe_check.txt` 等调试文件 + `dist-old/dist-qa/dist-new/dist-tmp/dist-voice` 多份构建副本，多数未 gitignore | 排查脚本随部署包外泄，部署包体积虚高 | 删除残留 + 补全前端 `.gitignore`（仅 `.probe.txt`/`_state.txt` 等少数已覆盖） |
| P2 | 后端根目录 30+ 个 `_*.txt`/`_*.log`、`_dbbackups/` 等开发残留 | 仓库脏、难定位真源码 | 清理或移入 gitignore 目录 |
| P2* | 工作区顶层（非本项目）散落大量 `_extract_*.py`、`_merge_*.js`、`_fix_*.js`、`cases.err`/`day1.err`、十几个 `_gitbackup_*` 目录、86M `_ocr_cache`、44M `_sample` | 整个工作区混杂，备份/缓存占大量磁盘 | 归到 `scratch/` 或压缩归档，与源码隔离 |

> P2* 为工作区级问题，不在本项目内，但影响你日常找文件。

---

## 维度三 · 后端代码质量

| 级别 | 问题 | 影响 | 建议 |
|---|---|---|---|
| **P1** | 写入卡口未集中强制：`quality_gate` 只在导入脚本调用（`generate_math_bank.py:852` 等），无统一 `create_question()` 封装，也无穷 `after_flush` 拦截 | 未来任一新端点直接 `db.add(Question, status='active')` 就绕过数据闸门，双不变式可能退化 | 建唯一 `create_question()` 强制调 gate，或用 SQLAlchemy `after_flush` 事件拦截 active 写入 |
| **P1** | 讲解质量闸门默认关闭：`config.py:89` `explanation_quality_gate_enabled=False` | AI 讲题实时质检生产默认不生效 | 模型稳定后默认开启，或加 CI 断言 |
| **P1** | 同步 AI 调用：`ark_client.py:296` 用 `httpx.Client` 同步 + `run_in_threadpool`，60s 超时（`config.py:73`） | 高并发时慢请求占满线程池，拖垮整个 API | 改 `httpx.AsyncClient` + `await` 并限流 |
| P2 | 抽题 4/5 档 fallback 为 TODO（`practice_service.py:437-509`），最坏返回 404 | 特定薄弱档位抽不到题 | 排期实现或文档明示限制 |
| P2 | 测试缺口：39 文件覆盖 practice/judge/weak，但缺 6 档 fallback 端到端、AI 讲题异步路径、`写入必过卡口` 契约测试 | 重构时无回归保护 | 补上述三类测试 |

---

## 维度四 · 前端 / 手机端体验

| 级别 | 问题 | 影响（人话） | 建议 |
|---|---|---|---|
| **P0** | 无 token 续期 + 进度全内存：`auth.ts:32` refresh 从未调用，`Login.vue:93` 存了不续期；`practiceState` 无持久化 | 孩子手机切后台/锁屏再回来，或 access 过期 → 触发 401 跳登录 → **做到一半的题全没了** | 实现 refresh 静默续期；401 先弹“保存并退出”而非静默跳登录 |
| **P0** | 并发 401 重跳：`http.ts:48-54` `handleUnauthorized` 无重入锁 | 多个并行请求同时 401 会多次跳登录、重复清 token，闪跳 | 加 module 级“已跳转”锁 |
| P1 | 重复组件：`QuestionCard.vue` 与 `practice/BankQuestionCard.vue` 注释“刻意不复用” | 双份维护，改一处易漏 | 抽公共题目渲染子组件复用 |
| P1 | 旧构建产物不清：`vite.config.ts:36` `emptyOutDir:false` | dist 旧 chunk 残留、占空间、易混淆 | CI 打包前 `rm -rf dist` 或改回 `true` |
| P2 | 无 vendor 代码分割（仅路由懒加载） | 手机首屏首包偏大 | 加 `manualChunks` 拆 vue/axios |
| P2 | 魔法字符串/文案散落（√/×、`小学/初中/高中`、错误提示硬编码） | 改文案需全局搜 | 建文案常量/字典 |
| P2 | 后端 schema 强耦合：`chat.ts` 大量 snake/camel 双写兼容，注释承认契约漂移 | 后端改名即前端 bug | 锁定 OpenAPI 并生成类型 |
| P2 | KaTeX 走 CDN（`index.html`） | 弱网/受限网络公式显示不稳 | 自托管或本地打包 KaTeX |

---

## 维度五 · 数据质量（已很好，维持）

- dev.db 实测：19 张表（含 5 张 `*_backup_*`）、questions 共 **3172**（active 2027 / archived 1145）。
- 用项目真实 `judge_engine` 复算：1165 道 active 选择题 **100% 含合法 A-D 且答案命中**，双不变式成立。
- 既有审计脚本（`.qa_cache/audit_*.py`）显示 I1-I4 不变式持续 OK、重同步被闸门挡掉 0。
- **维持**现有 `quality_gate` + `judge_engine` + 审计脚本，不要放松。

---

## 建议执行顺序

1. **能否上线**（P0 业务）：后端公网托管 —— 决定产品能否产生收入。
2. **手机端进度丢失**（P0 体验）：token 续期 + 进度持久化 —— 直接关系留存与口碑。
3. **gitignore + 清残留**（P1 卫生）：低风险、高收益，半小时可完成。
4. **卡口集中强制 + 讲解质控开启**（P1 数据护栏）：防未来数据退化。
5. **其余 P2**：排期，不阻塞上线。

---

## 我可以直接帮你执行的项

- 补后端/前端 `.gitignore` 并清理调试残留
- 前端加 token 静默续期 + 401 重入锁 + 进度本地持久化
- 后端加 `after_flush` 卡口拦截 + 开启讲解质控
- 后端 `ark_client` 改异步 + 限流
- 写一份 backend 部署（Docker/PG）文档

需要推哪些，告诉我即可。
