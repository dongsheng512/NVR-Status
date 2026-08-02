# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 配置 (PySide6): 在对应平台上执行
#   uv run pyinstaller --noconfirm NVRStatus.spec
#
# 可选: 将 ffmpeg/ffprobe 放入 bin/ 后一并打包(安装即用深度抽检)
# 图标: assets/AppIcon.icns (macOS) / assets/AppIcon.ico (Windows)

import os
import sys

block_cipher = None
root = os.path.abspath('.')

# 捆绑 bin/ 下的 ffmpeg/ffprobe(可选)
bins = []
bin_dir = os.path.join(root, 'bin')
if os.path.isdir(bin_dir):
    for name in os.listdir(bin_dir):
        p = os.path.join(bin_dir, name)
        if os.path.isfile(p) and not name.endswith('.md'):
            bins.append((p, 'bin'))

# 应用图标
icon_icns = os.path.join(root, 'assets', 'AppIcon.icns')
icon_ico = os.path.join(root, 'assets', 'AppIcon.ico')
icon_png = os.path.join(root, 'assets', 'app_logo.png')
if sys.platform == 'darwin' and os.path.isfile(icon_icns):
    app_icon = icon_icns
elif sys.platform == 'win32' and os.path.isfile(icon_ico):
    app_icon = icon_ico
else:
    app_icon = None

# PySide6 是 LGPL: 动态链接即可,无需 collect_all(打包器自带 hook)
# 仅附带应用图标资源
datas = []
if os.path.isfile(icon_png):
    datas.append((icon_png, 'assets'))

# 精简无用 Qt 模块,显著减小体积
excludes = [
    'PySide6.QtWebEngineCore',
    'PySide6.QtWebEngineWidgets',
    'PySide6.QtWebChannel',
    'PySide6.Qt3DCore',
    'PySide6.Qt3DRender',
    'PySide6.Qt3DExtras',
    'PySide6.QtBluetooth',
    'PySide6.QtMultimedia',
    'PySide6.QtMultimediaWidgets',
    'PySide6.QtQml',
    'PySide6.QtQuick',
    'PySide6.QtQuickWidgets',
    'PySide6.QtPdf',
    'PySide6.QtPdfWidgets',
    'PySide6.QtCharts',
    'PySide6.QtDataVisualization',
    'PySide6.QtDesigner',
    'PySide6.QtHelp',
    'PySide6.QtLocation',
    'PySide6.QtNfc',
    'PySide6.QtOpenGL',
    'PySide6.QtOpenGLWidgets',
    'PySide6.QtPositioning',
    'PySide6.QtPrintSupport',
    'PySide6.QtRemoteObjects',
    'PySide6.QtScxml',
    'PySide6.QtSensors',
    'PySide6.QtSerialPort',
    'PySide6.QtSql',
    'PySide6.QtTest',
    'PySide6.QtTextToSpeech',
    'PySide6.QtWebSockets',
    'tkinter',
    'customtkinter',
    'tksheet',
]

a = Analysis(
    ['run_gui.py'],
    pathex=[root],
    binaries=bins,
    datas=datas,
    hiddenimports=['requests', 'urllib3'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='NVRStatus',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI 无控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=app_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='NVRStatus',
)

# macOS .app 包
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='NVRStatus.app',
        icon=app_icon,
        bundle_identifier='com.local.nvrstatus',
        info_plist={
            'NSHighResolutionCapable': True,
            'CFBundleShortVersionString': '2.0.0',
            'CFBundleName': 'NVRStatus',
            'CFBundleDisplayName': 'NVR 状态巡检',
        },
    )
