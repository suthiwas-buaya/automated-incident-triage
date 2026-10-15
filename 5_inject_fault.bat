@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Fault Injection
py inject_menu.py
pause
