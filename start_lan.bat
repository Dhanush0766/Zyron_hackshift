@echo off
setlocal
title MedGate server (LAN mode for ESP32)
cd /d "%~dp0backend"
where python >nul 2>nul || (echo Python is not installed or not on PATH. Install Python 3.10 from python.org and tick "Add to PATH". & pause & exit /b 1)
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv || (echo Could not create .venv & pause & exit /b 1)
)
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, httpx" >nul 2>nul
if errorlevel 1 (
  echo Installing dependencies from the bundled wheels folder - no internet needed. Log: backend\setup.log
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-index --find-links wheels -r requirements.txt > setup.log 2>&1
  if errorlevel 1 (
    echo Offline install failed, trying the internet instead...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt >> setup.log 2>&1
    if errorlevel 1 (
      type setup.log
      echo.
      echo INSTALL FAILED. The full error is above and in backend\setup.log
      pause
      exit /b 1
    )
  )
)
echo.
rem Stop an older MedGate server that is still running (otherwise the browser keeps showing the old version).
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":8000 .*LISTENING"') do (
  echo Stopping an older server still running on port 8000 ^(process %%p^)...
  taskkill /PID %%p /F >nul 2>nul
)
echo  MedGate dashboard: http://127.0.0.1:8000
echo  Keep this window open while you use the dashboard. Close it to stop the server.
echo.
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn medgate.api:app --host 0.0.0.0 --port 8000
echo.
echo Server stopped. If it stopped by itself, the error is shown above.
pause
