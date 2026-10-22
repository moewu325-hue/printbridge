# -*- mode: python ; coding: utf-8 -*-
"""Windows x64单文件；依赖由固定清单安装，资源只读。"""
from pathlib import Path
from PyInstaller.utils.hooks import collect_dynamic_libs

root = Path(SPECPATH)
a = Analysis(
    [str(root / 'main.py')], pathex=[str(root)],
    binaries=collect_dynamic_libs('uiautomation'),
    datas=[(str(root / 'resources' / 'icon.png'), 'resources'),
           (str(root / 'LICENSE'), '.'),
           (str(root / 'THIRD_PARTY_NOTICES.md'), '.'),
           (str(root / 'resources' / 'licenses'), 'licenses')],
    # win32timezone 由 pywin32 在 EnumJobs 时惰性导入，冻结后需显式收集
    hiddenimports=['uiautomation', 'comtypes', 'pystray._win32', 'win32timezone'],
    excludes=['matplotlib', 'numpy', 'scipy', 'pandas', 'tkinter.test',
              'unittest', 'test', 'pytest', 'IPython'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='PrintBridge', debug=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    icon=str(root / 'resources' / 'icon.png'),
)
