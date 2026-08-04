/**
 * 云端语音合成中转函数（EdgeOne Makers Node.js Function）
 *
 * 路由：/api/tts  （文件位置 cloud-functions/api/tts.js 自动映射）
 *
 * 职责：
 *  - 接收前端 POST { text, voice? }
 *  - 通过 WebSocket 调用微软 Edge 浏览器"大声朗读"用的 TTS 服务，把文字合成 mp3
 *  - 以 audio/mpeg 二进制返回给前端，由前端用 <audio> 播放
 *
 * 设计要点：
 *  - 本函数【不持有任何 Key】：Edge TTS 走的是免鉴权的 TrustedClientToken 通道，
 *    与 /api/chat "中转不存 Key" 的原则一致
 *  - 服务端合成，彻底摆脱手机浏览器（微信 / 部分安卓）没有本地语音引擎的问题
 *
 * WebSocket 传输层（重要）：
 *  EdgeOne Makers 的函数运行时【没有全局 WebSocket】，因此按优先级选用：
 *   1. `ws` 库（若运行时能解析到该依赖，见同目录 package.json）
 *   2. 内置的极简 WebSocket 客户端（基于 node:tls 自行实现 RFC6455，零依赖兜底）
 *  之所以保留兜底：打包器（esbuild）把裸模块名当作 external 外部依赖处理，
 *  一旦部署环境没有真正安装 ws，就会在运行时报 "Cannot find module 'ws'"。
 *  有了 node:tls 兜底，无论依赖是否安装成功，语音功能都能正常工作。
 */

import { createHash, randomUUID, randomBytes } from 'node:crypto';
import { connect as tlsConnect } from 'node:tls';

/* ===== 常量配置 ===== */

// Edge 浏览器朗读服务的公开客户端令牌（微软公开固定值，非用户密钥、非付费 Key）
const TRUSTED_CLIENT_TOKEN = '6A5AA1D4EAFF4E9FB37E23D68491D6F4';

// Sec-MS-GEC 防重放令牌的版本号。
// 实测服务端只卡"版本不能太旧"（如 1-130 会直接 403，1-140 及以上正常），
// 因此首选一个足够高的版本号，避免代码随时间推移而失效；
// 万一将来服务端改为校验真实发布版本，再按列表依次回退到真实版本。
// 按顺序尝试，命中后缓存，后续请求直接复用。
const GEC_VERSIONS = ['1-200.0.0.0', '1-141.0.3537.57', '1-140.0.3485.14'];
let lastGoodVersionIndex = 0;

// 输出音频格式：24kHz / 48kbps 单声道 mp3，体积小、手机播放兼容性好
const OUTPUT_FORMAT = 'audio-24khz-48kbitrate-mono-mp3';

// 允许的发音人白名单（避免前端传入任意字符串注入 SSML）
const VOICE_WHITELIST = [
  'zh-CN-XiaoxiaoNeural',   // 晓晓，女声，自然亲和（默认）
  'zh-CN-XiaoyiNeural',     // 晓伊，女声，偏少女
  'zh-CN-YunxiNeural',      // 云希，男声，活泼
  'zh-CN-YunyangNeural',    // 云扬，男声，沉稳播报
  'zh-CN-YunjianNeural',    // 云健，男声，浑厚
  'zh-CN-liaoning-XiaobeiNeural',
  'zh-CN-shaanxi-XiaoniNeural',
];
const DEFAULT_VOICE = 'zh-CN-XiaoxiaoNeural';

// 单次合成的最长文本（超出直接截断）。
// 小老师的每条回复都是简短引导，800 字对应约 3 分钟语音，足够覆盖；
// 再长会显著拉长合成时间，反而影响"点了就响"的体验。
const MAX_TEXT_LENGTH = 800;

// 空闲超时：距离上一条消息超过该时间仍无新数据即判定失败（毫秒）
const IDLE_TIMEOUT_MS = 8000;

// 硬超时：单次合成的总时间上限，防止长文本无限占用函数（毫秒）
const HARD_TIMEOUT_MS = 25000;

// Edge 朗读扩展的固定 Origin（服务端会校验）
const WS_ORIGIN = 'chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold';

/**
 * 生成握手请求头。
 * 注意：服务端会校验 User-Agent 里的 Edge 版本，版本过旧会直接返回 403，
 * 因此 UA 版本必须与所用的 Sec-MS-GEC-Version 保持一致。
 * @param {string} gecVersion 形如 "1-141.0.3537.57"
 * @returns {Record<string,string>} 握手请求头
 */
function buildWsHeaders(gecVersion) {
  const major = (gecVersion.split('-')[1] || '141.0.0.0').split('.')[0];
  return {
    Pragma: 'no-cache',
    'Cache-Control': 'no-cache',
    Origin: WS_ORIGIN,
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'User-Agent':
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) ' +
      'Chrome/' + major + '.0.0.0 Safari/537.36 Edg/' + major + '.0.0.0',
  };
}

const CORS_HEADERS = { 'Access-Control-Allow-Origin': '*' };

/* ===== 小工具 ===== */

/**
 * 生成 Sec-MS-GEC 防重放令牌。
 * 算法：((Unix 秒 + Windows 纪元差) 向下取整到 5 分钟) * 1e7，拼接固定令牌后取 SHA256 大写十六进制。
 * @returns {string} 64 位大写十六进制字符串
 */
function generateSecMsGec() {
  const WIN_EPOCH_SECONDS = 11644473600n;
  let seconds = BigInt(Math.floor(Date.now() / 1000)) + WIN_EPOCH_SECONDS;
  seconds -= seconds % 300n;              // 向下取整到最近的 5 分钟
  const ticks = seconds * 10000000n;      // 100 纳秒为单位的 ticks
  return createHash('sha256')
    .update(ticks.toString() + TRUSTED_CLIENT_TOKEN, 'ascii')
    .digest('hex')
    .toUpperCase();
}

/**
 * 生成 Edge TTS 的 WebSocket 连接地址。
 * @param {string} gecVersion Sec-MS-GEC-Version 版本号
 * @returns {string} 完整 wss 地址
 */
function buildSocketUrl(gecVersion) {
  const connectionId = randomUUID().replace(/-/g, '');
  const params = [
    'TrustedClientToken=' + TRUSTED_CLIENT_TOKEN,
    'Sec-MS-GEC=' + generateSecMsGec(),
    'Sec-MS-GEC-Version=' + gecVersion,
    'ConnectionId=' + connectionId,
  ].join('&');
  return (
    'wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1?' + params
  );
}

/**
 * 生成协议要求的时间戳（RFC1123 风格，Edge 客户端固定格式）。
 * @returns {string}
 */
function timestampString() {
  return new Date().toString().replace(/GMT([+-]\d{4}).*/, 'GMT$1 (Coordinated Universal Time)');
}

/**
 * 转义 SSML 中的特殊字符，避免破坏 XML 结构。
 * @param {string} text 原始文本
 * @returns {string} 可安全嵌入 SSML 的文本
 */
function escapeXml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

/**
 * 清理不适合朗读的字符（Markdown 标记、【】标注、连续空白）。
 * @param {string} text 原始文本
 * @returns {string} 清理后的文本
 */
function cleanText(text) {
  return String(text || '')
    .replace(/【[^】]*】/g, '')
    .replace(/```[\s\S]*?```/g, '')
    .replace(/[*#`_~>]/g, '')
    .replace(/\r\n/g, '\n')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{2,}/g, '\n')
    .trim();
}

/**
 * 组装 speech.config 文本帧。
 * 注意：metadataoptions 的两个开关必须是【字符串】"true"/"false"，
 * 且整条消息以 \r\n 结尾，否则服务端会静默丢弃（连上但永远不返回音频）。
 * @returns {string}
 */
function buildSpeechConfigMessage() {
  const config =
    '{"context":{"synthesis":{"audio":{"metadataoptions":{' +
    '"sentenceBoundaryEnabled":"false","wordBoundaryEnabled":"false"},' +
    '"outputFormat":"' + OUTPUT_FORMAT + '"}}}}';
  return (
    'X-Timestamp:' + timestampString() + '\r\n' +
    'Content-Type:application/json; charset=utf-8\r\n' +
    'Path:speech.config\r\n\r\n' +
    config + '\r\n'
  );
}

/**
 * 组装 SSML 文本帧。
 * 注意：Edge 朗读通道的 Path 是 `ssml`（不是 Azure 的 `synthesis.ssml`），
 * 且 X-Timestamp 结尾要补一个 `Z`，否则服务端同样会静默丢弃。
 * @param {string} text 已清理的朗读文本
 * @param {string} voice 发音人名称
 * @param {string} requestId 32 位无横线 UUID
 * @returns {string}
 */
function buildSsmlMessage(text, voice, requestId) {
  const ssml =
    '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" ' +
    'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="zh-CN">' +
    '<voice name="' + voice + '">' +
    '<prosody pitch="+0Hz" rate="-5%" volume="+0%">' +
    escapeXml(text) +
    '</prosody></voice></speak>';
  return (
    'X-RequestId:' + requestId + '\r\n' +
    'Content-Type:application/ssml+xml\r\n' +
    'X-Timestamp:' + timestampString() + 'Z\r\n' +
    'Path:ssml\r\n\r\n' +
    ssml
  );
}

/**
 * 把各种二进制载体统一转换为 Buffer（同步，保证音频分片顺序不被打乱）。
 * @param {ArrayBuffer|Uint8Array|Buffer|string} data 原始数据
 * @returns {Buffer}
 */
function asBuffer(data) {
  if (Buffer.isBuffer(data)) return data;
  if (data instanceof ArrayBuffer) return Buffer.from(new Uint8Array(data));
  if (ArrayBuffer.isView(data)) {
    return Buffer.from(data.buffer, data.byteOffset, data.byteLength);
  }
  if (Array.isArray(data)) return Buffer.concat(data.map(asBuffer));
  return Buffer.from(String(data), 'utf8');
}

/**
 * 从二进制帧中剥离协议头，取出 mp3 数据段。
 * 帧格式：前 2 字节大端表示头部长度，其后为头部文本，再其后为音频二进制。
 * 兼容兜底：若长度前缀不合理，则退化为查找 "\r\n\r\n" 分隔符。
 * @param {Buffer} buf 完整二进制帧
 * @returns {Buffer|null} 音频片段；非 audio 帧返回 null
 */
function extractAudioChunk(buf) {
  if (buf.length < 4) return null;

  // 方案一：2 字节大端头部长度
  const headerLength = buf.readUInt16BE(0);
  if (headerLength > 0 && headerLength + 2 <= buf.length) {
    const header = buf.slice(2, 2 + headerLength).toString('utf8');
    if (header.indexOf('Path:') >= 0) {
      if (header.indexOf('Path:audio') < 0) return null;
      const chunk = buf.slice(2 + headerLength);
      return chunk.length > 0 ? chunk : null;
    }
  }

  // 方案二：查找 \r\n\r\n 分隔符
  const sepIndex = buf.indexOf('\r\n\r\n');
  if (sepIndex >= 0) {
    const header = buf.slice(0, sepIndex).toString('utf8');
    if (header.indexOf('Path:audio') < 0) return null;
    const chunk = buf.slice(sepIndex + 4);
    return chunk.length > 0 ? chunk : null;
  }
  return null;
}

/* ===== WebSocket 传输层 =====
 * 统一接口 Transport：{ send(text:string):void, close():void }
 * 事件通过 handlers 回调：{ onOpen, onMessage(buf, isBinary), onError(err), onClose }
 */

/** ws 库的缓存：undefined=未探测，null=不可用，其它=模块本身 */
let wsModuleCache;

/**
 * 尝试加载 ws 库。
 * 用变量作为模块名，避免打包器在【构建期】强行解析导致构建失败；
 * 解析不到时返回 null，由调用方回退到内置实现。
 * @returns {Promise<Function|null>} ws 的构造函数或 null
 */
async function loadWsLibrary() {
  if (wsModuleCache !== undefined) return wsModuleCache;
  try {
    const moduleName = 'ws';
    const mod = await import(moduleName);
    const Ctor = (mod && (mod.default || mod.WebSocket)) || null;
    wsModuleCache = typeof Ctor === 'function' ? Ctor : null;
  } catch (e) {
    wsModuleCache = null;
  }
  return wsModuleCache;
}

/**
 * 基于 ws 库的传输实现。
 * 注意：ws v8 的 message 事件签名是 (data, isBinary)，data 恒为 Buffer，
 * 文本帧【不会】自动转成 string，必须靠 isBinary 判断。
 * @param {Function} WebSocketCtor ws 构造函数
 * @param {string} url wss 地址
 * @param {Record<string,string>} headers 握手请求头
 * @param {{onOpen:Function,onMessage:Function,onError:Function,onClose:Function}} handlers 事件回调
 * @returns {{send:Function, close:Function}}
 */
function createWsTransport(WebSocketCtor, url, headers, handlers) {
  const socket = new WebSocketCtor(url, { headers });
  socket.on('open', () => handlers.onOpen());
  socket.on('message', (data, isBinary) => {
    // 老版本 ws 不传 isBinary，此时按"是否为字符串"兜底判断
    const binary = typeof isBinary === 'boolean' ? isBinary : typeof data !== 'string';
    handlers.onMessage(asBuffer(data), binary);
  });
  socket.on('error', (err) => handlers.onError(err));
  socket.on('close', () => handlers.onClose());
  return {
    send: (text) => socket.send(text),
    close: () => { try { socket.close(); } catch (e) { try { socket.terminate(); } catch (e2) { /* ignore */ } } },
  };
}

/**
 * 零依赖的极简 WebSocket 客户端（RFC6455，仅覆盖本场景所需能力）。
 * 只做：TLS 握手升级 → 发送掩码文本帧 → 解析服务端帧（含分片与 ping）。
 * @param {string} url wss 地址
 * @param {Record<string,string>} headers 附加握手请求头
 * @param {{onOpen:Function,onMessage:Function,onError:Function,onClose:Function}} handlers 事件回调
 * @returns {{send:Function, close:Function}}
 */
function createNativeTransport(url, headers, handlers) {
  const target = new URL(url);
  const port = target.port ? Number(target.port) : 443;
  const nonce = randomBytes(16).toString('base64');
  const pathWithQuery = target.pathname + target.search;

  let handshakeDone = false;
  let finished = false;          // 是否已通知过 onClose
  let pending = Buffer.alloc(0); // 尚未解析完的字节流
  let fragOpcode = 0;            // 分片消息的原始 opcode
  /** @type {Buffer[]} */
  let fragChunks = [];

  const socket = tlsConnect({ host: target.hostname, port, servername: target.hostname });
  socket.setNoDelay(true);

  const notifyClose = () => {
    if (finished) return;
    finished = true;
    handlers.onClose();
  };

  /**
   * 组装并发送一个客户端帧（客户端发出的帧必须加掩码）。
   * @param {number} opcode 操作码
   * @param {Buffer} payload 负载
   */
  const sendFrame = (opcode, payload) => {
    const len = payload.length;
    let header;
    if (len < 126) {
      header = Buffer.alloc(6);
      header[1] = 0x80 | len;
    } else if (len < 65536) {
      header = Buffer.alloc(8);
      header[1] = 0x80 | 126;
      header.writeUInt16BE(len, 2);
    } else {
      header = Buffer.alloc(14);
      header[1] = 0x80 | 127;
      header.writeUInt32BE(Math.floor(len / 4294967296), 2);
      header.writeUInt32BE(len >>> 0, 6);
    }
    header[0] = 0x80 | opcode;                 // FIN=1
    const mask = randomBytes(4);
    mask.copy(header, header.length - 4);
    const masked = Buffer.allocUnsafe(len);
    for (let i = 0; i < len; i++) masked[i] = payload[i] ^ mask[i & 3];
    socket.write(Buffer.concat([header, masked]));
  };

  /** 从缓冲区中尽可能多地解析出完整帧。 */
  const drainFrames = () => {
    for (;;) {
      if (pending.length < 2) return;
      const first = pending[0];
      const second = pending[1];
      const fin = (first & 0x80) !== 0;
      const opcode = first & 0x0f;
      const masked = (second & 0x80) !== 0;   // 服务端发来的帧不应带掩码
      let payloadLen = second & 0x7f;
      let offset = 2;

      if (payloadLen === 126) {
        if (pending.length < offset + 2) return;
        payloadLen = pending.readUInt16BE(offset);
        offset += 2;
      } else if (payloadLen === 127) {
        if (pending.length < offset + 8) return;
        const high = pending.readUInt32BE(offset);
        const low = pending.readUInt32BE(offset + 4);
        payloadLen = high * 4294967296 + low;
        offset += 8;
      }

      let maskKey = null;
      if (masked) {
        if (pending.length < offset + 4) return;
        maskKey = pending.slice(offset, offset + 4);
        offset += 4;
      }

      if (pending.length < offset + payloadLen) return;   // 帧还没收全，等下一批数据
      let payload = pending.slice(offset, offset + payloadLen);
      if (maskKey) {
        const unmasked = Buffer.allocUnsafe(payloadLen);
        for (let i = 0; i < payloadLen; i++) unmasked[i] = payload[i] ^ maskKey[i & 3];
        payload = unmasked;
      }
      pending = pending.slice(offset + payloadLen);

      if (opcode === 0x8) {           // close
        try { sendFrame(0x8, Buffer.alloc(0)); } catch (e) { /* ignore */ }
        try { socket.end(); } catch (e) { /* ignore */ }
        notifyClose();
        return;
      }
      if (opcode === 0x9) {           // ping → 必须回 pong
        try { sendFrame(0xa, payload); } catch (e) { /* ignore */ }
        continue;
      }
      if (opcode === 0xa) continue;   // pong，忽略

      if (opcode === 0x0) {           // continuation
        fragChunks.push(payload);
        if (fin) {
          handlers.onMessage(Buffer.concat(fragChunks), fragOpcode === 0x2);
          fragChunks = [];
          fragOpcode = 0;
        }
        continue;
      }

      // 首帧（text=0x1 / binary=0x2）
      if (fin) {
        handlers.onMessage(payload, opcode === 0x2);
      } else {
        fragOpcode = opcode;
        fragChunks = [payload];
      }
    }
  };

  socket.on('secureConnect', () => {
    const lines = [
      'GET ' + pathWithQuery + ' HTTP/1.1',
      'Host: ' + target.hostname,
      'Upgrade: websocket',
      'Connection: Upgrade',
      'Sec-WebSocket-Key: ' + nonce,
      'Sec-WebSocket-Version: 13',
    ];
    for (const key of Object.keys(headers)) lines.push(key + ': ' + headers[key]);
    socket.write(lines.join('\r\n') + '\r\n\r\n');
  });

  socket.on('data', (chunk) => {
    pending = pending.length ? Buffer.concat([pending, chunk]) : chunk;
    if (!handshakeDone) {
      const sep = pending.indexOf('\r\n\r\n');
      if (sep < 0) return;                       // 响应头还没收全
      const head = pending.slice(0, sep).toString('utf8');
      pending = pending.slice(sep + 4);
      const statusLine = head.split('\r\n')[0] || '';
      if (statusLine.indexOf(' 101') < 0) {
        handlers.onError(new Error('WebSocket 握手失败：' + statusLine.trim()));
        try { socket.destroy(); } catch (e) { /* ignore */ }
        return;
      }
      handshakeDone = true;
      handlers.onOpen();
    }
    drainFrames();
  });

  socket.on('error', (err) => handlers.onError(err));
  socket.on('close', notifyClose);
  socket.on('end', notifyClose);

  return {
    send: (text) => sendFrame(0x1, Buffer.from(text, 'utf8')),
    close: () => {
      try { if (handshakeDone) sendFrame(0x8, Buffer.alloc(0)); } catch (e) { /* ignore */ }
      try { socket.destroy(); } catch (e) { /* ignore */ }
    },
  };
}

/**
 * 建立传输通道：优先 ws 库，不可用时回退到内置实现。
 * 必须【同步】返回，确保调用方在任何事件回调触发前就持有 transport。
 * @param {Function|null} WebSocketCtor 已加载的 ws 构造函数，null 表示不可用
 * @param {string} url wss 地址
 * @param {Record<string,string>} headers 握手请求头
 * @param {{onOpen:Function,onMessage:Function,onError:Function,onClose:Function}} handlers 事件回调
 * @returns {{send:Function, close:Function}}
 */
function createTransport(WebSocketCtor, url, headers, handlers) {
  if (WebSocketCtor) {
    try {
      return createWsTransport(WebSocketCtor, url, headers, handlers);
    } catch (e) {
      // ws 构造失败（例如运行时缺少其依赖的原生能力）→ 记住并改用内置实现
      wsModuleCache = null;
    }
  }
  return createNativeTransport(url, headers, handlers);
}

/**
 * 通过 Edge TTS WebSocket 合成一段音频。
 * @param {string} text 已清理的朗读文本
 * @param {string} voice 发音人名称
 * @param {string} gecVersion Sec-MS-GEC-Version 版本号
 * @returns {Promise<Buffer>} 完整 mp3 Buffer
 */
async function synthesize(text, voice, gecVersion) {
  // 先把 ws 库探测完，后面就能同步建连，杜绝 onOpen 早于赋值的竞态
  const WebSocketCtor = await loadWsLibrary();

  return new Promise((resolve, reject) => {
    const url = buildSocketUrl(gecVersion);
    const requestId = randomUUID().replace(/-/g, '');
    /** @type {Buffer[]} */
    const chunks = [];
    let settled = false;
    let transport = null;
    let idleTimer = null;
    let hardTimer = null;

    const cleanup = () => {
      if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
      if (hardTimer) { clearTimeout(hardTimer); hardTimer = null; }
      if (transport) {
        try { transport.close(); } catch (e) { /* ignore */ }
      }
    };
    const finish = (buffer) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(buffer);
    };
    const fail = (err) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(err instanceof Error ? err : new Error(String(err)));
    };

    // 超时策略：只要还在持续收到音频就不打断（空闲超时），同时用硬超时兜底
    const onTimeout = () => {
      if (chunks.length > 0) {
        finish(Buffer.concat(chunks));
      } else {
        fail(new Error('语音合成超时'));
      }
    };
    const touchIdle = () => {
      if (settled) return;
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(onTimeout, IDLE_TIMEOUT_MS);
    };
    touchIdle();
    hardTimer = setTimeout(onTimeout, HARD_TIMEOUT_MS);

    const handlers = {
      onOpen: () => {
        if (settled) return;
        try {
          transport.send(buildSpeechConfigMessage());
          transport.send(buildSsmlMessage(text, voice, requestId));
        } catch (e) {
          fail(new Error('发送合成指令失败：' + (e && e.message ? e.message : String(e))));
        }
      },
      onMessage: (data, isBinary) => {
        if (settled) return;
        touchIdle();
        if (!isBinary) {
          // 文本帧：只关心 turn.end（合成结束）
          if (data.toString('utf8').indexOf('Path:turn.end') >= 0) {
            if (chunks.length === 0) {
              fail(new Error('语音服务未返回音频数据'));
            } else {
              finish(Buffer.concat(chunks));
            }
          }
          return;
        }
        const chunk = extractAudioChunk(data);
        if (chunk) chunks.push(chunk);
      },
      onError: () => {
        if (chunks.length > 0) {
          finish(Buffer.concat(chunks));
        } else {
          fail(new Error('语音服务连接异常'));
        }
      },
      onClose: () => {
        if (settled) return;
        if (chunks.length > 0) {
          finish(Buffer.concat(chunks));
        } else {
          fail(new Error('语音服务连接已关闭且未返回音频'));
        }
      },
    };

    try {
      transport = createTransport(WebSocketCtor, url, buildWsHeaders(gecVersion), handlers);
    } catch (e) {
      fail(new Error('无法建立语音合成连接：' + (e && e.message ? e.message : String(e))));
    }
  });
}

/**
 * 带版本回退的合成：先用上次成功的 GEC 版本，失败再依次尝试其它版本。
 * @param {string} text 已清理的朗读文本
 * @param {string} voice 发音人名称
 * @returns {Promise<Buffer>} 完整 mp3 Buffer
 */
async function synthesizeWithFallback(text, voice) {
  const total = GEC_VERSIONS.length;
  let lastError = new Error('语音合成失败');
  for (let i = 0; i < total; i++) {
    const index = (lastGoodVersionIndex + i) % total;
    try {
      const audio = await synthesize(text, voice, GEC_VERSIONS[index]);
      lastGoodVersionIndex = index;   // 记住可用版本，下次优先使用
      return audio;
    } catch (e) {
      lastError = e instanceof Error ? e : new Error(String(e));
    }
  }
  throw lastError;
}

/**
 * 生成 JSON 错误响应。
 * @param {string} message 错误信息
 * @param {number} status HTTP 状态码
 * @returns {Response}
 */
function jsonError(message, status) {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: { ...CORS_HEADERS, 'Content-Type': 'application/json; charset=UTF-8' },
  });
}

/* ===== 入口 ===== */

export const onRequestPost = async (context) => {
  const { request } = context;

  try {
    let payload = {};
    const rawBody = await request.text();
    if (rawBody) {
      try {
        payload = JSON.parse(rawBody) || {};
      } catch (e) {
        return jsonError('请求体不是合法 JSON，应为 {"text":"要朗读的文字"}', 400);
      }
    }

    const text = cleanText(payload.text).slice(0, MAX_TEXT_LENGTH);
    if (!text) {
      return jsonError('缺少要朗读的文字：请在请求体传 { "text": "..." }', 400);
    }

    const requestedVoice = String(payload.voice || '').trim();
    const voice = VOICE_WHITELIST.indexOf(requestedVoice) >= 0 ? requestedVoice : DEFAULT_VOICE;

    const audio = await synthesizeWithFallback(text, voice);
    if (!audio || audio.length < 512) {
      return jsonError('语音合成失败：返回音频为空', 502);
    }

    return new Response(audio, {
      status: 200,
      headers: {
        'Content-Type': 'audio/mpeg',
        'Content-Length': String(audio.length),
        'Cache-Control': 'public, max-age=3600',
        'Access-Control-Allow-Origin': '*',
      },
    });
  } catch (e) {
    return jsonError('语音合成异常：' + (e && e.message ? e.message : String(e)), 502);
  }
};

// 同源部署下浏览器不会触发跨域预检；此处附带 OPTIONS 处理仅为健壮性兜底
export const onRequestOptions = async () => {
  return new Response(null, {
    status: 204,
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    },
  });
};
