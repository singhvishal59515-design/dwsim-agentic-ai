@echo off
title DWSIM Agentic AI Server
echo ============================================
echo  DWSIM Agentic AI v2  -  Starting server...
echo ============================================
echo.

:: Kill anything already on port 8080
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8080 ^| findstr LISTENING') do (
    echo Killing old process on port 8080 (PID %%a)...
    taskkill /F /PID %%a >nul 2>&1
)

:: Wait for port to clear
timeout /t 2 /nobreak >nul

:: Start server
cd /d "%~dp0"
echo Starting server at http://localhost:8080
echo Press Ctrl+C to stop.
echo.
python api.py
pause
