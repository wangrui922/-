/**
 * 跨端进度同步函数（EdgeOne Makers Node.js Function）
 *
 * 路由：/api/progress  （文件位置 cloud-functions/api/progress.js 自动映射）
 *
 * 职责：
 *  - 把单个学生的练习进度（前端 localStorage 里的 JSON 快照）按 student_id 持久化到
 *    Blob 存储，实现「手机做到一半 → 平板/电脑接着做」的跨设备同步。
 *  - 抽题/判题逻辑在 Python 云函数（index.py）里，进度同步是另一条独立路径，放 Node 是因为
 *    Blob SDK（@edgeone/pages-blob）仅 Node 运行时可用。
 *
 * 存储模型：
 *  - 命名空间：store = getStore("progress")（首次访问自动创建）
 *  - 键：      key = student_id（已做白名单校验，杜绝路径穿越）
 *  - 值：      { ...payload, saved_at: <ms 时间戳> }
 *
 * 契约（与 Python 侧保持一致的外壳）：
 *  - 成功：{ "code": 0, "msg": "ok", "data": {...} }
 *  - 失败：{ "code": <非零>, "msg": "<人话>", "data": null }
 */

import { getStore } from '@edgeone/pages-blob';

const STORE_NAME = 'progress';
// student_id 白名单：仅允许字母数字下划线中划线，长度 1-64，杜绝 "/" 等路径穿越字符
const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

function ok(data, msg = 'ok') {
  return new Response(JSON.stringify({ code: 0, msg, data }), {
    status: 200,
    headers: JSON_HEADERS,
  });
}

function fail(code, msg, httpStatus = 400) {
  return new Response(JSON.stringify({ code, msg, data: null }), {
    status: httpStatus,
    headers: JSON_HEADERS,
  });
}

const JSON_HEADERS = {
  'Content-Type': 'application/json; charset=UTF-8',
  'Access-Control-Allow-Origin': '*',
};

function makeStore() {
  // 默认 eventual 一致性足够（进度同步对强一致不敏感）；如需更强可见性可加 { consistency: 'strong' }
  return getStore(STORE_NAME);
}

// GET /api/progress?student_id=xxx  → 读取该学生的进度
export const onRequestGet = async (context) => {
  try {
    const { request } = context;
    const url = new URL(request.url);
    const studentId = (url.searchParams.get('student_id') || '').trim();

    if (!studentId) {
      return fail(4001, '缺少 student_id 参数');
    }
    if (!ID_RE.test(studentId)) {
      return fail(4002, 'student_id 格式非法（仅允许字母数字 _ - ，长度 1-64）');
    }

    const store = makeStore();
    const rec = await store.get(studentId, { type: 'json' });

    if (!rec) {
      // 没有记录 ≠ 错误：返回空 data，前端据此走初始态
      return ok(null, 'no_record');
    }
    return ok(rec, 'ok');
  } catch (e) {
    return fail(5001, '读取进度失败：' + (e && e.message ? e.message : String(e)), 500);
  }
};

// POST /api/progress  body: { student_id, payload }  → 写入/覆盖该学生的进度
export const onRequestPost = async (context) => {
  try {
    const { request } = context;
    let body;
    try {
      body = await request.json();
    } catch (e) {
      return fail(4003, '请求体不是合法 JSON');
    }

    const studentId = (body && body.student_id ? String(body.student_id) : '').trim();
    const payload = body && body.payload;

    if (!studentId) {
      return fail(4001, '缺少 student_id');
    }
    if (!ID_RE.test(studentId)) {
      return fail(4002, 'student_id 格式非法（仅允许字母数字 _ - ，长度 1-64）');
    }
    if (payload === undefined || payload === null) {
      return fail(4004, '缺少 payload（要保存的进度对象）');
    }
    // payload 必须是可序列化的对象（不保存函数/undefined 等）
    if (typeof payload !== 'object' || Array.isArray(payload)) {
      return fail(4005, 'payload 必须是 JSON 对象');
    }

    const store = makeStore();
    const rec = { ...payload, saved_at: Date.now() };
    await store.setJSON(studentId, rec);

    return ok({ student_id: studentId, saved_at: rec.saved_at }, 'saved');
  } catch (e) {
    return fail(5002, '保存进度失败：' + (e && e.message ? e.message : String(e)), 500);
  }
};

// 同源部署下浏览器通常不触发预检；此处 OPTIONS 兜底仅为健壮性
export const onRequestOptions = async () => {
  return new Response(null, {
    status: 204,
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    },
  });
};
