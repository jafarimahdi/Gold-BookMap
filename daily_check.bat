@echo off
rem ==================================================================
rem  daily_check.bat - same as daily_check.sh but the console stays open.
rem  dashboard.bat is the one that opens the browser tab; this one is for
rem  reading the numbers in the terminal.  Pass a date to pick a day.
rem ==================================================================
cd /d "%~dp0"
set BASH=%ProgramFiles%\Git\bin\bash.exe
if not exist "%BASH%" set BASH=C:\Program Files\Git\bin\bash.exe
if not exist "%BASH%" set BASH=bash.exe

if "%1"=="" (
  "%BASH%" daily_check.sh --quiet
) else (
  "%BASH%" daily_check.sh %1 --quiet
)
echo.
echo (add nothing for the full output, or "--check" to verify the tester itself)
pause
