# TTS Provider 可切换改造说明

> 适用范围：读题 / 讲题的云端语音合成（`POST /api/tts`）。
> 代码位置：`backend/app/services/tts_service.py`（抽象层）、`backend/app/api/tts.py`（接口层）、`backend/app/core/config.py`（配置项）。
> 回归测试：`backend/tests/test_tts_provider.py`。

---

## 1. 为什么要改造

| 方案 | 优点 | 风险 |
| --- | --- | --- |
| `edge`（微软 Edge TTS，现状） | 免 Key、免费、国内可达、不用翻墙 | **逆向微软内部接口**：无官方 SLA，随时可能停服或限流；商用存在合规风险 |
| `volc`（火山引擎语音技术） | 有合同、有 SLA、可开票、音色丰富 | 需要 Key、按量计费 |
| `piper`（本地离线） | 完全离线、零边际成本 | 尚未实现（仅声明接口与配置位） |

**结论**：60 人免费内测期继续用 `edge`（零成本），备案 / 商用前把 `TTS_PROVIDER` 改成 `volc` 即可，**业务层与前端零改动**；任何 provider 失败都自动降级到 `edge`，保证「永远有声音」。

---

## 2. 三种 Provider 对照

| provider | 配置项开关 | 依赖 | 默认嗓音 | 状态 |
| --- | --- | --- | --- | --- |
| `edge` | `TTS_PROVIDER=edge`（**默认**） | `edge-tts`（已在 requirements） | `zh-CN-XiaoxiaoNeural`（甜美小云朵） | 可用，内测期默认 + 最终兜底 |
| `volc` | `TTS_PROVIDER=volc` | `httpx`（已是硬依赖）；官方 SDK 可选 | `BV001_streaming`（通用女声） | 可用，备案 / 商用切换目标 |
| `piper` | `TTS_PROVIDER=piper` | 本地 piper 可执行文件 + 中文模型 | 由 `TTS_VOICE` 决定 | **未实现**，选中会抛 `NotImplementedError` 并自动降级到 `edge` |

嗓音别名（前端可传，三种 provider 都能识别）：`xiaoxiao`（甜美女声，默认）、`yunxi`（阳光男声）、`xiaoyi`（温柔女声）。
`volc` 侧额外支持直接传火山音色 ID（形如 `BV700_streaming`）。

---

## 3. 环境变量（**密钥一律走环境变量，不进代码 / 不进文档**）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TTS_PROVIDER` | `edge` | 主 provider：`edge` / `volc` / `piper`；非法值回落 `edge` |
| `TTS_VOICE` | 空 | 全局默认嗓音；留空用各 provider 自带默认 |
| `TTS_FALLBACK_ENABLED` | `true` | 主 provider 失败时是否自动降级到 `edge`（**内测期务必保持 true**） |
| `TTS_VOLC_APP_ID` | 空 | 火山语音技术控制台的 AppID |
| `TTS_VOLC_ACCESS_TOKEN` | 空 | 火山 Access Token；**留空时自动复用 `ARK_API_KEY`**（同一火山账号） |
| `TTS_VOLC_CLUSTER` | `volcano_tts` | 火山集群标识（以控制台为准） |
| `TTS_VOLC_BASE_URL` | `https://openspeech.bytedance.com/api/v1/tts` | 接口地址（切 v3 / 内网网关时改这个） |
| `TTS_VOLC_VOICE_TYPE` | `BV001_streaming` | 默认音色（别名未命中时使用） |
| `TTS_VOLC_ENCODING` | `mp3` | 音频编码：`mp3` / `wav` / `ogg` / `pcm` |
| `TTS_VOLC_TIMEOUT_SEC` | `15` | 单次请求超时（秒） |
| `TTS_PIPER_EXECUTABLE` | 空 | 本地 piper 可执行文件路径（尚未实现） |
| `TTS_PIPER_MODEL_PATH` | 空 | 本地 piper 中文模型路径（尚未实现） |

`.env` 样例见仓库根目录 `.env.example` 的「语音合成 TTS」小节；复制后填自己的值即可，**不要把真实 Key 提交进 git**。

---

## 4. 启用方式（一行切换，重启生效）

```bash
# 内测期（默认，零成本）
TTS_PROVIDER=edge

# 备案 / 商用（火山引擎）
TTS_PROVIDER=volc
TTS_VOLC_APP_ID=你的AppID
TTS_VOLC_ACCESS_TOKEN=你的AccessToken   # 也可留空复用 ARK_API_KEY
TTS_FALLBACK_ENABLED=true
```

改完 `.env` 重启后端即可，**前端与业务层无需任何改动**（`POST /api/tts` 的入参、返回音频流契约不变）。

---

## 5. 降级路径

```
调用 POST /api/tts
   └─> tts_service.synthesize()
         ├─ 文本清洗（空 -> 400；超 2000 字截断）
         ├─ 主 provider（读 TTS_PROVIDER）
         │     ├─ volc：httpx 未装 / AppID 未配 / Token 缺失 / 上游报错 -> 抛错
         │     └─ piper：抛 NotImplementedError（尚未实现）
         └─ 失败且 TTS_FALLBACK_ENABLED=true 且当前不是 edge
               └─> 回落 edge（免 Key）
                     └─ edge 也失败 -> TtsError -> 接口返回 500
                                             -> 前端自动回退浏览器自带嗓音
```

关键保障：

1. **依赖全部 lazy import**（`edge_tts` / `httpx` 只在函数内部 import）——缺依赖永远只影响那一个通道，**应用照常启动**；
2. 火山 SDK（`volcengine-python-sdk`）**不是必需项**：内置实现走 HTTP 接口 + `httpx`，免编译、Windows 友好；未安装时 `volc` 通道照常可用，真出问题时也只是降级到 `edge`；
3. 前端 `speak()` 云端失败会自动回退浏览器嗓音，所以最坏情况是「音色差一点」，不会「没声音」。

---

## 6. 自检

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
    "providers": [
      { "name": "edge",  "available": true,  "reason": "免 Key，内测期默认与最终兜底" },
      { "name": "volc",  "available": false, "reason": "TTS_VOLC_APP_ID 未配置" },
      { "name": "piper", "available": false, "reason": "本地离线兜底尚未实现，选中会自动降级到 edge" }
    ]
  }
}
```

`voice` 为当前 provider 的默认音色，取前端可回传的别名形态：`edge` 为 `xiaoxiao`；`volc` 为 `settings.tts_volc_voice_type`（默认 `BV001_streaming`），可直接作为 `POST /api/tts` 的 `voice` 入参回传。

该接口**只读、不发起任何网络请求**，可放心给运维与前端做健康检查。

---

## 7. 内测期建议与切换时点

| 阶段 | 建议配置 | 理由 |
| --- | --- | --- |
| 60 人免费内测 | `TTS_PROVIDER=edge` + `TTS_FALLBACK_ENABLED=true` | 零成本、零接入门槛，够用 |
| ICP 备案 / 商用前 | 切 `TTS_PROVIDER=volc`，先灰度一台机器 | 消除逆向接口的停服与合规风险 |
| 切后观察 1~2 天 | 盯 `/api/tts/providers` 与后端日志 `app.services.tts` | 有 `TTS provider volc 合成失败，准备降级` 日志即说明在兜底，需排查凭据/额度 |
| 长期（可选） | 落地 `piper` 本地兜底 | 断网 / 内网部署也能朗读 |

**回滚**：把 `TTS_PROVIDER` 改回 `edge` 重启即可，无需改代码、无需数据迁移。

---

## 8. 测试与验收

```bash
cd backend && python -m pytest tests/test_tts_provider.py -q
```

覆盖点（全部 mock，不联网、不依赖真实 SDK）：

- provider 选择：默认 / 大小写归一化 / 未知值回落 `edge`；
- 降级：volc 凭据缺失、httpx 未安装、piper 未实现 -> 均自动回落 `edge`；
- 关闭兜底开关后不再降级（便于上线前暴露配置问题）；
- 入参：空文本报错、超长截断、语速与嗓音解析；
- 契约：`POST /api/tts` 仍返回 `audio/mpeg` 字节流；新增 `GET /api/tts/providers` 返回统一响应；
- 静态守卫：TTS 相关第三方依赖不得出现在模块顶层 import。
