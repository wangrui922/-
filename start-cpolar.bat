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
echo 正在穿透 http://localhost:5174 ...
echo 复制下方打印的 https://xxxx.cpolar.cn 公网地址发给试用者。
%CPOLAR% http 5174
