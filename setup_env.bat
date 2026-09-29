@echo off
rem Creates the Python virtual environment in .venv (excluded from Dropbox sync).
cd /d "%~dp0"
if not exist .venv mkdir .venv
powershell -NoProfile -Command "Set-Content -Path .venv -Stream com.dropbox.ignored -Value 1"
py -3.12 -m venv .venv || goto :error
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\pip.exe" install -r requirements.txt || goto :error
echo.
echo Environment ready. Start the app with run.bat
pause
exit /b 0
:error
echo Setup failed.
pause
exit /b 1
