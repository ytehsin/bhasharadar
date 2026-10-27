@echo off
setlocal EnableExtensions
title BhashaRadar
cd /d "%~dp0"

REM ---- Refuse to run from inside a zip (Windows extracts only this file to a temp folder)
if not exist "%~dp0app\main.py" (
  echo.
  echo  BhashaRadar files were not found next to run.bat.
  echo  If you opened run.bat from inside the zip, first right-click the zip,
  echo  choose "Extract All...", then open the extracted folder and run run.bat there.
  echo.
  pause
  exit /b 1
)

set "LOG=%~dp0run-log.txt"
echo BhashaRadar start %date% %time% > "%LOG%"

REM ---- Find a Python 3.10+ interpreter (python.org, py launcher, Anaconda, Miniconda)
set "PY="
if exist ".venv\installed.flag" if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe" & goto have_venv

for %%P in (
  "%USERPROFILE%\anaconda3\python.exe"
  "%USERPROFILE%\miniconda3\python.exe"
  "%LOCALAPPDATA%\anaconda3\python.exe"
  "%ProgramData%\anaconda3\python.exe"
  "%ProgramData%\miniconda3\python.exe"
) do if not defined PY if exist %%P call :try_py %%P

if not defined PY call :try_cmd py -3
if not defined PY call :try_cmd python
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if not defined PY if exist "%%D\python.exe" call :try_py "%%D\python.exe"

if not defined PY (
  echo.
  echo  Could not find Python 3.10 or newer.
  echo  Install it from https://www.python.org/downloads/  ^(tick "Add Python to PATH"^)
  echo  then run run.bat again.
  echo.
  pause
  exit /b 1
)

echo Using Python: %PY%
echo Using Python: %PY% >> "%LOG%"
%PY% -c "import platform,sys; print('Python', sys.version.split()[0], platform.machine())" >> "%LOG%" 2>&1
echo Creating a private environment in .venv (first run only)...
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv >> "%LOG%" 2>&1
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo  Could not create the .venv folder. Details are in run-log.txt
  echo.
  pause
  exit /b 1
)
set "PY=.venv\Scripts\python.exe"
echo Installing components (first run only, 1-3 minutes)...
"%PY%" -m pip install --upgrade pip >> "%LOG%" 2>&1
"%PY%" -m pip install --prefer-binary -r requirements.txt >> "%LOG%" 2>&1
if errorlevel 1 (
  echo.
  echo  Installing components failed. The last lines of run-log.txt:
  echo  -----------------------------------------------------------
  powershell -NoProfile -Command "Get-Content -Tail 25 '%LOG%'"
  echo.
  echo  Delete the .venv folder and try again, or send run-log.txt for help.
  pause
  exit /b 1
)
echo Installing optional speed-up (scikit-learn; skipped if unavailable for this PC)...
"%PY%" -m pip install --only-binary=:all: "scikit-learn>=1.4" >> "%LOG%" 2>&1
echo ok > ".venv\installed.flag"

:have_venv

REM ---- Pick the first free port from 8000
set "PORT=8000"
:port_loop
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul && (set /a PORT+=1 & goto port_loop)
set "BR_PORT=%PORT%"

echo.
echo  BhashaRadar is starting at http://127.0.0.1:%PORT%
echo  Your browser will open in a few seconds. Keep this window open while you use the app.
echo  Close this window to stop BhashaRadar.
echo.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 60;$i++){try{Invoke-WebRequest -UseBasicParsing http://127.0.0.1:%PORT%/api/status -TimeoutSec 2 | Out-Null; Start-Process 'http://127.0.0.1:%PORT%'; break}catch{Start-Sleep 1}}"
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%
echo.
echo  BhashaRadar stopped. If it stopped by itself, the error is shown above.
pause
exit /b 0

:try_py
"%~1" -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1 && set "PY="%~1""
exit /b 0

:try_cmd
%* -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1 && set "PY=%*"
exit /b 0
