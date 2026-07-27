@echo off
REM Launch Pipevoice. First run sets up a virtual environment.
REM If keystrokes don't reach an *elevated* terminal, right-click -> Run as administrator.
cd /d "%~dp0"
if not exist ".venv" (
    echo First run: creating virtual environment and installing dependencies...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install --upgrade pip
)
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
if not exist "assets\wisprlite.ico" python assets\make_icon.py
python -m wisprlite
pause
