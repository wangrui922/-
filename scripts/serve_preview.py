"""本地预览服务器：在同一端口同时托管前端静态资源 + 反向代理 /api 到 FastAPI 后端。

用法：
    cd D:\制作网页\cuotixiaolaoshi
    backend\.venv\Scripts\python.exe scripts/serve_preview.py
然后浏览器打开 http://127.0.0.1:5173

前端（student-web/dist）使用相对 baseURL '/api'，本服务把 /api/* 转发到
后端（默认 http://127.0.0.1:8000），从而无需 CORS、无需重建前端即可联调。
"""
from __future__ import annotations

import os
import socketserver
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

BACKEND = "http://127.0.0.1:8000"
STATIC_DIR = r"D:\制作网页\cuotixiaolaoshi\student-web\dist"
PORT = 5173

# 不转发给后端的请求头（避免冲突）
_HOP_BY_HOP = {"host", "content-length", "connection", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization", "proxy-connection"}


def _content_type(path: str) -> str:
    if path.endswith(".html"):
        return "text/html; charset=utf-8"
    if path.endswith(".js"):
        return "application/javascript; charset=utf-8"
    if path.endswith(".css"):
        return "text/css; charset=utf-8"
    if path.endswith(".json"):
        return "application/json; charset=utf-8"
    if path.endswith(".svg"):
        return "image/svg+xml"
    if path.endswith(".png"):
        return "image/png"
    if path.endswith(".jpg") or path.endswith(".jpeg"):
        return "image/jpeg"
    if path.endswith(".ico"):
        return "image/x-icon"
    if path.endswith(".woff2"):
        return "font/woff2"
    if path.endswith(".woff"):
        return "font/woff"
    return "application/octet-stream"


class Handler(BaseHTTPRequestHandler):
    # ---- 反向代理 /api -> 后端 ----
    def _proxy(self) -> None:
        url = BACKEND + self.path
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(url, data=body, method=self.command)
        for key, val in self.headers.items():
            if key.lower() in _HOP_BY_HOP:
                continue
            req.add_header(key, val)
        try:
            resp = urllib.request.urlopen(req, timeout=120)
            payload = resp.read()
            self.send_response(resp.status)
            for key, val in resp.getheaders():
                if key.lower() in _HOP_BY_HOP:
                    continue
                self.send_header(key, val)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            self.send_response(exc.code)
            for key, val in exc.headers.items():
                if key.lower() in _HOP_BY_HOP:
                    continue
                self.send_header(key, val)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if payload:
                self.wfile.write(payload)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    # ---- 静态资源 + SPA 回退 ----
    def _static(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/":
            path = "/index.html"
        fpath = os.path.normpath(os.path.join(STATIC_DIR, path.lstrip("/")))
        if not fpath.startswith(STATIC_DIR) or not os.path.isfile(fpath):
            # 单页应用回退：未知路由交给 index.html
            fpath = os.path.join(STATIC_DIR, "index.html")
        with open(fpath, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", _content_type(fpath))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._proxy()
        else:
            self._static()

    def do_POST(self):
        if self.path.startswith("/api/"):
            self._proxy()
        else:
            self.send_response(405)
            self.end_headers()

    def do_PUT(self):
        if self.path.startswith("/api/"):
            self._proxy()
        else:
            self.send_response(405)
            self.end_headers()

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            self._proxy()
        else:
            self.send_response(405)
            self.end_headers()

    def do_OPTIONS(self):
        if self.path.startswith("/api/"):
            self._proxy()
        else:
            self.send_response(204)
            self.end_headers()

    def log_message(self, *args):  # 静默
        return


if __name__ == "__main__":
    if not os.path.isdir(STATIC_DIR):
        raise SystemExit(f"静态目录不存在: {STATIC_DIR}")
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"预览服务已启动: http://127.0.0.1:{PORT}  (静态={STATIC_DIR}, /api -> {BACKEND})")
        httpd.serve_forever()
