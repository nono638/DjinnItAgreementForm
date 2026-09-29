@echo off
rem Starts DjinnItAgreementForm from source using the project venv.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo The .venv is missing. Run setup_env.bat first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m minute_filler.main %*
