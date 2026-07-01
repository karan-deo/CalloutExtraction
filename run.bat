@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM  CalloutExtraction - run the app
REM
REM  Starts the annotation server (which auto-preprocesses any
REM  PDFs in pdfs/ first) and opens the UI in the default browser
REM  only once the server is actually accepting connections.
REM
REM  Run by double-clicking, or from a terminal in this folder.
REM ============================================================

REM Always operate from the directory this script lives in.
cd /d "%~dp0"

set "HOST=127.0.0.1"
set "PORT=5000"
set "URL=http://%HOST%:%PORT%/"

REM Give up waiting after MAX_ATTEMPTS * 2 seconds so a crashed server
REM doesn't leave us polling forever. Preprocessing can be slow, so this
REM is deliberately generous (500 * 6s = 50 minutes).
set /a MAX_ATTEMPTS=500

echo.
echo === CalloutExtraction ===
echo Working directory: %CD%
echo.

REM Start the server in its own window. It runs preprocessing, then serves.
echo Starting server (this also preprocesses any new PDFs)...
start "CalloutExtraction server" cmd /k "uv run app.py"

echo Waiting for %URL% to come up...
set /a attempt=0
:wait
set /a attempt+=1
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -Uri '%URL%' -TimeoutSec 3 ^| Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto ready
if %attempt% geq %MAX_ATTEMPTS% (
    echo.
    echo ERROR: server did not respond after waiting. Check the server window.
    endlocal
    exit /b 1
)
timeout /t 6 /nobreak >nul
goto wait

:ready
echo Server is up. Opening %URL% in your browser...
start "" "%URL%"
endlocal
exit /b 0
