@echo off
rem Builds dist\DjinnItAgreementForm\DjinnItAgreementForm.exe
cd /d "%~dp0"
for %%D in (build dist) do (
  if not exist %%D mkdir %%D
  powershell -NoProfile -Command "Set-Content -Path %%D -Stream com.dropbox.ignored -Value 1"
)
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean DjinnItAgreementForm.spec || (echo Build failed & pause & exit /b 1)
echo.
echo Built: dist\DjinnItAgreementForm\DjinnItAgreementForm.exe
pause
