@echo off
chcp 65001 >nul
:: 仅启动 cpolar 穿透前端 dev 端口（默认 5174）。
:: 单独提供，便于先装好 cpolar、再单独拉起隧道。
set CPOLAR=
if exist "%ProgramFiles%\cpolar\cpolar.exe" set CPOLAR=%ProgramFiles%\cpolar\cpolar.exe
if exist "%ProgramFiles(x86)%\cpolar\cpolar.exe" set CPOLAR=%ProgramFiles(x86)%\cpolar\cpolar.exe
where cpolar >nul 2>&1 && set CPOLAR=cpolar

if not defined CPOLAR (
  echo 未检测到 cpolar。请访问 https://www.cpolar.cn/ 下载安装（国产，国内可直连），
  echo 安装后重新打开本窗口即可。
  pause
  exit /b 1
)
echo.
echo ============================================================
echo   正在穿透 http://localhost:5174 ...
echo   cpolar 启动后会打印一行类似：
echo.
echo     https://1a2b3c4d.cpolar.cn -^> http://localhost:5174
echo.
echo   请复制 https://... 那整行，发给试用者手机浏览器打开。
echo   注意：不能用 xxxx.cpolar.cn 这种占位符，必须是窗口里打印的真实地址。
echo ============================================================
echo.
%CPOLAR% http 5174
