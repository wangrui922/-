@echo off
REM ============================================================
REM  错题小老师 - 本机 LAN 试用一键启动
REM  用法:
REM    双击此文件            -> 开发模式(推荐, 改完即时生效)
REM    start-lan-trial build  -> 构建静态版(注入 VITE_API_BASE, 更轻量)
REM  手机试用: 手机连同一 WiFi, 浏览器打开下方"手机访问地址"
REM  停止: 关闭弹出的两个命令行窗口
REM ============================================================
SETLOCAL
cd /d "%~dp0"

REM 1) 探测本机局域网 IP (优先 192.168 段, 否则取第一个 IPv4)
SET LAN_IP=
FOR /F "tokens=2 delims=:" %%a IN ('ipconfig ^| findstr /R "IPv4.*192\.168"') DO (
  IF NOT DEFINED LAN_IP SET "LAN_IP=%%a"
)
IF NOT DEFINED LAN_IP (
  FOR /F "tokens=2 delims=:" %%a IN ('ipconfig ^| findstr /R "IPv4"') DO (
    IF NOT DEFINED LAN_IP SET "LAN_IP=%%a"
  )
)
SET "LAN_IP=%LAN_IP: =%"
ECHO 本机局域网 IP: %LAN_IP%

IF /I "%1"=="build" GOTO :BUILD

REM ---------- 开发模式(默认): vite dev + 后端, 走代理, 无需注入 API 地址 ----------
SET "PUBLIC_BASE_URL=http://%LAN_IP%:5174"
start "backend" cmd /k "pushd "%~dp0backend" && set PUBLIC_BASE_URL=%PUBLIC_BASE_URL% && .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
start "frontend" cmd /k "pushd "%~dp0student-web" && npm run dev"
GOTO :DONE

:BUILD
REM ---------- 构建静态版: 注入 VITE_API_BASE 直连后端 LAN 地址 ----------
SET "PUBLIC_BASE_URL=http://%LAN_IP%:4173"
start "backend" cmd /k "pushd "%~dp0backend" && set PUBLIC_BASE_URL=%PUBLIC_BASE_URL% && .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
pushd "%~dp0student-web"
SET "VITE_API_BASE=http://%LAN_IP%:8000"
call npm run build
IF %ERRORLEVEL% NEQ 0 (
  ECHO 构建失败, 请检查 student-web/node_modules 是否完整
  popd
  pause
  EXIT /B 1
)
start "frontend" cmd /k "pushd "%~dp0student-web" && npm run preview -- --host --port 4173"
popd
GOTO :DONE

:DONE
ECHO.
ECHO ===================================================
ECHO  手机访问地址(发给试用者):
ECHO  http://%LAN_IP%:5174
ECHO  (构建模式则为 http://%LAN_IP%:4173)
ECHO  后端管理: http://%LAN_IP%:8000/docs
ECHO  停止: 关闭弹出的两个命令行窗口
ECHO ===================================================
ENDLOCAL
