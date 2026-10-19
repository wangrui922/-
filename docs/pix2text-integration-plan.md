# Pix2Text 引入计划（侦察结论转开题）

- 日期：2026-10-20
- 作者：oss-scout（技术侦察）
- 状态：开题计划（等评审后排期；本轮只出计划，不装依赖、不改业务代码）
- 上游依据：《技术侦察简报》（GitHub 同类项目 5 维度核查，许可证逐个验证原文）
- 工作树：`F:/错题教练/_wt/integrate`（develop @ 0c27f64）

---

## ① 侦察结论回顾（已刷新至最新）

| 项 | 值 |
|---|---|
| 仓库 | `breezedus/Pix2Text`（https://github.com/breezedus/Pix2Text） |
| 定位 | "Mathpix 平替"：版面/表格/数学公式(LaTeX)/文本 → Markdown，小模型，80+ 语言 |
| star | 3274（forks 281，open issues 43） |
| 最近提交 | pushed 2026-08-23（近 6 个月活跃，符合入选口径） |
| 最新版本 | **v1.1.7**（2026-08-23）；新特性：`api_base` 支持 OpenAI 兼容 VLM 端点（含 MiniMax-M3），文本/公式识别 API 透传 VLM 参数——即「本地小模型」与「云端 VLM」两条路都留好了口子 |
| 许可证 | **MIT**（LICENSE 原文已验证：允许商用、修改、分发，保留版权声明即可） |
| 商用结论 | 绿色，可直接引入 |

同赛道备选（不采用）：`lukas-blecher/LaTeX-OCR`（16.5k★，MIT，单公式 ViT→LaTeX，可作 PoC 对照基线）；`RQLuo/MixTeX-Latex-OCR`（1.6k★，**AGPL-3.0**，网络服务触发源码披露，禁入）；`LinXueyuanStdio/LaTeX_OCR_PRO`（1.3k★，**GPL-3.0**，禁入）；`VikParuchuri/texify`（1.1k★，仓库已归档，观察）。

## ② 集成点现状（仓库文件路径 + 行号）

本项目有**两条独立的 OCR 链路**，Pix2Text 的价值与风险完全不同，必须分开决策：

### A. 在线拍题识题（产品运行时，链路不动）

| 环节 | 文件:行号 | 现状 |
|---|---|---|
| 入口 | `backend/app/api/chat.py:44,102` | `ChatRequest.image` → `_strip_data_url` 剥 data URL |
| 编排 | `backend/app/ai/orchestrator.py:445` | `question.analyze_question(text=, image_b64=)` |
| 读图 | `backend/app/ai/agents/question.py:87-124` | `ocr_image` / `ocr_image_async`：调方舟多模态（`ark_respond` + `app/ai/prompts/ocr.py` PROMPT v1.1.0）转写题面 |
| 分析 | `backend/app/ai/agents/question.py:186-190` | 二次方舟调用出 JSON（knowledge/difficulty/type/confidence…） |
| 兜底 | `backend/app/ai/orchestrator.py:95-116` | `_confidence_gate`：置信度 < `CONFIDENCE_RETHRESHOLD` → `need_rephoto`，不建会话 |

**结论：运行时读图已是 LLM 多模态 + 置信度重拍闸门，60 人内测期不引入 Pix2Text 旁路**（增加 ~200MB 部署重量换不确定的精度增益，ROI 为负；留作二期评估项）。

### B. 离线题库导入管线（Pix2Text 一期唯一落点）

| 环节 | 文件:行号 | 现状 |
|---|---|---|
| OCR 执行 | `F:/错题教练/04-题库与OCR资料/根目录脚本与题库/_ocr_wangchaoxia.py:37-44` | `ocr_page()`：subprocess 调 Tesseract 5.4（`C:\Program Files\Tesseract-OCR\tesseract.exe -l chi_sim+eng --psm 3`） |
| 缓存 | 同上 `:63-92` | `ocr_with_cache()`：渲染 PNG + OCR 文本 JSON 落 `OCR缓存/`（86M / 109 文件），避免重跑 |
| 清洗 | 同上 `:95-113` | `clean_ocr_text()`：去水印/页码行（王朝霞教辅水印 Regex） |
| 切题 | 同上 `:126-150` | `extract_questions()`：按"数字．题干"正则切题 |

**现状痛点（PoC 要量化的问题）**：抽样 `OCR缓存/26秋…预备二年级…_ocr.json`，Tesseract 输出为逐字加空格文本（"Z 二 二 二 一 一 <"）与成片乱码（"oasosopoooogpgmoppag"），数学符号/图形信息基本丢失；**教材题仅部分入库（抽样 38%）**。这正是侦察简报判定的「公式 OCR 重复造轮子重灾区」——自研无意义，Pix2Text 是现成替代。

## ③ 依赖与部署成本

| 面 | 现状 | Pix2Text 进入后的成本 |
|---|---|---|
| Windows 本机 | `backend/start_server.bat/ps1` 直接 uvicorn；`backend/requirements.txt` 无 onnx/torch 依赖 | **不进主 requirements**。PoC 与离线管线用独立 venv / 独立脚本目录（`04-题库与OCR资料/根目录脚本与题库/`），预估安装体积 onnxruntime + opencv-python + cnstd/cnocr 栈 + 模型文件 **~200-300MB**（PoC 实测核实后回填本表） |
| 云端 | `backend/Dockerfile`（python:3.12-slim + gunicorn/uvicorn + celery/redis）、`docker-compose.yml`（SQLite volume） | **不进后端镜像**。离线导入是开发机/批处理作业，不占云部署面；避免拖慢 CI 与镜像分发 |
| `.qa_cache` 关系 | `backend/.qa_cache/` 现为空目录；曾被 gitignore 双保险覆盖（根 `.gitignore:111` + `backend/.gitignore:19`），历史 QA 评测下载的 onnx 模型缓存从未入 git | PoC 若产生模型缓存同样落 `.qa_cache/`，**不污染仓库、不进任何 commit**；但要在 PoC 报告里登记实际占用 |

## ④ PoC 方案（可执行）

**选图（5 张，全部现成）**：
1. `04-题库与OCR资料/样例/预备二_p6.png`、`预备二_p60.png`（2 张官方样例）
2. `04-题库与OCR资料/OCR缓存/p0006.png`、`p0007.png`、`p0008.png`（3 张教材渲染页，**已有 Tesseract 现存输出**可直接对比）

**脚本设计**（PoC 阶段不提交业务代码；脚本落 `04-题库与OCR资料/根目录脚本与题库/_poc_pix2text.py`，随离线资料走，不进仓库）：

```
1. p2t = Pix2Text(); 对 5 张图 recognize_text_formula() → Markdown/LaTeX
2. 从 OCR缓存/*_ocr.json 载入同图 Tesseract 输出
3. 指标计算（每页人工抽 3 题建真值，共 15 题）：
   - 题面 CER（字错率）：Pix2Text vs Tesseract vs 人工真值
   - 数学符号保真：分数/根号/单位/括号抽检正确率（math-verify parse 可解析率）
   - 可切题率：现有 extract_questions() 切出题数 / 人工题数
   - 水印乱码行率：命中 WATERMARK_RE 之外的可疑行占比
   - 单页耗时（CPU）
4. 输出对比表 + 引入判定
```

**评判标准（引入门槛，全部满足才进一期改造）**：
- 题面 CER 较 Tesseract **下降 ≥ 40%**；
- 数学符号抽检正确率 **≥ 90%**（分数/根号/单位不漏不变形）；
- 可切题率 **≥ 80%**（直接对应对库率 38% → 70%+ 的目标）；
- 单页 CPU 耗时 **< 5s**（86M 缓存重跑可接受）。

## ⑤ 引入决策建议 + 回滚 + 工作量

**决策：引入，但限定范围——一期只改造离线题库导入管线，运行时链路原样不动。**

- 一期改造（PoC 达标后）：`_ocr_wangchaoxia.py:37` `ocr_page()` 增加引擎开关（`OCR_ENGINE=tesseract|pix2text`，默认 tesseract），Pix2Text 分支 lazy import + `ImportError` 兜底——与 `backend/requirements.txt` 里 sympy/math-verify/fsrs 既有的「函数内 lazy import + 降级」纪律完全同构；重跑 5 册 PDF 缓存，对比入库率。
- 回滚方案：环境变量切回 `tesseract` 即整体回退；OCR 缓存 JSON 不动、无数据损失；新代码零侵入运行时，回滚成本 ≈ 0。
- 二期预留（内测后评估，默认不做）：运行时低置信度重拍前加 Pix2Text 本地复核（`orchestrator.py:448` gate 之前），或公式结构化提取喂判题/Geogebra 场景；需先过部署重量与延迟实测。
- 工作量估算：PoC **1.5 人日**（环境 0.5 + 脚本 0.5 + 人工真值 0.5）＋ 一期管线改造 **2 人日**（引擎开关/清洗适配/重跑对账）= **3.5 人日**；二期旁路另计 3 人日。

**最大风险**：模型对「小学教辅扫描件 + 中文手写 + 图形内嵌信息」的适配未经实测（v1.1.7 的中文公式能力依赖其开源模型，PoC 的 CER/符号保真两项若任一不达标，本计划整体不进入改造，仅保留 LaTeX-OCR 对照结论）。
