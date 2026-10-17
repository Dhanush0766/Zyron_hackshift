@echo off
setlocal
title Push MedGate to GitHub
cd /d "%~dp0"
where git >nul 2>nul || (echo Git is not installed. Install it from https://git-scm.com/download/win then run this again. & pause & exit /b 1)
set REPO=https://github.com/Dhanush0766/Zyron_hackshift.git
if not exist ".git" git init
git config user.name >nul 2>nul || git config user.name "Dhanush0766"
git config user.email >nul 2>nul || git config user.email "Dhanush0766@users.noreply.github.com"
git add -A
git commit -m "MedGate: health-gated medication dispenser (backend, dashboard, simulator, ESP32 firmware, docs)"
git branch -M main
git remote remove origin >nul 2>nul
git remote add origin %REPO%
echo.
echo A GitHub sign-in window may open: sign in as Dhanush0766.
git push -u origin main
if errorlevel 1 (echo. & echo PUSH FAILED. Read the message above. & pause & exit /b 1)
echo.
echo Done: https://github.com/Dhanush0766/Zyron_hackshift
pause
