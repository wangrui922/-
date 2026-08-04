@echo off
chcp 65001 >nul 2>&1
title 错题小老师 - 本地服务器
echo ====================================
echo    错题小老师 正在启动...
echo ====================================
cd /d "%~dp0"
node server.js
pause
