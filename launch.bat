@echo off
setlocal
cd /d "%~dp0"
title AI Lead Filter

rem ---- Find Python ----
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python was not found on this computer.
    echo Install it from https://www.python.org/downloads/
    echo and tick "Add Python to PATH" during setup. Then run this file again.
    pause
    exit /b 1
)

rem ---- First run: create the virtual environment and install packages ----
if not exist "venv\Scripts\python.exe" (
    echo First run: setting things up. This can take a few minutes.
    %PY% -m venv venv
    if errorlevel 1 (
        echo Could not create the virtual environment.
        pause
        exit /b 1
    )
    "venv\Scripts\python.exe" -m pip install --upgrade pip
    "venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Package install failed. Check your internet connection and try again.
        rmdir /s /q venv
        pause
        exit /b 1
    )
)

rem ---- Make sure there is a .env file for the API key ----
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
    ) else (
        > ".env" echo GOOGLE_API_KEY=
    )
    echo No .env file was found, so one was created.
    echo Notepad will open now. Paste your Gemini API key after GOOGLE_API_KEY=
    echo then save the file and close Notepad.
    notepad ".env"
)

rem ---- Start the app ----
"venv\Scripts\python.exe" app.py
if errorlevel 1 (
    echo.
    echo The app closed with an error. If a crash_log.txt file exists, it has the details.
    pause
)

endlocal
