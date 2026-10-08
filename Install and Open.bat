@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title DJI Flight Log Viewer

echo.
echo  DJI Flight Log Viewer
echo  Windows one-click setup
echo  ------------------------
echo.

call :find_python
if errorlevel 1 goto :install_python

:have_python
echo Using Python:
"%PY%" -c "import sys; print(sys.version)"
echo.

if not exist ".venv\Scripts\python.exe" (
  echo Creating a local Python environment...
  "%PY%" -m venv .venv
  if errorlevel 1 goto :fail
)

echo Installing required packages. The first run needs an internet connection.
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto :fail

echo.
echo Starting the viewer at http://127.0.0.1:8080
echo Leave this window open. Closing it stops the viewer.
echo.

start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8080/"
set "HOST=127.0.0.1"
set "PORT=8080"
".venv\Scripts\python.exe" "%~dp0app.py"
echo.
echo The viewer has stopped.
pause
exit /b 0

:install_python
echo Python 3.10 or newer was not found.
where winget >nul 2>&1
if errorlevel 1 goto :no_winget
echo Installing Python 3.12 for this Windows user. This can take a few minutes.
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :fail
call :find_python_local
if errorlevel 1 goto :fail
goto :have_python

:no_winget
echo.
echo Install Python 3.12 from https://www.python.org/downloads/
echo On the first installer screen, tick "Add python.exe to PATH".
echo Then double-click this file again.
echo.
pause
exit /b 1

:fail
echo.
echo Setup failed. Read the messages above, then try again.
echo.
pause
exit /b 1

:find_python
set "PY="
where py >nul 2>&1
if errorlevel 1 goto :try_python_cmd
py -3 -c "import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if errorlevel 1 goto :try_python_cmd
py -3 -c "import sys; print(sys.executable)" > "%TEMP%\dji-viewer-python.txt"
set /p PY=<"%TEMP%\dji-viewer-python.txt"
if not defined PY exit /b 1
exit /b 0

:try_python_cmd
where python >nul 2>&1
if errorlevel 1 exit /b 1
python -c "import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
python -c "import sys; print(sys.executable)" > "%TEMP%\dji-viewer-python.txt"
set /p PY=<"%TEMP%\dji-viewer-python.txt"
if not defined PY exit /b 1
exit /b 0

:find_python_local
set "PY="
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
  if exist "%%D\python.exe" set "PY=%%D\python.exe"
)
if not defined PY exit /b 1
"%PY%" -c "import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
exit /b 0
