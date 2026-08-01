# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 配置: 在对应平台上执行
#   uv run pyinstaller NVRStatus.spec
#
# 可选: 将 ffmpeg/ffprobe 放入 bin/ 后一并打包(安装即用深度抽检)
# 图标: assets/AppIcon.icns (macOS) / assets/AppIcon.ico (Windows)

import os
import sys

from PyInstaller.utils.hooks import collect_all

block_cipher = None
root = os.path.abspath('.')

bins = []
bin_dir = os.path.join(root, 'bin')
if os.path.isdir(bin_dir):
    for name in os.listdir(bin_dir):
        p = os.path.join(bin_dir, name)
        if os.path.isfile(p) and not name.endswith('.md'):
            bins.append((p, 'bin'))

# customtkinter / tksheet 资源
ctk_datas, ctk_binaries, ctk_hidden = collect_all('customtkinter')
try:
    ts_datas, ts_binaries, ts_hidden = collect_all('tksheet')
except Exception:
    ts_datas, ts_binaries, ts_hidden = [], [], []

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

extra_datas = list(ctk_datas) + list(ts_datas)
if os.path.isfile(icon_png):
    extra_datas.append((icon_png, 'assets'))

a = Analysis(
    ['run_gui.py'],
    pathex=[root],
    binaries=bins + ctk_binaries + ts_binaries,
    datas=extra_datas,
    hiddenimports=['customtkinter', 'tksheet', 'requests', 'urllib3']
    + list(ctk_hidden) + list(ts_hidden),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
            'CFBundleShortVersionString': '1.0.0',
            'CFBundleName': 'NVRStatus',
            'CFBundleDisplayName': 'NVR 状态巡检',
        },
    )
