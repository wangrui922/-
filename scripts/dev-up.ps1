<#
.SYNOPSIS
    AI数学教练 V4.1 · Windows 本地一键启动脚本

.DESCRIPTION
    -Mode sqlite (默认) : 零外部依赖，DATABASE_URL=sqlite:///./dev.db
    -Mode pg            : docker compose up -d db，DATABASE_URL 指向 localhost:5433
    流程：准备 venv -> 安装依赖 -> (可选)起 PG -> 建表 -> 跑 pytest -> 起 uvicorn

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1
    powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1 -Mode pg
    powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1 -SkipTests -NoServe
#>
[CmdletBinding()]
param(
    [ValidateSet('sqlite', 'pg')]
    [string]$Mode = 'sqlite',

    [string]$Python = 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe',

    [int]$Port = 8000,

    [switch]$SkipInstall,
    [switch]$SkipTests,
    [switch]$NoServe
)

$ErrorActionPreference = 'Stop'

$RootDir    = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RootDir 'backend'
$VenvDir    = Join-Path $RootDir '.venv'
$VenvPy     = Join-Path $VenvDir 'Scripts\python.exe'

function Write-Step($msg) { Write-Host "`n>>> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn2($m)  { Write-Host "    [!!] $m"   -ForegroundColor Yellow }

Write-Host "==== AI数学教练 V4.1 dev-up (mode=$Mode) ====" -ForegroundColor Magenta
Write-Host "     root = $RootDir"

# ---------------------------------------------------------------- 1. venv
Write-Step '1/6 准备 Python 虚拟环境'
if (-not (Test-Path $VenvPy)) {
    if (-not (Test-Path $Python)) {
        throw "找不到 Python 解释器：$Python （用 -Python 指定其它路径）"
    }
    & $Python -m venv $VenvDir
    Write-Ok "已创建 venv -> $VenvDir"
} else {
    Write-Ok 'venv 已存在，跳过创建'
}

# ---------------------------------------------------------------- 2. deps
Write-Step '2/6 安装后端依赖'
if ($SkipInstall) {
    Write-Warn2 '已指定 -SkipInstall，跳过'
} else {
    & $VenvPy -m pip install -q -U pip
    & $VenvPy -m pip install -q -r (Join-Path $BackendDir 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'pip install 失败（检查网络或镜像源）' }
    Write-Ok '依赖安装完成'
}

# ---------------------------------------------------------------- 3. .env
Write-Step '3/6 检查 .env'
$envFile = Join-Path $RootDir '.env'
$envSample = Join-Path $RootDir '.env.example'
if (-not (Test-Path $envFile) -or ((Get-Item $envFile).Length -eq 0)) {
    Copy-Item $envSample $envFile -Force
    Write-Ok '.env 由 .env.example 生成'
} else {
    Write-Ok '.env 已存在'
}

# ---------------------------------------------------------------- 4. DB
Write-Step '4/6 准备数据库'
if ($Mode -eq 'pg') {
    Push-Location $RootDir
    try {
        docker compose up -d db
        if ($LASTEXITCODE -ne 0) { throw 'docker compose up -d db 失败（Docker Desktop 是否已启动？）' }
    } finally { Pop-Location }

    Write-Host '    等待 PostgreSQL healthy ...'
    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Seconds 2
        $state = (docker inspect --format '{{.State.Health.Status}}' coach-db 2>$null)
        if ($state -eq 'healthy') { $ready = $true; break }
    }
    if (-not $ready) { throw 'PostgreSQL 未在 80s 内就绪，请看 docker logs coach-db' }
    $env:DATABASE_URL = 'postgresql+psycopg://coach:coach@localhost:5433/coach'
    Write-Ok 'PostgreSQL 16 + pgvector 就绪 (localhost:5433)'
} else {
    $env:DATABASE_URL = 'sqlite:///./dev.db'
    Write-Ok 'SQLite 回退模式 (./dev.db)'
}

Write-Host "    DATABASE_URL = $($env:DATABASE_URL)"
Push-Location $BackendDir
try {
    & $VenvPy (Join-Path $BackendDir 'scripts\init_db.py')
    if ($LASTEXITCODE -ne 0) { throw 'init_db.py 建表失败' }
} finally { Pop-Location }
Write-Ok '11 张表建表完成'

# ---------------------------------------------------------------- 5. tests
Write-Step '5/6 冒烟测试'
if ($SkipTests) {
    Write-Warn2 '已指定 -SkipTests，跳过'
} else {
    Push-Location $BackendDir
    try {
        & $VenvPy -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw 'pytest 未全绿' }
    } finally { Pop-Location }
    Write-Ok 'pytest 全绿'
}

# ---------------------------------------------------------------- 6. serve
Write-Step '6/6 启动后端服务'
if ($NoServe) {
    Write-Warn2 '已指定 -NoServe，不启动 uvicorn'
    Write-Host "手动启动： & `"$VenvPy`" -m uvicorn app.main:app --reload --port $Port --app-dir `"$BackendDir`"" -ForegroundColor DarkGray
} else {
    Write-Host "    http://localhost:$Port/api/health" -ForegroundColor Green
    Write-Host "    http://localhost:$Port/docs" -ForegroundColor Green
    Push-Location $BackendDir
    try {
        & $VenvPy -m uvicorn app.main:app --reload --host 0.0.0.0 --port $Port
    } finally { Pop-Location }
}
