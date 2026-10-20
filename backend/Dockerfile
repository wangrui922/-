# AI Math Coach backend - production image (cloud-ready, reserved for later).
# Local Windows dev uses start_server.bat (uvicorn); this file is for Docker/cloud.
# Build context must be the backend/ directory.

FROM python:3.12-slim

# PIP_INDEX_URL: Tencent Cloud internal PyPI mirror.
# The production server (118.25.44.126) cannot reach pypi.org directly (pypi.org and
# Tsinghua mirror time out; Tencent Cloud mirror answers in 0.62s). pip reads this env var
# natively, so requirements + gunicorn/uvicorn + celery all three installs use it.
# Override with: docker build --build-arg PIP_INDEX_URL=...
# NOTE: keep this file LF-only. With CRLF, the trailing \r leaks into the RUN line and pip
# endlessly retries fetching a non-existent "requirements.txt\r" package, hanging the build
# (seen 2026-10-20: build stuck 45 min, load 0.14, CPU 0.3%).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.cloud.tencent.com

WORKDIR /app

# install python deps first (cached layer)
COPY requirements.txt .
RUN pip install -r requirements.txt
# gunicorn + uvicorn worker: enables --max-requests to recycle workers
RUN pip install "gunicorn==23.0.*" "uvicorn[standard]==0.32.*"
# Celery + Redis (async task queue)
RUN pip install "celery[redis]==5.4.*" "redis==5.0.*"

# copy app source
COPY . .

# SQLite (dev.db) lives in /app and must be mounted as a volume by compose,
# otherwise data is lost when the container restarts.
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

# 2 workers, each recycled after 1000 requests (jitter 100) -> no long-lived
# memory growth. Tune --workers to (2 x CPU cores + 1) on bigger boxes.
CMD ["gunicorn", "app.main:app", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--max-requests", "1000", \
     "--max-requests-jitter", "100", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
