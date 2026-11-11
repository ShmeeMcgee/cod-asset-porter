@echo off
rem Builds "dist\CoD Asset Porter.exe" (no Python needed to run it).
title Building CoD Asset Porter
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python 3 isn't installed. Get it from https://www.python.org/downloads/
    pause
    exit /b 1
)

%PY% -m pip install --upgrade -r requirements.txt pyinstaller || goto :fail
%PY% -m unittest discover -s tests || goto :fail
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name "CoD Asset Porter" --icon icon.ico --add-data "icon.ico;." ^
    --collect-all tkinterdnd2 app.py || goto :fail

echo.
echo Done: dist\CoD Asset Porter.exe
pause
exit /b 0

:fail
echo.
echo Build failed - see the messages above.
pause
exit /b 1
