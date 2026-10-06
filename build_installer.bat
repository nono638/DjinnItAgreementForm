@echo off
rem Tests, builds the exe, then wraps it in an installer:
rem   dist\installer\YinItAgreementForm-Setup-<version>.exe
rem Bump the version in minute_filler\__init__.py before each release.
cd /d "%~dp0"
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup 6 not found. Install it from https://jrsoftware.org/isdl.php  ^(or: winget install JRSoftware.InnoSetup^)
  pause & exit /b 1
)
".venv\Scripts\python.exe" -m pytest -q || (echo Tests failed - not building. & pause & exit /b 1)
rem A build after a release gets the next version number (a version not yet released on GitHub keeps its number).
rem For a bigger step run first:  python tools\bump_version.py minor   (or major, or an exact number)
".venv\Scripts\python.exe" tools\bump_version.py auto
for /f "tokens=2 delims==" %%V in ('findstr /r "__version__" minute_filler\__init__.py') do set "VER=%%~V"
set "VER=%VER: =%"
set "VER=%VER:"=%"
echo Building version %VER%


for %%D in (build dist) do (
  if not exist %%D mkdir %%D
  powershell -NoProfile -Command "Set-Content -Path %%D -Stream com.dropbox.ignored -Value 1"
)
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean YinItAgreementForm.spec || (echo PyInstaller failed & pause & exit /b 1)
"%ISCC%" /Qp /DMyAppVersion=%VER% installer\YinItAgreementForm.iss || (echo Inno Setup failed & pause & exit /b 1)
echo.
echo Installer: dist\installer\YinItAgreementForm-Setup-%VER%.exe
pause
