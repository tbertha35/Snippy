@echo off
REM ============================================================================
REM build_windows.bat \u2014 one-command Windows build for Snippy
REM ============================================================================
REM
REM Produces:
REM   dist\Snippy\              (the PyInstaller one-folder build, used by Inno)
REM   dist\Snippy-Setup-X.Y.Z.exe (the Inno Setup installer)
REM   dist\Snippy-X.Y.Z-portable.zip (just the folder, no installer \u2014 for USB)
REM
REM Used by .github\workflows\release.yml on windows-latest runners.
REM Can also be run locally if you have Python 3.10+ and Inno Setup 6.x.
REM
REM Usage:
REM   packaging\build_windows.bat                 (defaults to current version)
REM   packaging\build_windows.bat 0.3.0           (override the version)
REM
REM Requirements:
REM   - Python 3.10+ on PATH
REM   - pip install pyinstaller==6.*
REM   - Inno Setup 6.x installed (ISCC.exe on PATH, or at
REM     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe")
REM
REM ============================================================================

setlocal enabledelayedexpansion

REM --- 1. Resolve version -----------------------------------------------------
set VERSION=%1
if "%VERSION%"=="" (
    for /f "tokens=*" %%i in ('python -c "import snippy; print(snippy.__version__)"') do set VERSION=%%i
)
echo.
echo === Snippy Windows build (version %VERSION%) ===
echo.

REM --- 2. Make sure pyinstaller is installed ---------------------------------
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    python -m pip install pyinstaller==6.*
)

REM --- 3. Clean previous build ------------------------------------------------
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

REM --- 4. Run PyInstaller -----------------------------------------------------
echo.
echo [1/3] Running PyInstaller...
pyinstaller packaging\pyinstaller.spec --clean --noconfirm
if errorlevel 1 (
    echo PyInstaller FAILED
    exit /b 1
)

REM --- 5. Run Inno Setup ------------------------------------------------------
echo.
echo [2/3] Running Inno Setup...
set ISCC=
for %%p in (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    "C:\Program Files\Inno Setup 6\ISCC.exe"
) do (
    if exist %%~p if "!ISCC!"=="" set ISCC=%%~p
)
if "!ISCC!"=="" (
    echo Inno Setup not found. Install it from https://jrsoftware.org/isdl.php
    echo Skipping installer creation. The dist\Snippy\ folder is still valid.
) else (
    "!ISCC!" packaging\inno_setup.iss /DSnippyVersion=%VERSION%
    if errorlevel 1 (
        echo Inno Setup FAILED
        exit /b 1
    )
)

REM --- 6. Build a portable .zip (for USB-stick distribution) -----------------
echo.
echo [3/3] Building portable .zip...
powershell -NoProfile -Command ^
    "Compress-Archive -Path 'dist\Snippy\*' -DestinationPath 'dist\Snippy-%VERSION%-portable.zip' -Force"
if errorlevel 1 (
    echo PowerShell Compress-Archive FAILED
    exit /b 1
)

echo.
echo === Build complete ===
echo Output: dist\
dir /b dist
echo.
endlocal