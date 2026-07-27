# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 — PrintBridge (修复版)"""
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# 收集 tkinter 和 pystray 的全部子模块和资源文件
tkinter_hidden = collect_submodules('tkinter')
pystray_hidden = collect_submodules('pystray')
tkinter_datas = collect_data_files('tkinter')
pystray_datas = collect_data_files('pystray')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('resources/icon.png', 'resources'),
    ] + tkinter_datas + pystray_datas,
    hiddenimports=[
        # tkinter 全家桶
        'tkinter', 'tkinter.ttk', 'tkinter.messagebox', 'tkinter.filedialog',
        'tkinter.simpledialog', 'tkinter.scrolledtext',
        '_tkinter',
        # 打印相关
        'win32print', 'win32api', 'win32con',
        # 微信桥接
        'uiautomation', 'uiautomation.uiautomation',
        'comtypes', 'comtypes.stream', 'comtypes.client',
        # 托盘
        'pystray', 'pystray._win32', 'pystray._util',
        # 图片
        'PIL', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageTk',
        # 基础
        'queue', 'threading', 'json', 'sqlite3',
        'logging', 'socket', 'os', 'signal', 'shutil',
        'dataclasses', 'datetime', 'pathlib',
    ] + tkinter_hidden + pystray_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'numpy', 'scipy', 'pandas',
        'tkinter.test', 'unittest', 'test',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='PrintBridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='resources/icon.png',
)
