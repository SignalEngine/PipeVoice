@echo off
REM Build a standalone Pipevoice.exe (no console window) with PyInstaller.
REM Output lands in dist\Pipevoice.exe. Run this after run.bat has set up .venv.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
pip install pyinstaller
if not exist "assets\wisprlite.ico" python assets\make_icon.py
pyinstaller --noconfirm --clean --noconsole --onedir --noupx --name Pipevoice ^
    --icon assets\wisprlite.ico ^
    --add-data "assets\wisprlite.ico;assets" ^
    --add-data "assets\pipevoice-lockup.png;assets" ^
    --collect-all deepgram ^
    --collect-all faster_whisper ^
    --collect-all ctranslate2 ^
    --collect-all mss ^
    --collect-all pystray ^
    --collect-all PIL ^
    --collect-all mcp ^
    --collect-all pydantic ^
    --collect-all anyio ^
    REM FastMCP imports these at module load and PyInstaller cannot see
    REM them statically. Missing pydantic_settings shipped a --mcp that
    REM died on import in v2.40.1 - the build was green, the feature was
    REM dead. The CI smoke test now runs the built exe to catch this.
    --collect-all pydantic_settings ^
    --collect-all jsonschema ^
    --collect-all sse_starlette ^
    --collect-all starlette ^
    --collect-all uvicorn ^
    --collect-all httpx ^
    launch.py
echo.
echo Done. See dist\Pipevoice\Pipevoice.exe
echo (Local Whisper downloads its model on first use; the exe stays small.)
