@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Dashboard API - KEEP OPEN
py dashboard\backend\app.py
pause
