@echo off
echo ============================================================
echo  DWSIM Full Setup - Install backend + frontend dependencies
echo ============================================================

echo.
echo [1/3] Creating Python virtual environment...
cd /d "%~dp0backend"
python -m venv venv
if errorlevel 1 (echo ERROR: Python not found. Install from python.org & pause & exit /b 1)

echo.
echo [2/3] Installing Python packages...
call venv\Scripts\activate.bat
pip install -r requirements.txt
if errorlevel 1 (echo ERROR: pip install failed & pause & exit /b 1)

echo.
echo [3/3] Installing Node packages...
cd /d "%~dp0frontend"
call npm install
if errorlevel 1 (echo ERROR: npm install failed. Install Node.js from nodejs.org & pause & exit /b 1)

echo.
echo ============================================================
echo  Setup complete!
echo.
echo  Next steps:
echo    1. Copy backend\.env.example to backend\.env
echo    2. Add your API key to backend\.env
echo    3. Run: start.bat
echo ============================================================
pause
