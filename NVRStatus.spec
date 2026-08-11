# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 配置 (PySide6): 在对应平台上执行
#   uv run pyinstaller --noconfirm NVRStatus.spec
#
# 环境变量:
#   NVR_LITE=1          不捆绑 ffmpeg/ffprobe（体积最小；深度抽检需系统 PATH）
#   NVR_BUNDLE_FFMPEG=0 同上
#
# 可选: 将 ffmpeg/ffprobe 放入 bin/ 后一并打包(安装即用深度抽检)
# 图标: assets/AppIcon.icns (macOS) / assets/AppIcon.ico (Windows)
#
# 体积优化: Analysis 后过滤未使用的 Qt 框架/插件/翻译（excludes 只挡 Python 模块）

import os
import sys

block_cipher = None
root = os.path.abspath('.')

# ---- 是否捆绑 ffmpeg ----
_lite = os.environ.get("NVR_LITE", "").strip().lower() in ("1", "true", "yes")
_no_ff = os.environ.get("NVR_BUNDLE_FFMPEG", "1").strip().lower() in ("0", "false", "no")
bundle_ffmpeg = not (_lite or _no_ff)

bins = []
bin_dir = os.path.join(root, 'bin')
if bundle_ffmpeg and os.path.isdir(bin_dir):
    # 递归收集 bin/ 下可执行文件与 dylibbundler 产出的 libs/
    # （Homebrew ffmpeg 动态链接，需一并打包 libs 才可离线运行）
    for dirpath, _dirnames, filenames in os.walk(bin_dir):
        for name in filenames:
            if name.endswith('.md'):
                continue
            p = os.path.join(dirpath, name)
            if not os.path.isfile(p):
                continue
            rel_dir = os.path.relpath(dirpath, bin_dir)
            dest = 'bin' if rel_dir in ('.', '') else os.path.join('bin', rel_dir)
            bins.append((p, dest))

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
datas = []
if os.path.isfile(icon_png):
    datas.append((icon_png, 'assets'))

# 精简无用 Qt 模块（Python 层）
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
    'PySide6.QtVirtualKeyboard',
    'tkinter',
    'customtkinter',
    'tksheet',
    'pytest',
    'unittest',
    # CLI 专用（GUI 入口不需要；rich 会连带 pygments 膨胀数 MB）
    'rich',
    'pygments',
    'cli_report',
    'markdown_it',
    'mdurl',
]


def _norm(path: str) -> str:
    return path.replace('\\', '/')


# 必须丢弃的路径片段（framework / plugin / 翻译）
# 业务仅用 Widgets + PNG logo + QSS 引用临时 SVG（需 QtSvg）
_DROP_SUBSTRINGS = (
    # 未使用框架（hook 会经插件拖入）
    'QtPdf',
    'QtQml',
    'QtQuick',
    'QtVirtualKeyboard',
    'QtOpenGL',
    'Qt3D',
    'QtMultimedia',
    'QtWebEngine',
    'QtCharts',
    'QtDataVisualization',
    'QtPositioning',
    'QtLocation',
    'QtSensors',
    'QtSerialPort',
    'QtSql',
    'QtTest',
    'QtBluetooth',
    'QtNfc',
    'QtRemoteObjects',
    'QtScxml',
    'QtTextToSpeech',
    'QtWebSockets',
    'QtWebChannel',
    'QtPrintSupport',
    'QtDesigner',
    'QtHelp',
    # 全量翻译（应用文案中文硬编码；匹配 PySide6/Qt/translations/...）
    'translations/',
    '/translations',
    # 多余 imageformats（保留 gif/ico/jpeg/svg）
    'libqpdf',
    'qpdf.dll',
    'libqmacheif',
    'qmacheif.dll',
    'libqmacjp2',
    'qmacjp2.dll',
    'libqtga',
    'qtga.dll',
    'libqtiff',
    'qtiff.dll',
    'libqwebp',
    'qwebp.dll',
    'libqwbmp',
    'qwbmp.dll',
    # 非目标平台 / 调试用平台插件
    'libqminimal',
    'qminimal.dll',
    'libqoffscreen',
    'qoffscreen.dll',
    'libqeglfs',
    'libqvnc',
    'libqlinuxfb',
    # 虚拟键盘输入法
    'libqtvirtualkeyboard',
    'qtvirtualkeyboard',
    'platforminputcontexts',
    # 杂项
    'Lorem ipsum',
)

# 平台相关：丢弃对方平台的平台插件（双保险）
if sys.platform == 'darwin':
    _DROP_SUBSTRINGS = _DROP_SUBSTRINGS + (
        'qwindows',
        'qwindowsvistastyle',
        'qdirect2d',
    )
elif sys.platform == 'win32':
    _DROP_SUBSTRINGS = _DROP_SUBSTRINGS + (
        'libqcocoa',
        'libqmacstyle',
        'qcocoa',
        'qmacstyle',
    )


def _should_drop(name: str) -> bool:
    """是否从包中剔除。保留 Core/Gui/Widgets/Network/DBus/Svg 与必要插件。"""
    n = _norm(name)
    for s in _DROP_SUBSTRINGS:
        if s in n:
            return True
    return False


def _filter_toc(toc):
    """过滤 (dest_name, src_path, typecode) 三元组列表。"""
    kept = []
    dropped = []
    for entry in toc:
        name = entry[0]
        if _should_drop(name):
            dropped.append(name)
        else:
            kept.append(entry)
    if dropped:
        # 构建日志里可见
        print(f'[NVRStatus.spec] dropped {len(dropped)} binaries/datas for size:')
        for d in sorted(set(_norm(x) for x in dropped))[:40]:
            print(f'  - {d}')
        if len(dropped) > 40:
            print(f'  ... and {len(dropped) - 40} more')
    return kept


a = Analysis(
    ['run_gui.py'],
    pathex=[root],
    binaries=bins,
    datas=datas,
    hiddenimports=['requests', 'urllib3'],
    hookspath=[],
    hooksconfig={
        # 尽量少收集翻译；真正剔除仍靠下方 filter
        'PySide6': {
            'module_collection_mode': 'pyz+py',
        },
    },
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# 关键：原生 Qt 框架/插件不吃 excludes，在此裁剪
a.binaries = _filter_toc(a.binaries)
a.datas = _filter_toc(a.datas)

# 去掉 Analysis 产生的空壳 Qt* 符号链接占位（若有）
# 以及 setuptools 测试文本等
a.datas = [
    d for d in a.datas
    if 'Lorem ipsum' not in _norm(d[0]) and 'setuptools/_vendor' not in _norm(d[0])
]

print(
    f'[NVRStatus.spec] bundle_ffmpeg={bundle_ffmpeg} '
    f'binaries={len(a.binaries)} datas={len(a.datas)}'
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
    upx=False,  # macOS arm64 / 签名场景下 UPX 收益差且易出问题
    console=False,
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
    upx=False,
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
