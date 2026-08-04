"""文件存储接口（T03 · 3.4）。

    POST /api/file/upload   上传题目照片，返回 ``key`` 与可直接 ``<img src>`` 的 url
    GET  /api/file/{key}    读取已上传文件（返回二进制流，不套统一响应外壳）

设计要点：
    * ``STORAGE_DRIVER`` 目前只实现 ``local``：文件落在 ``backend/storage/``
      （由 ``settings.storage_path`` 解析并自动创建），切 COS 时只换这一层；
    * ``key`` 一律由服务端用 ``uuid4`` 生成，只保留白名单后缀，
      彻底杜绝用户可控文件名带来的路径穿越；
    * 下载接口**不鉴权**，方便前端 ``<img>`` 直接引用；key 不可枚举（uuid4）。
"""

from __future__ import annotations

import base64
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app.ai.ark_client import ArkError, ark_respond
from app.ai.prompts import ocr as ocr_prompt
from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user
from app.core.response import ApiError, CODE_BAD_REQUEST, CODE_NOT_FOUND, ok

logger = logging.getLogger("app.api.file")

router = APIRouter(tags=["file"])

#: 允许上传的后缀 -> Content-Type
MEDIA_TYPES: Dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".heic": "image/heic",
}

#: 后缀识别不出时的兜底 Content-Type
DEFAULT_MEDIA_TYPE: str = "application/octet-stream"

#: 单文件大小上限（10 MB，手机拍照足够）
MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024


def _storage_dir() -> Path:
    """本地存储目录（不存在时自动创建）。"""
    return settings.storage_path


def _safe_ext(filename: Optional[str]) -> str:
    """从原始文件名取白名单后缀。

    Args:
        filename: 客户端上传的文件名，可为空。

    Returns:
        形如 ``.jpg`` 的小写后缀。

    Raises:
        ApiError: code=400，后缀不在白名单内。
    """
    ext = Path(filename or "").suffix.lower()
    if ext not in MEDIA_TYPES:
        allowed = "/".join(sorted(MEDIA_TYPES))
        raise ApiError(CODE_BAD_REQUEST, f"只支持图片格式：{allowed}")
    return ext


def _resolve_key(key: str) -> Path:
    """把 ``key`` 解析成存储目录内的绝对路径。

    只接受「纯文件名」形式的 key，任何带分隔符或 ``..`` 的输入直接按 400 拒绝，
    再叠加一层「解析后必须仍在 storage 目录内」的校验，双保险防路径穿越。

    Args:
        key: 上传时返回的文件键。

    Returns:
        文件绝对路径。

    Raises:
        ApiError: code=400 key 非法；code=404 文件不存在。
    """
    name = (key or "").strip()
    if not name or name != Path(name).name or name in {".", ".."}:
        raise ApiError(CODE_BAD_REQUEST, "文件 key 非法")

    root = _storage_dir().resolve()
    path = (root / name).resolve()
    if root not in path.parents:
        raise ApiError(CODE_BAD_REQUEST, "文件 key 非法")
    if not path.is_file():
        raise ApiError(CODE_NOT_FOUND, "文件不存在")
    return path


def _run_ocr(raw: bytes) -> str:
    """同步调用方舟做一次题目转写（失败降级为空串，不影响上传本身）。

    Args:
        raw: 图片二进制。

    Returns:
        转写出的题目文字；调用失败时返回空串。
    """
    image_b64 = base64.b64encode(raw).decode("ascii")
    try:
        return ark_respond(
            messages=[{"role": "user", "content": "请按规则转写这张图片里的数学题。"}],
            system=ocr_prompt.PROMPT,
            image_b64=image_b64,
        )
    except ArkError as exc:
        logger.warning("上传附带 OCR 失败，降级为空串：%s", exc)
        return ""


@router.post("/file/upload")
async def upload_file(
    file: UploadFile = File(...),
    ocr: int = Query(0, description="传 1 时同步做一次 OCR，返回 ocrText"),
    current: CurrentUser = Depends(get_current_user),
) -> dict:
    """上传题目照片到本地存储。

    Returns:
        ``{"code":0,"msg":"ok","data":{"key","url"}}``；
        带 ``?ocr=1`` 时额外返回 ``ocrText``（识别失败为空串）。

    Raises:
        ApiError: 400 文件为空 / 超过 10MB / 格式不支持。
    """
    ext = _safe_ext(file.filename)
    raw = await file.read()
    if not raw:
        raise ApiError(CODE_BAD_REQUEST, "上传文件为空")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ApiError(CODE_BAD_REQUEST, "图片过大，请压缩到 10MB 以内")

    key = f"{uuid.uuid4().hex}{ext}"
    (_storage_dir() / key).write_bytes(raw)

    data: Dict[str, Any] = {"key": key, "url": f"/api/file/{key}"}
    if ocr == 1:
        data["ocrText"] = await run_in_threadpool(_run_ocr, raw)
    return ok(data)


@router.get("/file/{key}")
async def get_file(key: str) -> FileResponse:
    """读取已上传的文件。

    注意：本接口返回二进制流，**不套** ``{code,msg,data}`` 外壳，
    以便前端 ``<img src="/api/file/xxx.jpg">`` 直接引用。

    Raises:
        ApiError: 400 key 非法；404 文件不存在。
    """
    path = _resolve_key(key)
    media_type = MEDIA_TYPES.get(path.suffix.lower(), DEFAULT_MEDIA_TYPE)
    return FileResponse(path, media_type=media_type, filename=path.name)


__all__ = ["router", "MEDIA_TYPES", "MAX_UPLOAD_BYTES"]
