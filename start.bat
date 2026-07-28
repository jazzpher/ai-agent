@echo off
echo ========================================
echo    AI Agent - Local AI Assistant
echo ========================================
echo.

REM Check if virtual environment exists
if not exist "venv\" (
    echo Creating virtual environment...
    python -m venv venv
    echo.
)

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate
echo.

REM Check if requirements are installed
echo Checking dependencies...
pip show gradio >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r requirements.txt
    echo.
)

REM Run the app
echo Starting AI Agent...
echo Opening browser at http://127.0.0.1:7860
echo.
python app.py

pause
