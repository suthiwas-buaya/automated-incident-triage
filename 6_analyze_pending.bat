@echo off
cd /d "%~dp0"
:menu
cls
echo ============================================================
echo   Phase 3.5 - Analyze tickets that rules could not explain
echo ------------------------------------------------------------
echo   Requires: Ollama running
echo   (close CML first if the machine runs low on RAM)
echo ============================================================
echo.
echo   1. Run once   - analyze the current queue, then exit
echo   2. Watch      - keep running, analyze new tickets as they appear
echo   3. Dry run    - show results only, do not touch the database
echo.
set "mode="
set /p mode=Select (1-3): 

if "%mode%"=="1" (
    set "label=Run once"
) else if "%mode%"=="2" (
    set "label=Watch"
) else if "%mode%"=="3" (
    set "label=Dry run"
) else (
    echo.
    echo   [!] Pick 1, 2 or 3
    echo.
    pause
    goto menu
)

echo.
echo   Selected: %label%
set "ok="
set /p ok=Confirm? (Enter = yes, n = cancel): 
if /i "%ok%"=="n" goto menu

echo.
if "%mode%"=="2" (
    py analyze_pending.py --watch
) else if "%mode%"=="3" (
    py analyze_pending.py --dry-run
) else (
    py analyze_pending.py
)
echo.
pause
