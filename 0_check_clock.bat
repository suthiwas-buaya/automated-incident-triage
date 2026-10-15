@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Step 0 - Router Clock Sync

echo ============================================================
echo   STEP 0 - Router clock sync  (run this FIRST, before 1-5)
echo ------------------------------------------------------------
echo   Router clocks do not survive a lab reboot. If they are not
echo   set, every syslog line arrives with a "*" and the system
echo   creates NO tickets at all - while looking perfectly normal.
echo.
echo   This connects to all 4 routers AT THE SAME TIME and fires
echo   the clock command on a whole-second boundary, compensating
echo   for SSH travel time. One pass sets and verifies.
echo ============================================================
echo.

py check_clock.py --set

echo.
echo ============================================================
echo   Done. Green check on all four routers = ready to test.
echo   Yellow means sub-second drift remains - acceptable, but
echo   write it down as measurement error in the report.
echo ============================================================
echo.
pause
