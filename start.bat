@echo off
echo ============================================================
echo  DWSIM Agentic AI - Starting services
echo ============================================================

echo.
echo [1/2] Building React UI (if needed)...
cd /d "%~dp0frontend"
if not exist "build\index.html" (
    echo     build not found - running npm run build ...
    call "C:\Program Files\nodejs\npm.cmd" run build
    if errorlevel 1 (echo ERROR: React build failed & pause & exit /b 1)
    echo     React build complete.
) else (
    echo     build\index.html found - skipping build.
)

echo.
echo [2/2] Starting FastAPI backend on http://localhost:8080 ...
cd /d "%~dp0backend"
start "DWSIM Backend" cmd /k "call venv\Scripts\activate.bat && python api.py"

echo.
echo ============================================================
echo  Services starting:
echo.
echo  Classic UI (ui.html):  http://localhost:8080/
echo  React UI:              http://localhost:8080/app
echo  API docs (Swagger):    http://localhost:8080/docs
echo.
echo  (React dev server optional: cd frontend && npm start)
echo ============================================================
