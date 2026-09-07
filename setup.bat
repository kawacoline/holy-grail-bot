@echo off
echo =========================================
echo Setting up holy-grail-bot Environment...
echo =========================================

echo.
echo [1/2] Creating Python Virtual Environment. Please wait...
python -m venv venv
if %errorlevel% neq 0 (
    echo [ERROR] Failed to create virtual environment. Ensure you have python installed.
    pause
    exit /b 1
)

echo.
echo [2/2] Installing requirements from requirements.txt...
call venv\Scripts\activate.bat
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo =========================================
echo Setup Complete! 
echo Remember to configure your client files in clients/ before starting.
echo You can run the bot using RUN_24_7.bat
echo =========================================
pause
