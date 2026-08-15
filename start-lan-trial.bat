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

REM 校验 IP 是否合法（不能是占位符/空）
ECHO %LAN_IP% | findstr /R "^192\.168\.[0-9][0-9]*\.[0-9][0-9]*$" >nul
IF %ERRORLEVEL% NEQ 0 (
  ECHO.
  ECHO [错误] 没探测到合法的局域网 IP，可能本机未连接 WiFi/有线网络。
  ECHO        请确认电脑和手机连在同一网络后再试。
  ECHO        下面 ipconfig 输出供排查：
  ipconfig | findstr /R "IPv4"
  pause
  EXIT /B 1
)

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
SET "PHONE_URL=http://%LAN_IP%:5174"
SET "BUILD_URL=http://%LAN_IP%:4173"

REM 把真实地址写入剪贴板，避免用户复制源码占位符
powershell -NoProfile -Command "Set-Clipboard -Value '%PHONE_URL%'" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
  SET CLIP_MSG=（已复制到剪贴板，直接 Ctrl+V 粘贴发送）
) else (
  SET CLIP_MSG=（请手动复制下面这一行）
)

ECHO.
ECHO ============================================================
ECHO.
ECHO   手机访问地址：%CLIP_MSG%
ECHO.
ECHO     %PHONE_URL%
ECHO.
ECHO   构建模式则为：%BUILD_URL%
ECHO.
ECHO   注意：
ECHO     1. 上面必须是 192.168.x.x 这种真实数字，不能带 %% 号。
ECHO     2. 手机和电脑必须连同一个 WiFi 或有线局域网。
ECHO     3. 如果手机打不开，检查 Windows 防火墙是否放行 5174 和 8000。
ECHO.
ECHO   后端文档：http://%LAN_IP%:8000/docs
ECHO   停止：关闭弹出的后端/前端两个命令行窗口。
ECHO.
ECHO ============================================================

REM 额外把地址写进 txt 文件，防止剪贴板失败
ECHO %PHONE_URL% > "%~dp0LAN_URL.txt"
ECHO 地址也保存到：%~dp0LAN_URL.txt

pause
ENDLOCAL
