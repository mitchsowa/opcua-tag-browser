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
    call :find_wt
    if not defined WT_EXE (
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
            rem winget may need a moment to register the App Execution Alias
            timeout /t 2 /nobreak >nul 2>&1
            call :find_wt
        )
    )
    if defined WT_EXE (
        set "OPCUA_RELAUNCHED=1"
        rem Make sure WindowsApps is on PATH so the App Execution Alias resolves
        rem when we invoke wt.exe by bare name.
        set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WindowsApps"
        rem Strategy 1: bare name - lets ShellExecute resolve the App Execution
        rem Alias. This is the strategy that works for freshly-installed WT.
        start "" wt.exe -d "%CD%" cmd /c "%~nx0" %*
        if not errorlevel 1 exit /b 0
        rem Strategy 2: absolute path - works on systems where wt is a regular
        rem exe rather than a reparse-point alias.
        start "" "!WT_EXE!" -d "%CD%" cmd /c "%~nx0" %*
        if not errorlevel 1 exit /b 0
        echo.
        echo [WARN] Windows Terminal is installed but could not be launched.
        echo        Continuing in this console window. If you want the
        echo        Windows Terminal experience, close this window, open a
        echo        fresh cmd, and re-run start.bat.
        echo.
        rem Reset the sentinel so a future run can retry the relaunch.
        set "OPCUA_RELAUNCHED="
    ) else (
        echo.
        echo [INFO] Windows Terminal was installed but is not yet visible to this
        echo        shell. Close this window and re-run start.bat - the next run
        echo        will pick up Windows Terminal automatically.
        echo        Continuing in the legacy console for now.
        echo.
    )
)
goto :after_wt

:find_wt
set "WT_EXE="
for /f "delims=" %%I in ('where wt.exe 2^>nul') do if not defined WT_EXE set "WT_EXE=%%I"
if not defined WT_EXE if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe" set "WT_EXE=%LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe"
exit /b 0

:after_wt

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
rem    Check imports AND that asyncua is >= 1.1.5. Older asyncua raises
rem    "TypeError: issubclass() arg 1 must be a class" on connect under
rem    Python 3.12+, because dataclasses.fields() returns string-form types
rem    that older asyncua passes straight into issubclass().
"%VENV_PY%" -c "import textual, asyncua; v=tuple(int(x) for x in asyncua.__version__.split('.')[:3]); raise SystemExit(0 if v >= (1,1,5) else 2)" >nul 2>&1
set "DEPS_RC=%ERRORLEVEL%"
if "%DEPS_RC%"=="0" (
    echo Dependencies OK.
) else (
    if "%DEPS_RC%"=="2" (
        echo Upgrading asyncua - installed version is too old for this Python ...
    ) else (
        echo Installing dependencies from requirements.txt ...
    )
    "%VENV_PY%" -m pip install --upgrade pip >nul
    "%VENV_PY%" -m pip install --upgrade -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
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
