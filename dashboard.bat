@echo off
rem ==================================================================
rem  dashboard.bat - DOUBLE-CLICK THIS.
rem  It re-audits the newest day (or a day you pass) and opens the result
rem  in your browser. Needs Git Bash, which you already have installed.
rem  For a fixed day:  dashboard.bat 2026-09-23
rem ==================================================================
cd /d "%~dp0"
set BASH=%ProgramFiles%\Git\bin\bash.exe
if not exist "%BASH%" set BASH=C:\Program Files\Git\bin\bash.exe
if not exist "%BASH%" set BASH=bash.exe

"%BASH%" daily_check.sh %1
if errorlevel 1 (
  echo.
  echo [note] the audit finished with a warning code - that is normal when a test
  echo        has no data. The dashboard was still written: data\
)
echo.
echo Press any key to close this window (the browser tab stays open).
pause >nul
