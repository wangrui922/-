@echo off
chcp 65001 >nul
:: ============================================================
::  AI 数学教练 · 本机穿透公网试用（不租服务器、外地人也能用）
::  原理：只把前端 dev(5174) 穿透出去，/api 由 vite 代理回本机
::        后端(8000)，远程浏览器只跟前端公网地址通信，后端不暴露。
::  使用前：先按 PUBLIC_TRIAL.md 装好 cpolar（国产，国内可直连）。
:: ============================================================
setlocal
cd /d "%~dp0"

:: 穿透后前端源是随机公网域名，放开 CORS（dev 专用，配合 credentials=False 安全）
set CORS_ALLOW_ALL=1

echo.
echo ===================================================
echo  启动后端 (FastAPI :8000) ...
echo ===================================================
start "后端-backend" cmd /k "cd /d %~dp0backend && call .venv\Scripts\activate.bat && set CORS_ALLOW_ALL=1 && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"

echo.
echo ===================================================
echo  启动前端 (Vue dev :5174) ...
echo ===================================================
start "前端-student-web" cmd /k "cd /d %~dp0student-web && npm run dev"

:: 等待前端起来
timeout /t 6 >nul

echo.
echo ===================================================
echo  启动 cpolar 穿透前端 :5174 ...
echo ===================================================
:: 定位 cpolar
set CPOLAR=
if exist "%ProgramFiles%\cpolar\cpolar.exe" set CPOLAR=%ProgramFiles%\cpolar\cpolar.exe
if exist "%ProgramFiles(x86)%\cpolar\cpolar.exe" set CPOLAR=%ProgramFiles(x86)%\cpolar\cpolar.exe
where cpolar >nul 2>&1 && set CPOLAR=cpolar

if defined CPOLAR (
  start "穿透-cpolar" cmd /k "%CPOLAR% http 5174"
  echo  [ok] 已尝试启动 cpolar，请在「穿透-cpolar」窗口复制形如
  echo       https://xxxx.cpolar.cn  的公网地址，发给试用者手机打开。
) else (
  echo  [!] 未检测到 cpolar。请先按 PUBLIC_TRIAL.md 安装 cpolar，然后：
  echo      1) 双击本目录 start-cpolar.bat
  echo      2) 复制它打印的 https://xxxx.cpolar.cn 发给试用者
)

echo.
echo  试用者手机打开该公网地址即可。登录：任意11位手机号 -> 获取验证码
echo  -> 页面直接显示「开发环境验证码：XXXXXX」-> 填入登录。
echo  （保持本批处理打开的两个后端/前端窗口运行，关掉即失效）
echo.
pause
endlocal
