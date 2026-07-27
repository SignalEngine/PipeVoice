@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM Launch Pipevoice. First run sets up a virtual environment.
REM If keystrokes don't reach an *elevated* terminal, right-click -> Run as administrator.
cd /d "%~dp0"
if not exist ".venv" (
    echo First run: creating virtual environment and installing dependencies...
    python -m venv .venv
    if errorlevel 1 goto :setup_failed
)

set "REQ_HASH="
for /f %%H in ('powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 'requirements.txt').Hash"') do set "REQ_HASH=%%H"
if not defined REQ_HASH set "REQ_HASH=unknown"

set "INSTALLED_HASH="
if exist ".venv\requirements.sha256" set /p INSTALLED_HASH=<".venv\requirements.sha256"
if /I not "!REQ_HASH!"=="!INSTALLED_HASH!" (
    echo Installing updated dependencies...
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 (
        REM An existing environment may still be usable when the package index is offline.
        .venv\Scripts\python.exe -m pip install --no-index -r requirements.txt >nul 2>&1
        if errorlevel 1 goto :setup_failed
        echo Dependency update unavailable; launching with the installed packages.
    ) else (
        >".venv\requirements.sha256" echo !REQ_HASH!
    )
)

if not exist "assets\wisprlite.ico" (
    .venv\Scripts\python.exe assets\make_icon.py
    if errorlevel 1 echo Icon generation failed; launching without a custom icon.
)
.venv\Scripts\python.exe -m wisprlite
if errorlevel 1 goto :launch_failed
goto :end

:setup_failed
echo Pipevoice dependency setup failed. Check your connection and try again.
pause
exit /b 1

:launch_failed
echo Pipevoice failed to launch.
pause
exit /b 1

:end
pause
