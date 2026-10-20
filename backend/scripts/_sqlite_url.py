"""SQLite ``DATABASE_URL`` 解析（生产/本地共用）。

⚠️ **不能先过 ``Path()``**：``Path("sqlite:////app/data/coach.db").as_posix()``
会把重复斜杠压成 ``sqlite:/app/data/coach.db``（少一斜杠），文件必然不存在。
必须**先切字符串再转 Path**。

支持三种形态：
    ``sqlite:////app/data/coach.db`` → ``/app/data/coach.db``（Linux 绝对路径）
    ``sqlite:///C:/path/x.db``      → ``C:/path/x.db``（Windows 盘符）
    ``sqlite:///./rel.db``          → ``/./rel.db``（相对路径，交给 Path 处理）
"""

from __future__ import annotations

import re

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[/\\]")


def sqlite_path_from_url(url: str) -> str:
    """从 ``DATABASE_URL`` 解析出 SQLite 文件路径字符串。"""
    raw = (url or "").strip()
    if not raw.startswith("sqlite:"):
        return raw
    rest = raw[len("sqlite://"):].lstrip("/")
    if not rest:
        return raw
    if _WINDOWS_DRIVE_RE.match(rest):
        return rest.replace("\\", "/")
    return "/" + rest
