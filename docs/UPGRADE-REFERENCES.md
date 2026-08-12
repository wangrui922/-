# AI 数学教练 v4.1 升级路线图 · GitHub Skill 借鉴与改造清单

> 本文是 **v4.1 封装包** 的升级路线图附件。
> 来源：在 GitHub 上研究的 5 个最对口 skill/repo，结合本项目真实代码结构，给出「借鉴点 + 接入 v4.1 的具体改造清单」。
>
> ⚠️ **范围说明**：项目已按新规划重定基线为 **v0.1（tutor/ 单文件 Demo）**，商业化路线分 8 阶段推进。本文件记录的「skill 借鉴点 + 改造清单」对两种基线都适用：
> - **v4.1（cuotixiaolaoshi 全栈版）**：直接按下方文件级清单改 `backend/app/`。
> - **v0.1（tutor/）**：先在前端 `index.html` 的单文件闭环里实验，后续上后端时再迁移到服务端。
> 每个改造项都标注了「适用基线」。

---

## 0. 你给出的总览映射表

| 你的短板 | 借哪个 | 接法 |
|---|---|---|
| 无跨会话记忆/数据飞轮 | DeepTutor + anki-mcp | 加「掌握度表」+ 复习排程 |
| 苏格拉底提示词无范本 | Bloom | 抄 2-Sigma 脚手架提示词 |
| 错因分类太粗 | diagnostic-tutor | 五类缺口接 `judge_engine` |
| 题库只有 282 | examprep-skill | 资料→标准题库管线 |
| 讲解质量不可证 | examprep-skill | 🟢🟡⚠️ 溯源 + 抽检 rubric |
| 拍照入口堵死 | HMER + mathtutor | 视觉模型接拍照识题 |

---

## 1. 五个 Skill 详细改造清单

### 1.1 Bloom（Li-Evan/Bloom，⭐225）— 苏格拉底提示词范本
- **是什么**：React 19 + FastAPI + OpenAI 兼容 Key 的自托管 AI 家教。核心理念「1对1 辅导=前 2%」，用**脚手架循环**：`大纲→讲课→批注→反馈→下一课→测评→总结`。
- **借什么**：
  1. **2-Sigma 苏格拉底脚手架提示词**（渐进提示：不给答案、只给下一步提示）。
  2. **syllabus→lesson 循环** → 正好套到你的「错题→讲解→按错因推题→再测」主流程。
  3. **多 provider 抽象**（DashScope/DeepSeek 等 OpenAI 兼容 Key）→ 和你「国内 DeepSeek 免外网」路线一致。
- **接入 v4.1 的具体改造**：
  - `[v0.1]` 把 `tutor/public/index.html` 里现有的 `SYSTEM_PROMPT`（已写死「不直接给答案、一次只问一个」）对照 Bloom 2-Sigma 补全「批注→反馈→测评→总结」四环节，形成 v0.2 提示词。
  - `[v4.1]` 新增 `backend/app/services/tutor_prompt.py`，把 Socratic 提示词从前端挪到服务端统一管理（顺带解决前端硬编码 Key 隐患）。
  - `[v4.1]` `backend/app/core/config.py` 已有 AI 配置，对齐 Bloom 的 provider 抽象，支持「DeepSeek 文本 + 独立视觉模型」双 provider。
  - `[v4.1]` `backend/app/api/` 下新增 `/api/tutor/guide` 端点，承载「引导下一步」逻辑（目前 `server.js` 里另有一套未被前端使用的 `/api/tutor`，需收口统一）。

### 1.2 diagnostic-tutor（SenmuuuuW/universal-diagnostic-tutor-skill，⭐159）— 错因五类缺口
- **是什么**：纯 Markdown 的「教学行为层」，强调**先诊断缺口再教**。错因拆成 5 类缺口：**概念 / 前置知识 / 记号 / 方法 / 推理**；配 Practice&Mastery Loop（定性评分→纠错→就绪判定）。
- **借什么**：**错因 taxonomy（分类法）**——把粗粒度错因升级为五类缺口，让「按错因推题」推得更准；以及「就绪判定」状态机。
- **接入 v4.1 的具体改造**：
  - `[v4.1]` 在 `backend/app/services/judge_engine.py` 扩展错因枚举，从现有粗略类型（计算/概念/审题…）扩到五类缺口：`concept / prerequisite / notation / method / reasoning`，并保留 `normalize_options` 归一口径（**红线：判定只能有一处权威实现**）。
  - `[v4.1]` `backend/app/services/practice_service.py`（现有 6 档 fallback）按五类缺口选择针对性练习题，而非纯随机档位。
  - `[v4.1]` 新增「就绪判定」：某知识点连续正确 N 次（建议 3）才标记 `mastered`，否则继续在 `weak_profile` 里加权。
  - `[v0.1]` 前端 `index.html` 的错因展示先用一个简单下拉/标签预演五类，收集真实学生错因后再固化到后端。

### 1.3 examprep-skill（ZeKaiNie/universal-examprep-skill，⭐297）— 题库管线 + 溯源护栏
- **是什么**：把课件/作业/真题文件夹自动转成「带溯源标记的题库」，每条结论标 **🟢来自资料 / 🟡AI补充 / ⚠️AI编造**；资料不支持就明说「我不知道」。
- **借什么**：
  1. **自动判分题库扩充管线** → 你下一步 P0-2 把可判分池从 282 做到千级。
  2. **「不编造/可证伪」护栏 + 抽检 rubric** → 补你「AI 讲解质量不可证」短板。
- **接入 v4.1 的具体改造**：
  - `[v4.1]` 写 `scripts/import_from_textbook.py`：读资料（如 ChinaTextbook PDF/文本）→ 抽知识点+例题 → 调 `normalize_options` 归一 → 经 `importers/quality_gate.py` 卡口入库为「带溯源标记」标准题。
  - `[v4.1]` `backend/app/services/importers/quality_gate.py` 增加 `source_tag` 字段（`green/yellow/red`），凡 `red` 题入库即进入「待抽检队列」，未通过不对外抽题。
  - `[v4.1]` 新增 `backend/app/services/tutor_eval.py`：**讲解质量 rubric 自动评测 harness**，评分维度至少含：①是否直接给答案（应否）②提示是否渐进 ③错因判定是否命中五类 ④溯源标记是否诚实。定时跑 + 人工抽检兜底。
  - `[v0.1]` 前端 `index.html` 小结卡片先手动标注 🟢🟡⚠️，验证用户能否理解该信号。

### 1.4 DeepTutor（HKUDS/DeepTutor，⭐35k）+ anki-mcp（ankimcp/anki-mcp-server，⭐435）— 跨会话记忆 + 间隔重复
- **是什么**：
  - DeepTutor：每个账号有长期记忆 + 知识掌握度追踪（LightRAG/GraphRAG）；v1.5 有「keep-alive 修诡异 500」「多账号隔离」。
  - anki-mcp：通过 MCP 操作 Anki 闪卡，暴露 `get_due_cards / rate_card` 等工具，做「到期复习→评分→排下次」间隔重复。
- **借什么**：**账号级长期记忆 + 知识点掌握状态表**（数据飞轮），以及**间隔重复状态机**（留存飞轮）。
- **接入 v4.1 的具体改造**：
  - `[v4.1]` `backend/app/services/ai_exercise_service.py` 的 `get_weak_profile`（当前一次性薄弱画像）升级为**时间累积掌握度曲线**：`learning_records(user_id, kp, mastery_score, last_wrong, streak)`。
  - `[v4.1]` 新增复习排程表 `review_schedule(user_id, kp, due_at, last_rate)`，状态机照搬 anki-mcp 的 `due→rate→reschedule`。
  - `[v4.1]` 稳定性：参考 DeepTutor keep-alive，把现有「杀旧重启」SOP 升级为 uvicorn 健康检查自愈（已有 `server.js` P0-1b 启动器思路可复用）。
  - `[v0.1]` 前端 `index.html` 的 `localStorage` 错题本先扩成「本地掌握度草稿」，证明「越练越懂你」的体验，再上服务端。

### 1.5 HMER（whywhs/Pytorch-Handwritten-Mathematical-Expression-Recognition，⭐237）+ mathtutor-on-groq — 拍照识题 OCR
- **是什么**：手写数学公式识别（HMER）+ 语音/讲解里实时渲染 LaTeX。
- **借什么**：**拍照识题入口**（你 v4.1 的 OCR 始终没跑通——DeepSeek 纯文本、视觉走外部模型未落地）+ **LaTeX 渲染**。
- **接入 v4.1 的具体改造**：
  - `[v4.1]` 把 `tutor/public/index.html` 已有的「⚙ 填视觉 Key」入口真正打通：前端拍照 → 调独立视觉模型（火山方舟/智谱）→ 返回 LaTeX/文本 → 进识题流程。
  - `[v4.1]` 讲解 UI 渲染 LaTeX（引 KaTeX/MathJax），让学生看到规范公式而非纯文本。
  - `[v4.1]` 视觉模型 Key 一律走服务端代理（`backend/app/api/ocr.py`），绝不暴露 Key 到前端。
  - `[v0.1]` 先在 `index.html` 跑通「图片→视觉模型→题目文本」最小链路（已有 `ocrBtn` 雏形），验证拍照入口可用。

---

## 2. 映射到新阶段路线（AI 学习教练 8 阶段）

| 阶段 | 内容 | 对应借用的 skill |
|---|---|---|
| 阶段0 | 产品定位（初中数学·学生错题教练） | — |
| 阶段1 | Demo→MVP（v0.1 闭环跑通） | 已就绪（tutor/index.html） |
| 阶段2 | AI 错题核心引擎（上后端/DB） | Bloom（provider 抽象）、examprep（入库管线） |
| 阶段3 | AI 老师大脑（5 个 Skill） | Bloom（Socratic）、diagnostic-tutor（错因）、examprep（出题+护栏）、HMER（识题） |
| 阶段4 | 学生知识画像 | DeepTutor（掌握度表） |
| 阶段5 | 长期记忆 | DeepTutor + Personal Tutor（本地状态） + anki-mcp（间隔重复） |
| 阶段6 | 商业化 Beta | examprep（质量护栏保口碑） |
| 阶段7 | 商业化 | — |
| 阶段8 | AI 学习平台 | DeepTutor（知识图谱 RAG） |

**一句话**：这 5 个里，**Bloom 同栈最该先看代码**（FastAPI+前端，能直接抄结构）；**diagnostic-tutor 的错因模型**和 **examprep 的题库管线+护栏** 是补 v4.1 两条最弱链（规模/可证）的现成轮子。

---

## 3. 扩展参考（你后续问到的 5 个 skill）

| Skill | 仓库 | 借鉴点 | 接法 |
|---|---|---|---|
| education-skills | flysheep-ai/education-skills（⭐81） | 中文 K12 苏格拉底渐进引导话术、鼓励式语言 | 直接做 `tutor_prompt.py` 的中文提示词范本（理科导师覆盖数/物/化/生） |
| Personal Tutor | briannajzhang/personal-tutor（⭐11） | 本地状态存储（课程/进度/测验历史）+ 组件化教学 UI | 补 v4.1「无跨会话记忆」：本地 `~/.personal-tutor` 思路 → `learning_records` 前身 |
| DeepTutor Skill | ndpvt-web/deeptutor-claude-skill（⭐25） | 双环解题（分析环+解题环）+ 知识图谱 RAG + 难度校准出题 | 阶段3 讲解引擎范式；轻量，只取方法论 |
| school-skills | Jellypod-Inc/school-skills（⭐6） | **rubric 评分量规** + **概念图** | 直接做你缺的两样：讲解质量评测 harness + 知识点图谱可视化 |
| ChinaTextbook | TapXWorld/ChinaTextbook（⭐79k） | 小初高+大学 PDF 教材开源库（人教版数学全年级） | P0-2 扩题库的**课标/教材内容源**，批量抽知识点+例题 |

> ⚠️ 不要一上来就 clone 整个 ChinaTextbook（79k 星、体量巨大）。先用 `examprep-skill` 的管线思路，按需抓取初中数学对应分册即可。

---

## 4. 优先级排序与落地顺序（不重复造轮子）

按「投入小、补最弱链、可独立验证」排序：

1. **P0（规模/可证，最弱链）**：`examprep-skill` 题库管线 + 溯源护栏 → 把 282 扩到千级，建立质量 rubric。
2. **P0（入口）**：`HMER + mathtutor` 拍照识题 → 打通 v0.1 的 `ocrBtn` 最小链路。
3. **P1（教得好）**：`Bloom` 2-Sigma 提示词 + `diagnostic-tutor` 五类错因 → 升级 `tutor_prompt` 与 `judge_engine`。
4. **P1（留得住）**：`DeepTutor + anki-mcp` 掌握度表 + 复习排程 → 数据飞轮。
5. **P2（长大）**：`DeepTutor` 知识图谱 RAG、`school-skills` 概念图 → 平台化。

---

## 5. 红线提醒（沿用 v4.1 工程纪律）

- **零迁移红线**：数据清洗只做 `UPDATE status='archived'`（可逆），不加表/列/CHECK；留痕走独立备份表 + CSV 清单。
- **判定口径唯一**：错因/归一一律复用 `judge_engine.normalize_options`，禁用朴素 SQL 误报。
- **数据质量三道防线**：①`quality_gate` 入库卡口 ②`scripts/data_cleanup` 全库清洗 ③`test_practice` 全库不变式。
- **git 安全**：`origin` 从未真正配置，所有 commit 仅本地；大改动后必冷备（robocopy `.git` 到库外 `_gitbackup_*`）。
- **密钥不下前端**：DeepSeek/视觉 Key 一律服务端代理，前端不得硬编码（v0.1 先实验，阶段2 必须收口）。
- **进程常驻**：uvicorn 长跑偶发 500，靠「杀旧重启」SOP；阶段4+ 升级为健康检查自愈。
