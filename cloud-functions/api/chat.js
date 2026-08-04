/**
 * 云端中转函数（EdgeOne Makers Node.js Function）
 *
 * 路由：/api/chat  （文件位置 cloud-functions/api/chat.js 自动映射）
 *
 * 职责：
 *  - 接收前端 POST 请求，从请求头 `Authorization: Bearer <用户Key>` 取出用户自己的方舟 Key
 *    （兼容兜底：若请求头缺失，则从 JSON body 的 `api_key` 字段读取）
 *  - 原样把请求体（{ model, input:[...] }）透传给火山方舟 Responses API
 *  - 把方舟的响应（状态码 + body）原样转发回前端，不得增删字段、不得改写 model/input
 *
 * 设计要点（满足需求）：
 *  - 本函数【不持有任何固定 Key】，只转发前端带来的用户 Key
 *  - 部署者测试用的 Key 仅用于验证连通，不写死、不留存到代码或生产环境
 *  - 前端与方舟的字段结构完全一致，中转只做“搬运”，保证 100% 透传
 */

const ARK_ENDPOINT = 'https://ark.cn-beijing.volces.com/api/v3/responses';

export const onRequestPost = async (context) => {
  const { request } = context;
  const corsHeaders = { 'Access-Control-Allow-Origin': '*' };

  try {
    // 读取请求体原文（便于原样转发）
    const rawBody = await request.text();

    // 解析用户 Key：优先取 Authorization 头，其次兼容 body.api_key
    let userKey = '';
    const authHeader = request.headers.get('Authorization') || '';
    if (/^Bearer\s+/i.test(authHeader)) {
      userKey = authHeader.replace(/^Bearer\s+/i, '').trim();
    }
    if (!userKey && rawBody) {
      try {
        const parsed = JSON.parse(rawBody);
        userKey = parsed.api_key || '';
      } catch (e) {
        // 非 JSON body（理论上不会发生），忽略，交给下方缺失 Key 判断
      }
    }

    if (!userKey) {
      return new Response(
        JSON.stringify({
          error: '缺少用户方舟 API Key：请在请求头携带 `Authorization: Bearer <你的Key>`',
        }),
        {
          status: 401,
          headers: { ...corsHeaders, 'Content-Type': 'application/json; charset=UTF-8' },
        }
      );
    }

    // 原样透传请求到方舟
    const upstream = await fetch(ARK_ENDPOINT, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer ' + userKey,
      },
      body: rawBody,
    });

    // 原样回传方舟响应（状态码 + 响应体），不改动任何字段
    const upstreamBody = await upstream.text();
    return new Response(upstreamBody, {
      status: upstream.status,
      headers: {
        'Content-Type': upstream.headers.get('Content-Type') || 'application/json; charset=UTF-8',
        'Access-Control-Allow-Origin': '*',
      },
    });
  } catch (e) {
    return new Response(
      JSON.stringify({ error: '云端中转异常：' + (e && e.message ? e.message : String(e)) }),
      {
        status: 502,
        headers: { ...corsHeaders, 'Content-Type': 'application/json; charset=UTF-8' },
      }
    );
  }
};

// 同源部署下浏览器不会触发跨域预检；此处附带 OPTIONS 处理仅为健壮性兜底
export const onRequestOptions = async () => {
  return new Response(null, {
    status: 204,
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    },
  });
};
