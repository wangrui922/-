# TTS Provider 可切换改造说明

> 适用范围：读题 / 讲题的云端语音合成（`POST /api/tts`）。
> 代码位置：`backend/app/services/tts_service.py`（抽象层）、`backend/app/api/tts.py`（接口层）、`backend/app/core/config.py`（配置项）。
> 回归测试：`backend/tests/test_tts_provider.py`。
> 最近一次协议升级：2026-11-05，`volc` 默认切到 **v3 / 豆包语音合成模型 2.0**（真实凭据实测）。

---

## 1. 为什么要改造

| 方案 | 优点 | 风险 |
| --- | --- | --- |
| `edge`（微软 Edge TTS） | 免 Key、免费、国内可达 | **逆向微软内部接口**：无官方 SLA，随时可能停服或限流；商用存在合规风险 |
| `volc`（火山引擎豆包语音） | 有合同、有 SLA、可开票、教学音色自然 | 需要 Key、按字数计费（试用包 20,000 字数，到期 2027-05-05） |
| `piper`（本地离线） | 完全离线、零边际成本 | 尚未实现（仅声明接口与配置位） |

**结论**：60 人免费内测期继续用 `edge`（零成本）；需使用教学音色时把 `TTS_PROVIDER` 改成 `volc` 即可，**业务层与前端零改动**；任何 provider 失败都自动降级到 `edge`，保证「永远有声音」。

---

## 2. 三种 Provider 对照

| provider | 配置项开关 | 依赖 | 默认嗓音 | 状态 |
| --- | --- | --- | --- | --- |
| `edge` | `TTS_PROVIDER=edge`（**默认**） | `edge-tts`（已在 requirements） | `zh-CN-XiaoxiaoNeural`（甜美小云朵） | 可用，内测期默认 + 最终兜底 |
| `volc` | `TTS_PROVIDER=volc` | `httpx`（已是硬依赖）；SDK 可选 | `zh_female_vv_uranus_bigtts`（2.0 通用女声，**用户试听确认**） | 可用，默认走 v3 流式 |
| `piper` | `TTS_PROVIDER=piper` | 本地 piper 可执行文件 + 中文模型 | 由 `TTS_VOICE` 决定 | **未实现**，选中会自动降级到 `edge` |

嗓音别名（前端可传）：`xiaoxiao`（默认）、`yunxi`、`xiaoyi`。
`volc` 侧当前 2.0 只实测通过一款音色，故三个别名统一解析到 `zh_female_vv_uranus_bigtts`；直接传原生音色 ID（`BV*` 或 `zh_*`）可直通。

---

## 3. 环境变量（**密钥一律走环境变量，不进代码 / 不进文档**）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TTS_PROVIDER` | `edge` | 主 provider：`edge` / `volc` / `piper`；非法值回落 `edge` |
| `TTS_VOICE` | 空 | 全局默认嗓音；留空用各 provider 自带默认 |
| `TTS_FALLBACK_ENABLED` | `true` | 主 provider 失败时是否自动降级到 `edge`（**务必保持 true**） |
| `TTS_VOLC_APP_ID` | 空 | 豆包语音控制台「服务接口认证信息」里的 AppID |
| `TTS_VOLC_ACCESS_TOKEN` | 空 | **语音应用独立 Access Token**；★ 与 `ARK_API_KEY` 不是一套，**留空不再复用**，缺失直接判通道不可用 |
| `TTS_VOLC_API_VERSION` | `v3` | `v3` = 语音合成模型 2.0（默认）；`v1` = 经典小模型（官方已标不推荐，仅回退） |
| `TTS_VOLC_RESOURCE_ID` | `seed-tts-2.0` | ★ v3 资源 ID，**必须与音色同属一套**；2.0 音色固定此值 |
| `TTS_VOLC_BASE_URL` | 空 | 留空按 `API_VERSION` 自动选；也可填内网网关 |
| `TTS_VOLC_VOICE_TYPE` | `zh_female_vv_uranus_bigtts` | 默认音色（别名未命中时使用） |
| `TTS_VOLC_CLUSTER` | `volcano_tts` | 仅 `v1` 使用 |
| `TTS_VOLC_ENCODING` | `mp3` | 音频编码：`mp3` / `wav` / `ogg` / `pcm` |
| `TTS_VOLC_TIMEOUT_SEC` | `15` | 单次请求超时（秒） |
| `TTS_PIPER_EXECUTABLE` / `TTS_PIPER_MODEL_PATH` | 空 | 本地 piper 占位，尚未实现 |

`.env` 样例见仓库根目录 `.env.example` 的「语音合成 TTS」小节。真实 Key 只写在服务器上被 `.gitignore` 保护的 `deploy/.env.production` 里，**绝不提交进 git**。

---

## 4. v3 协议（豆包语音合成模型 2.0）

2026-11-05 用真实凭据穷举 2 个 resource-id × 10 个音色，实测结论：

| 项 | 值 |
| --- | --- |
| 端点 | `POST https://openspeech.bytedance.com/api/v3/tts/unidirectional` |
| Headers | `X-Api-App-Id` / `X-Api-Access-Key` / `X-Api-Resource-Id` / `X-Api-Request-Id` / `Content-Type` |
| Body | `user.uid`、`req_params.text`、`req_params.speaker`、`req_params.audio_params.{format,sample_rate}` |
| 实测唯一出声组合 | `X-Api-Resource-Id: seed-tts-2.0` + `speaker: zh_female_vv_uranus_bigtts` |
| 响应 | **流式 NDJSON**：N 行 `{"code":0,"data":"<base64 分片>"}`，末行 `{"code":20000000,"message":"OK","data":null}` |
| 拼接 | **逐行 `base64.b64decode` 后按顺序拼字节**；不可先把 base64 字符串接起来再解码 |

已知错误码：

| 码 | 含义 | 处理 |
| --- | --- | --- |
| `55000000` | `resource ID is mismatched with speaker related resource` | resource-id 与音色不匹配：1.0 的 `*_moon_bigtts` / `*_mars_bigtts` 配 `seed-tts-2.0` 必报；换 2.0 音色 |
| `403 / 45000030` | `requested resource not granted` | 该 resource 未开通（如 `volc.service_type.10029`） |
| `403 / 3001` | `volc.tts.default not granted` | v1 经典接口未开通，所以**必须走 v3** |

**凭据陷阱**：语音应用的 Access Token 与火山方舟 `ARK_API_KEY` 是两套凭据。早期代码在 token 留空时复用方舟 Key，会鉴权失败后静默降级到 `edge`，表现为「以为接了火山、其实一直在用 edge」。现已移除该兜底，缺失直接报「通道不可用」。

---

## 5. 启用方式（一行切换，重启生效）

```bash
# 内测期（默认，零成本）
TTS_PROVIDER=edge

# 使用教学音色（火山 v3 / 2.0）
TTS_PROVIDER=volc
TTS_VOLC_APP_ID=<控制台 AppID>
TTS_VOLC_ACCESS_TOKEN=<控制台 Access Token>
TTS_VOLC_API_VERSION=v3
TTS_VOLC_RESOURCE_ID=seed-tts-2.0
TTS_VOLC_VOICE_TYPE=zh_female_vv_uranus_bigtts
TTS_FALLBACK_ENABLED=true
```

改完 `.env` 重启后端即可，**前端与业务层无需任何改动**（`POST /api/tts` 入参、返回音频流契约不变）。

上线前用冒烟脚本验真实链路（强制指定 `volc`、关闭降级，失败直接暴露火山原始错误）：

```bash
cd backend
export TTS_VOLC_APP_ID=... TTS_VOLC_ACCESS_TOKEN=...
python scripts/tts_smoke.py
# 成功会打印 [ok] 并把 mp3 落到 backend/.qa_cache/tts_volc_smoke_<日期>.mp3
```

---

## 6. 降级路径

```
调用 POST /api/tts
   └─> tts_service.synthesize()
         ├─ 文本清洗（空 -> 400；超 2000 字截断）
         ├─ 主 provider（读 TTS_PROVIDER）
         │     ├─ volc：凭据缺失 / httpx 未装 / HTTP 4xx / 业务错误码 / NDJSON 无分片 -> 抛错
         │     └─ piper：抛 NotImplementedError（尚未实现）
         └─ 失败且 TTS_FALLBACK_ENABLED=true 且当前不是 edge
               └─> 回落 edge（免 Key）
                     └─ edge 也失败 -> TtsError -> 接口返回 500
                                             -> 前端自动回退浏览器自带嗓音
```

关键保障：

1. **依赖全部 lazy import**（`edge_tts` / `httpx` 只在函数内部 import）——缺依赖只影响那一个通道，**应用照常启动**；
2. 火山 SDK（`volcengine-python-sdk`）**不是必需项**：内置实现走 HTTP + `httpx`，免编译、Windows 友好；
3. 前端 `speak()` 云端失败会自动回退浏览器嗓音，最坏情况是「音色差一点」，不会「没声音」。

---

## 7. 自检

```bash
curl http://localhost:8000/api/tts/providers
```

返回（统一响应，camelCase）：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "provider": "edge",
    "fallbackEnabled": true,
    "fallbackProvider": "edge",
    "voice": "xiaoxiao",
    "maxTextLength": 2000,
    "volcApiVersion": "v3",
    "volcResourceId": "seed-tts-2.0",
    "providers": [
      { "name": "edge",  "available": true,  "reason": "免 Key，内测期默认与最终兜底" },
      { "name": "volc",  "available": false, "reason": "TTS_VOLC_APP_ID 未配置" },
      { "name": "piper", "available": false, "reason": "本地离线兜底尚未实现，选中会自动降级到 edge" }
    ]
  }
}
```

`volcApiVersion` / `volcResourceId` 是 2026-11-05 新增字段，用于确认线上真的在用 v3 与正确资源 ID。
`voice` 为当前 provider 的默认音色，`edge` 下发 `xiaoxiao`，`volc` 下发 `TTS_VOLC_VOICE_TYPE`，可直接回传给 `POST /api/tts`。

该接口**只读、不发起任何网络请求**。

---

## 8. 切换时点与回滚

| 阶段 | 建议配置 | 理由 |
| --- | --- | --- |
| 60 人免费内测 | `TTS_PROVIDER=edge` + `TTS_FALLBACK_ENABLED=true` | 零成本、零接入门槛 |
| 需要教学音色 / 备案商用 | 切 `TTS_PROVIDER=volc`，先灰度一台 | 消除逆向接口的停服与合规风险 |
| 切后观察 1~2 天 | 盯 `/api/tts/providers` 与后端日志 `app.services.tts` | 有 `TTS provider volc 合成失败，准备降级` 日志即说明在兜底，需查凭据与额度 |
| 长期（可选） | 落地 `piper` 本地兜底 | 断网 / 内网部署也能朗读 |

**额度提醒**：试用包 20,000 字数、已用从 0 起算、到期 2027-05-05；服务开通后计费方式为「按字数后付费」，额度用尽或到期会自动按量计费。建议在火山费用中心设预算告警 / 用量提醒。

**回滚**：把 `TTS_PROVIDER` 改回 `edge` 重启即可，无需改代码、无需数据迁移；协议级回滚可把 `TTS_VOLC_API_VERSION` 改回 `v1`（仅当账号开通了经典小模型）。

---

## 9. 测试与验收

```bash
cd backend && python -m pytest tests/test_tts_provider.py -q
```

覆盖点（全部 mock，不联网、不依赖真实 SDK）：

- provider 选择：默认 / 大小写归一化 / 未知值回落 `edge`；
- 降级：凭据缺失、httpx 未安装、piper 未实现 -> 均自动回落 `edge`；关闭兜底开关后不再降级；
- **凭据守卫**：`TTS_VOLC_ACCESS_TOKEN` 缺失时**不得**复用 `ARK_API_KEY`，必须直接报不可用；
- **v3 golden 真值**：实测出声组合（resource-id + 音色 + 端点 + 终止码）逐项写死断言；
- **NDJSON 拼接**：3 个不同长度分片手算 `b"AAABBC"` 真值，验证「逐行解码再拼字节」；已做变异测试（故意改成「先拼字符串再解码」，该用例确实变红）；
- **错误路径**：`55000000` 音色不匹配、只有终止行无分片、HTTP 403 未授权 -> 均抛清晰错误，不静默返回空音频；
- **可配置性**：`TTS_VOLC_RESOURCE_ID` 可覆盖；`TTS_VOLC_API_VERSION=v1` 仍走经典单 JSON 协议；
- **语速**：默认不下发 `speech_rate`，显式传 `rate` 才带整数；
- 契约：`POST /api/tts` 仍返回 `audio/mpeg` 字节流；`GET /api/tts/providers` 返回统一响应并下发协议版本；
- 静态守卫：TTS 相关第三方依赖不得出现在模块顶层 import。
