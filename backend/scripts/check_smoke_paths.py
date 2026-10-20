"""校验冒烟脚本用到的端点路径都真实存在（开发期自检脚本）。"""
import os
import sys
from pathlib import Path

# 让脚本能 import app：把 backend/ 目录加入 sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.main import app  # noqa: E402

def collect_paths(router, prefix: str = "") -> set:
    """递归收集所有路由路径。

    本项目使用的 FastAPI 版本在 include_router 后会把子路由包成
    _IncludedRouter（无 .path 属性），其真实路径在
    .include_context.prefix + .original_router.routes 里，需逐层展开。
    """
    found = set()
    for r in getattr(router, "routes", []) or []:
        ctx = getattr(r, "include_context", None)
        if ctx is not None:  # _IncludedRouter
            sub_prefix = prefix + (getattr(ctx, "prefix", "") or "")
            found |= collect_paths(r.original_router, sub_prefix)
            continue
        p = getattr(r, "path", None)
        if isinstance(p, str):
            found.add(prefix + p)
    return found


paths = collect_paths(app)
need = [
    "/api/health",
    "/api/auth/activation/activate",
    "/api/practice/session",
    "/api/practice/answer",
    "/api/compliance/guardian-consent",
    "/api/mistake/list",
    "/api/progress/adaptive-plan",
    "/api/billing/entitlement",
    "/api/chat",
    "/api/admin/dashboard/overview",
    "/api/admin/dashboard/members",
    "/api/billing/pending",
    "/api/billing/confirm",
]

missing = []
for n in need:
    # 允许后端带 router 前缀（如 /api/billing/pending 与 /billing/pending）
    hit = n in paths or any(p.endswith(n) for p in paths)
    mark = "OK  " if hit else "MISS"
    if not hit:
        missing.append(n)
    print(mark, n)
print("---")
print("total routes:", len(paths))
print("missing:", missing)
sys.exit(1 if missing else 0)
