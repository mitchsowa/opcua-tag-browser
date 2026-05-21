@echo off
setlocal enabledelayedexpansion

rem ============================================================
rem  OPC-UA Tag Browser - Windows launcher
rem  Verifies Python, creates .venv, installs deps, runs the TUI.
rem  Tested on Windows 10 and 11.
rem ============================================================

cd /d "%~dp0"

chcp 65001 >nul 2>&1

rem --- 0. Ensure Windows Terminal, then relaunch under it ---
rem    WT_SESSION is set automatically when running inside Windows Terminal.
rem    OPCUA_RELAUNCHED is our own sentinel so we never loop more than once.
if not defined WT_SESSION if not defined OPCUA_RELAUNCHED (
    where wt.exe >nul 2>&1
    if errorlevel 1 (
        echo Windows Terminal not found - attempting install via winget ...
        where winget >nul 2>&1
        if errorlevel 1 (
            echo [WARN] winget is not available on this system.
            echo        Install "Windows Terminal" manually from the Microsoft Store
            echo        or https://github.com/microsoft/terminal/releases
            echo        Continuing in the legacy console - display may be degraded.
            echo.
        ) else (
            winget install --id Microsoft.WindowsTerminal -e --source winget --accept-source-agreements --accept-package-agreements
            if errorlevel 1 (
                echo [WARN] Windows Terminal install did not complete.
                echo        Continuing in the legacy console.
                echo.
            )
        )
    )
    where wt.exe >nul 2>&1
    if not errorlevel 1 (
        set "OPCUA_RELAUNCHED=1"
        start "" wt.exe -d "%~dp0" cmd /c ""%~f0" %*"
        exit /b 0
    )
)

rem --- 1. Locate a working Python (3.10+) ---
set "PY="
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)

if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 (
        python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
        if not errorlevel 1 set "PY=python"
    )
)

if not defined PY (
    echo.
    echo [ERROR] Python 3.10 or newer was not found on PATH.
    echo         Install it from https://www.python.org/downloads/windows/
    echo         and make sure "Add python.exe to PATH" is checked.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%V in ('%PY% -c "import sys;print(sys.version.split()[0])"') do set "PYVER=%%V"
echo Using Python %PYVER%

rem --- 2. Create the virtual environment if missing ---
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment in .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

set "VENV_PY=.venv\Scripts\python.exe"

rem --- 3. Install / verify dependencies ---
"%VENV_PY%" -c "import textual, asyncua" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies from requirements.txt ...
    "%VENV_PY%" -m pip install --upgrade pip >nul
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
) else (
    echo Dependencies OK.
)

rem --- 4. Launch the TUI ---
echo Starting OPC-UA Tag Browser ...
"%VENV_PY%" opcua_tui.py %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo opcua_tui.py exited with code %RC%.
    pause
)

endlocal & exit /b %RC%
