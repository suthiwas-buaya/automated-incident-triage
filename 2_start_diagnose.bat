@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Diagnose Worker
py diagnose_worker.py
pause
