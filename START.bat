@echo off
cd /d "%~dp0"
title Holy Grail Trading System

set PYTHON_BIN=
if exist "venv\Scripts\python.exe" set PYTHON_BIN=venv\Scripts\python.exe

if "%PYTHON_BIN%"=="" (
    echo [ERROR] Virtual environment not found!
    echo Please run setup.bat first.
    pause
    exit /b
)

:loop
echo ======================================================
echo           HOLY GRAIL TRADING SYSTEM
echo ======================================================
echo.

%PYTHON_BIN% auto_updater.py

if %ERRORLEVEL% equ 69 (
    echo.
    echo [CRITICAL] EMERGENCY STOP INITIATED BY CIRCUIT BREAKER.
    echo The bot has been fully halted to protect your funds.
    pause
    exit /b
)

echo.
echo [WARNING] Process Exited! Restarting in 5 seconds...
timeout /t 5
goto loop
