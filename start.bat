@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE="
if defined CONDA_PREFIX (
    if exist "%CONDA_PREFIX%\python.exe" set "PYTHON_EXE=%CONDA_PREFIX%\python.exe"
)
if not defined PYTHON_EXE (
    if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"
)
if not defined PYTHON_EXE (
    if exist "C:\Python\Miniforge3\python.exe" set "PYTHON_EXE=C:\Python\Miniforge3\python.exe"
)

if not defined PYTHON_EXE (
    echo [ERROR] Python environment not found. Please activate your Miniforge environment first.
    pause
    exit /b 1
)

if /i "%~1"=="--check" (
    echo Using Python: %PYTHON_EXE%
    "%PYTHON_EXE%" -c "import uvicorn, industrypulse; print('ENV_OK')"
    exit /b %errorlevel%
)

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8765"

set "ENV_ARG="
if exist ".env" set "ENV_ARG=--env-file .env"

echo Starting IndustryPulse...
echo Python: %PYTHON_EXE%
echo Preferred URL: http://127.0.0.1:%PORT%/
echo Press Ctrl+C to stop.
echo.

"%PYTHON_EXE%" -m industrypulse.cli serve --host 127.0.0.1 --port %PORT% %ENV_ARG%
if errorlevel 1 (
    echo.
    echo [ERROR] Server exited unexpectedly.
    pause
)
