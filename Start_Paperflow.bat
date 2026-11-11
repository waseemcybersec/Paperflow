@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Setting up a private Python environment for Paperflow...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv .venv
    ) else (
        python -m venv .venv
    )
    if errorlevel 1 goto setup_error
)

if not exist ".venv\Scripts\paper-collector-ui.exe" (
    echo Installing Paperflow and its required packages. This first setup needs internet access...
    ".venv\Scripts\python.exe" -m pip install -e .
    if errorlevel 1 goto setup_error
)

".venv\Scripts\paper-collector-ui.exe"
goto done

:setup_error
echo.
echo Paperflow could not finish setup. Confirm Python 3.11 or newer is installed and try again.
pause

:done
endlocal
