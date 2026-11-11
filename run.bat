@echo off
title CoD Asset Porter
cd /d "%~dp0"

rem Prefer the "py" launcher; fall back to python on PATH.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python 3 isn't installed.
    echo Get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

if not exist ".setup_done_v1" (
    echo First run: installing the libraries this app needs...
    %PY% -m pip install --upgrade -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Installing failed. Check your internet connection and run this again.
        pause
        exit /b 1
    )
    echo ok> ".setup_done_v1"
)

%PY% app.py
if errorlevel 1 pause
