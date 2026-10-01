# PyInstaller spec - build with build_exe.bat / build_installer.bat
import re
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable,
                                                 StringStruct, VarFileInfo, VarStruct)

VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', Path("minute_filler/__init__.py").read_text()).group(1)
nums = tuple(int(x) for x in (VERSION.split(".") + ["0"] * 4)[:4])
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=nums, prodvers=nums),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "Noah Collin"),
            StringStruct("FileDescription", "DjinnItAgreementForm"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "DjinnItAgreementForm"),
            StringStruct("OriginalFilename", "DjinnItAgreementForm.exe"),
            StringStruct("ProductName", "DjinnItAgreementForm"),
            StringStruct("ProductVersion", VERSION),
            StringStruct("LegalCopyright", "Noah Collin"),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

hidden = collect_submodules("winrt") + ["openpyxl"]  # openpyxl: imported only when needed (run sheet, Excel export)

a = Analysis(
    ["minute_filler/main.py"],
    pathex=["."],
    datas=[
        ("minute_filler/forms/*.pdf", "minute_filler/forms"),
        ("minute_filler/assets/app.ico", "minute_filler/assets"),
        ("minute_filler/assets/djinn_*.jpg", "minute_filler/assets"),
        ("minute_filler/rate_sheets/*.csv", "minute_filler/rate_sheets"),
        ("minute_filler/templates/*.xlsx", "minute_filler/templates"),
    ],
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtNetwork", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="DjinnItAgreementForm",
    icon="minute_filler/assets/app.ico",
    version=version_info,
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="DjinnItAgreementForm", upx=False)
