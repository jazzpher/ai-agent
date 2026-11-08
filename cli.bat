@echo off
REM Terminal coding agent. Works in the folder you run it from.
REM   cli.bat                 chat
REM   cli.bat -p "prompt"     one-shot
REM   cli.bat -C ..\project   another folder
setlocal
set "APPDIR=%~dp0"
if not exist "%APPDIR%venv\Scripts\python.exe" (
    echo venv not found. Run install.bat first.
    exit /b 1
)
"%APPDIR%venv\Scripts\python.exe" "%APPDIR%cli.py" %*
