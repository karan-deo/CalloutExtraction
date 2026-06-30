@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM  CalloutExtraction - Windows environment setup
REM
REM  Automates the uv-based setup documented in README.md:
REM    1. Ensure uv is installed (installs via winget if missing)
REM    2. Install the required Python version via uv
REM    3. Sync dependencies from uv.lock into .venv/
REM
REM  Run by double-clicking, or from a terminal in this folder.
REM ============================================================

REM Always operate from the directory this script lives in
REM (the one holding pyproject.toml / uv.lock).
cd /d "%~dp0"

echo.
echo === CalloutExtraction setup ===
echo Working directory: %CD%
echo.

REM ------------------------------------------------------------
REM 1. Ensure uv is available
REM ------------------------------------------------------------
where uv >nul 2>&1
if errorlevel 1 (
    echo [1/3] uv not found. Installing via winget...

    where winget >nul 2>&1
    if errorlevel 1 (
        echo.
        echo ERROR: winget is not available, so uv cannot be installed automatically.
        echo Install uv manually, then re-run this script:
        echo     https://docs.astral.sh/uv/getting-started/installation/
        goto :fail
    )

    winget install --id=astral-sh.uv -e --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo.
        echo ERROR: winget failed to install uv.
        goto :fail
    )

    REM winget updates PATH for new shells, but not this one. Re-check via
    REM the default install location so the rest of the script can proceed.
    where uv >nul 2>&1
    if errorlevel 1 (
        set "UV_BIN=%USERPROFILE%\.local\bin"
        if exist "!UV_BIN!\uv.exe" (
            set "PATH=!UV_BIN!;%PATH%"
        )
    )

    where uv >nul 2>&1
    if errorlevel 1 (
        echo.
        echo uv was installed but is not on PATH in this window.
        echo Close and reopen your terminal, then run setup.bat again.
        goto :fail
    )
) else (
    echo [1/3] uv already installed.
)

for /f "tokens=*" %%v in ('uv --version') do echo       %%v

REM ------------------------------------------------------------
REM 2. Install the required Python (version comes from .python-version)
REM ------------------------------------------------------------
echo.
echo [2/3] Installing Python toolchain via uv...
uv python install 3.14
if errorlevel 1 (
    echo.
    echo ERROR: 'uv python install' failed.
    goto :fail
)

REM ------------------------------------------------------------
REM 3. Sync dependencies from uv.lock
REM ------------------------------------------------------------
echo.
echo [3/3] Syncing dependencies into .venv\ ...
uv sync
if errorlevel 1 (
    echo.
    echo ERROR: 'uv sync' failed.
    goto :fail
)

echo.
echo === Setup complete ===
echo Run the app with:
echo     uv run app.py
echo Then open http://127.0.0.1:5000
echo.
endlocal
exit /b 0

:fail
echo.
echo === Setup failed ===
endlocal
exit /b 1
