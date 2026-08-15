@echo off
rem Start the disc builder, installing the two Python packages it needs the
rem first time.
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (set PY=py) else (set PY=python)

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" 2>nul
if errorlevel 1 (
  echo Python 3.8 or newer is needed. Install it from python.org and tick
  echo "Add Python to PATH" during setup.
  pause
  exit /b 1
)

%PY% -c "import numpy, pycdlib" 2>nul
if errorlevel 1 (
  echo Installing the Python packages this needs...
  %PY% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Could not install them. Try running: %PY% -m pip install -r requirements.txt
    pause
    exit /b 1
  )
)

%PY% -m rb2dx gui
if errorlevel 1 pause
