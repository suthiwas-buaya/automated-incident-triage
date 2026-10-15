@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Syslog Server
py syslog_server.py
pause
