/**
 * 错题小老师 · 本地开发服务器
 *
 * 用法：双击 start-server.bat 或运行 node server.js
 * 功能：
 *   1. 在 http://localhost:3210 ��供 index.html 静态文件服务
 *   2. 把 /api/chat 请求原样透传给火山方舟（和云函数 chat.js 逻辑一致）
 *
 * 这样双击启动后，所有功能都能正常使用（拍照辅导、练习出题、题库改编等）
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const url = require('url');

const PORT = 3210;
const ARK_ENDPOINT = 'https://ark.cn-beijing.volces.com/api/v3/responses';

// MIME 类型映射
const MIME_TYPES = {
  '.html': 'text/html; charset=UTF-8',
  '.js': 'application/javascript; charset=UTF-8',
  '.css': 'text/css; charset=UTF-8',
  '.json': 'application/json; charset=UTF-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.wasm': 'application/wasm',
};

const server = http.createServer(async (req, res) => {
  // CORS 头
  const corsHeaders = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
  };

  // OPTIONS 预检
  if (req.method === 'OPTIONS') {
    res.writeHead(204, corsHeaders);
    res.end();
    return;
  }

  const parsedUrl = url.parse(req.url, true);
  const pathname = parsedUrl.pathname;

  /* ========== /api/chat — 方舟代理（和云函数逻辑完全一致）========== */
  if (pathname === '/api/chat' && req.method === 'POST') {
    let body = '';
    for await (const chunk of req) body += chunk;

    // 取用户 Key：优先 Authorization 头，其次 body.api_key
    let userKey = '';
    const authHeader = req.headers['authorization'] || '';
    if (/^Bearer\s+/i.test(authHeader)) {
      userKey = authHeader.replace(/^Bearer\s+/i, '').trim();
    }
    if (!userKey && body) {
      try { userKey = JSON.parse(body).api_key || ''; } catch(e) {}
    }

    if (!userKey) {
      res.writeHead(401, { ...corsHeaders, 'Content-Type': 'application/json; charset=UTF-8' });
      res.end(JSON.stringify({ error: '缺少 API Key：请先在设置中填写方舟 Key' }));
      return;
    }

    try {
      const upstreamRes = await fetch(ARK_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + userKey },
        body: body,
      });
      const upstreamBody = await upstreamRes.text();
      res.writeHead(upstreamRes.status, {
        'Content-Type': upstreamRes.headers.get('content-type') || 'application/json; charset=UTF-8',
        ...corsHeaders,
      });
      res.end(upstreamBody);
    } catch (e) {
      res.writeHead(502, { ...corsHeaders, 'Content-Type': 'application/json; charset=UTF-8' });
      res.end(JSON.stringify({ error: '方舟连接失败：' + (e?.message || String(e)) }));
    }
    return;
  }

  /* ========== 静态文件服务 ========== */
  // 安全：防止路径穿越
  const safePath = path.normalize(path.join(__dirname, pathname)).replace(/\\/g, '/');
  if (!safePath.startsWith(__dirname.replace(/\\/g, '/'))) {
    res.writeHead(403); res.end('Forbidden'); return;
  }

  let filePath = safePath;
  // 如果是目录，默认返回 index.html
  if (fs.existsSync(filePath) && fs.statSync(filePath).isDirectory()) {
    filePath = path.join(filePath, 'index.html');
  }

  if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    // SPA 兜底：未匹配的路由也返回 index.html
    filePath = path.join(__dirname, 'index.html');
  }

  const ext = path.extname(filePath).toLowerCase();
  const contentType = MIME_TYPES[ext] || 'application/octet-stream';
  
  try {
    const content = fs.readFileSync(filePath);
    res.writeHead(200, { 'Content-Type': contentType });
    res.end(content);
  } catch (e) {
    res.writeHead(500, { 'Content-Type': 'text/plain' });
    res.end('Internal Server Error');
  }
});

server.listen(PORT, () => {
  const addr = `http://localhost:${PORT}`;
  console.log('====================================');
  console.log('  错题小老师 本地服务器已启动');
  console.log(`  请在浏览器打开：${addr}`);
  console.log('  按 Ctrl+C 停止服务器');
  console.log('====================================');

  // 自动打开浏览器
  const { exec } = require('child_process');
  const platform = process.platform;
  if (platform === 'win32') {
    exec(`start ${addr}`);
  } else if (platform === 'darwin') {
    exec(`open ${addr}`);
  } else {
    exec(`xdg-open ${addr}`);
  }
});
