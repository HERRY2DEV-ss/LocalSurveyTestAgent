@echo off
cd /d "%~dp0"
py --version >nul 2>nul
if errorlevel 1 (
  echo Python 3 is required. Install Python 3.10 or newer with the Python launcher enabled.
  pause
  exit /b 1
)
py -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed. Check your internet connection and Python installation.
  pause
  exit /b 1
)
py -m playwright install chromium
if errorlevel 1 (
  echo Chromium installation failed. Check your internet connection and rerun setup.bat.
  pause
  exit /b 1
)
py setup_password.py
if errorlevel 1 (
  echo Password setup did not complete.
  pause
  exit /b 1
)
echo Setup complete. Start the dashboard with run_agent.bat.
pause
