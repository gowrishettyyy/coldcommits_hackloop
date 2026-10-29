@echo off
REM CampusPulse - double-click this file to start the whole app on Windows.
REM It sets up the project toolbox once, installs the tools once, then starts.
setlocal
cd /d "%~dp0"

echo.
echo   CampusPulse
echo   -----------
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo   Python is not installed, or Windows cannot find it.
  echo   Get Python 3.12 from python.org and tick "Add python.exe to PATH" while installing.
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo   First run: creating the project toolbox ^(.venv^) ...
  python -m venv .venv
  if errorlevel 1 (
    echo   Could not create the toolbox. Copy this whole window and ask for help.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -c "import cv2, ultralytics" >nul 2>nul
if errorlevel 1 (
  echo   Installing the tools. This is a one-time download of about 300 MB. Leave this window open.
  echo.
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo   The install failed. Copy the red text at the end of this window and ask for help.
    pause
    exit /b 1
  )
)

echo   Starting CampusPulse. Your browser will open at http://localhost:8000
echo   Keep this window open while you use the app. Press Ctrl+C here to stop it.
echo.
".venv\Scripts\python.exe" run.py %*

echo.
pause
