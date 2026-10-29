@echo off
cd /d "%~dp0"
if not exist ".env" (
  echo Local password configuration is missing.
  echo Run setup.bat first.
  pause
  exit /b 1
)
py -c "import flask, flask_wtf, dotenv, openpyxl" >nul 2>nul
if errorlevel 1 (
  echo Required packages are missing. Run setup.bat first.
  pause
  exit /b 1
)
py -c "import playwright" >nul 2>nul
if errorlevel 1 (
  echo Playwright is missing. Run setup.bat first.
  pause
  exit /b 1
)
start "Local Survey Test Agent Server" /min py app.py
set /a attempts=0
:wait_for_server
py -c "import urllib.request; d=urllib.request.urlopen('http://127.0.0.1:8765/login', timeout=1).read().decode(); assert 'Sign in - Local Test Agent' in d" >nul 2>nul
if not errorlevel 1 goto server_ready
set /a attempts+=1
if %attempts% geq 20 goto server_timeout
timeout /t 1 /nobreak >nul
goto wait_for_server
:server_timeout
echo The dashboard did not start. Port 8765 may be occupied; check the minimized server window.
pause
exit /b 1
:server_ready
echo Dashboard URL: http://127.0.0.1:8765
start "" "http://127.0.0.1:8765"
echo The local server is running in its minimized window. Close it to stop the app.
pause
