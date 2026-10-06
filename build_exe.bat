@echo off
rem Builds dist\YinItAgreementForm\YinItAgreementForm.exe
cd /d "%~dp0"
for %%D in (build dist) do (
  if not exist %%D mkdir %%D
  powershell -NoProfile -Command "Set-Content -Path %%D -Stream com.dropbox.ignored -Value 1"
)
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean YinItAgreementForm.spec || (echo Build failed & pause & exit /b 1)
echo.
echo Built: dist\YinItAgreementForm\YinItAgreementForm.exe
pause
