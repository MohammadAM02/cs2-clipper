# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for dist\\CS2Clipper.exe: the whole app in one windowed file. Run by build.ps1.

Adapted from thelifeofsuleyman/cs2-clipper's `packaging/aegis.spec` (MIT). What we changed: one file
instead of a folder; our entry point, name, icon and version; the pages as data; psycopg's binary
build with the libpq it brings (a delvewheel .libs folder); no obsws_python; and Pillow left to
PyInstaller's own hooks.

MIT License

Copyright (c) 2026 thelifeofsuleyman

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
import os
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_delvewheel_libs_directory, collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo)

ROOT = os.path.dirname(os.path.abspath(SPECPATH))
sys.path.insert(0, ROOT)
from clipper.icon import write_ico  # noqa: E402

with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
    VERSION = tomllib.load(f)["project"]["version"]
NUMBERS = tuple(int(part) for part in VERSION.split(".")) + (0,)    # 0.1.0 -> (0, 1, 0, 0)

ICON = Path(ROOT, "build", "CS2Clipper.ico")
ICON.parent.mkdir(exist_ok=True)
write_ico(ICON)

datas = [(os.path.join(ROOT, "clipper", "pages"), "clipper/pages")]
binaries = []
hiddenimports = ["clr", "webview.platforms.edgechromium", "webview.platforms.winforms",
                 "webview.platforms.mshtml"] + collect_submodules("psycopg")
for package in ("clr_loader", "pythonnet"):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports
# psycopg_binary's modules come in through psycopg's own imports (it cannot be imported on its own, so
# collect_all cannot scan it); its libpq, libssl and libcrypto sit beside it in psycopg_binary.libs.
datas, binaries = collect_delvewheel_libs_directory("psycopg_binary", datas=datas, binaries=binaries)

a = Analysis(
    [os.path.join(ROOT, "clipper", "__main__.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)

version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=NUMBERS, prodvers=NUMBERS),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("FileDescription", "CS2 Clipper"),     # the name Task Manager shows
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "CS2Clipper"),
            StringStruct("OriginalFilename", "CS2Clipper.exe"),
            StringStruct("ProductName", "CS2 Clipper"),
            StringStruct("ProductVersion", VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CS2Clipper",
    console=False,
    upx=False,
    icon=str(ICON),
    version=version,
)
