#!/usr/bin/env python3
"""生成应用图标：监控(CCTV)主题,矢量绘制 → PNG / icns / ico。

用法:
    uv run python scripts/make_icon.py [--preview]

产物:
    assets/AppIcon.iconset/*.png   (iconutil 输入)
    assets/AppIcon.icns            (macOS)
    assets/AppIcon.ico             (Windows)
    assets/app_logo.png            (窗口图标, 512)
    /tmp/icon_preview.png          (--preview 时另存 256 预览)
"""

from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication

_app = QGuiApplication.instance() or QGuiApplication(sys.argv)

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
MASTER = 1024


def _rounded_rect(x, y, w, h, r) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, w, h), r, r)
    return path


def draw_background(p: QPainter, s: int) -> None:
    """macOS 风格圆角方形底板:深蓝渐变 + 顶部内高光。"""
    radius = s * 0.225
    margin = 0  # 全幅
    rect = QRectF(margin, margin, s - 2 * margin, s - 2 * margin)

    grad = QLinearGradient(0, 0, 0, s)
    grad.setColorAt(0.0, QColor("#31445F"))
    grad.setColorAt(0.55, QColor("#1E2E47"))
    grad.setColorAt(1.0, QColor("#101C30"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    p.drawPath(_rounded_rect(rect.x(), rect.y(), rect.width(), rect.height(), radius))

    # 顶部边缘内高光(细描边,增强立体感)
    p.setBrush(Qt.BrushStyle.NoBrush)
    pen = QPen(QColor(255, 255, 255, 26), s * 0.006)
    p.setPen(pen)
    p.drawPath(_rounded_rect(rect.x() + s * 0.004, rect.y() + s * 0.004,
                             rect.width() - s * 0.008, rect.height() - s * 0.008,
                             radius * 0.96))


def draw_signal_arcs(p: QPainter, s: int) -> None:
    """从镜头发出的三道信号弧(暗示"监视/传输中"),全部落在画布内。"""
    cx, cy = s * 0.675, s * 0.545
    for r, alpha in ((0.15, 105), (0.225, 62), (0.30, 30)):
        pen = QPen(QColor(56, 189, 248, alpha), s * 0.028)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        rect = QRectF(cx - r * s, cy - r * s, 2 * r * s, 2 * r * s)
        p.drawArc(rect, -80 * 16, 72 * 16)  # 右下象限


def draw_camera(p: QPainter, s: int) -> None:
    """CCTV 枪机(侧视,镜头朝右下),白机身 + 深色镜头 + 绿色状态灯。"""
    p.save()
    p.translate(s * 0.48, s * 0.55)
    p.rotate(14)

    u = s / 1024.0  # 设计基准单位

    # ---- 支架:机身后上方的 L 形臂 + 顶板(不触边) ----
    silver = QLinearGradient(0, -430 * u, 0, 0)
    silver.setColorAt(0.0, QColor("#DDE6F0"))
    silver.setColorAt(1.0, QColor("#93A5BC"))
    p.setPen(QPen(QColor("#5C6E88"), 3 * u))
    p.setBrush(QBrush(silver))
    p.drawPath(_rounded_rect(-250 * u, -380 * u, 92 * u, 280 * u, 34 * u))
    p.drawPath(_rounded_rect(-360 * u, -440 * u, 320 * u, 72 * u, 32 * u))

    # ---- 机身(圆角长方体) ----
    body_grad = QLinearGradient(0, -150 * u, 0, 150 * u)
    body_grad.setColorAt(0.0, QColor("#FFFFFF"))
    body_grad.setColorAt(0.55, QColor("#E8EEF5"))
    body_grad.setColorAt(1.0, QColor("#B7C4D4"))
    p.setPen(QPen(QColor("#7486A0"), 4 * u))
    p.setBrush(QBrush(body_grad))
    p.drawPath(_rounded_rect(-320 * u, -130 * u, 460 * u, 260 * u, 80 * u))

    # 机身中段装饰凹槽
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor("#9FB0C4")))
    p.drawPath(_rounded_rect(-226 * u, -130 * u, 38 * u, 260 * u, 19 * u))

    # ---- 遮阳罩(机身上方前段) ----
    shade = QLinearGradient(0, -206 * u, 0, -120 * u)
    shade.setColorAt(0.0, QColor("#3D4F6B"))
    shade.setColorAt(1.0, QColor("#22334C"))
    p.setPen(QPen(QColor("#1B2A40"), 3 * u))
    p.setBrush(QBrush(shade))
    shield = QPainterPath()
    shield.moveTo(-90 * u, -124 * u)
    shield.lineTo(-78 * u, -190 * u)
    shield.quadTo(-72 * u, -204 * u, -54 * u, -204 * u)
    shield.lineTo(140 * u, -204 * u)
    shield.quadTo(162 * u, -204 * u, 158 * u, -182 * u)
    shield.lineTo(148 * u, -124 * u)
    shield.closeSubpath()
    p.drawPath(shield)

    # ---- 镜头筒 ----
    barrel = QLinearGradient(0, -120 * u, 0, 120 * u)
    barrel.setColorAt(0.0, QColor("#C6D2E0"))
    barrel.setColorAt(1.0, QColor("#8194AC"))
    p.setPen(QPen(QColor("#5C6E88"), 4 * u))
    p.setBrush(QBrush(barrel))
    p.drawPath(_rounded_rect(92 * u, -122 * u, 180 * u, 244 * u, 66 * u))

    # ---- 镜头 ----
    p.setPen(QPen(QColor("#33445E"), 6 * u))
    p.setBrush(QBrush(QColor("#16283F")))
    p.drawEllipse(QPointF(196 * u, 0), 112 * u, 112 * u)
    glass = QRadialGradient(QPointF(178 * u, -30 * u), 130 * u)
    glass.setColorAt(0.0, QColor("#2C4A6E"))
    glass.setColorAt(0.55, QColor("#0D1B2C"))
    glass.setColorAt(1.0, QColor("#050B14"))
    p.setBrush(QBrush(glass))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(196 * u, 0), 90 * u, 90 * u)
    # 玻璃高光
    p.setPen(QPen(QColor(125, 211, 252, 190), 10 * u))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(196 * u - 60 * u, -60 * u, 120 * u, 120 * u), 40 * 16, 80 * 16)
    p.setPen(QPen(QColor(125, 211, 252, 90), 6 * u))
    p.drawArc(QRectF(196 * u - 42 * u, -42 * u, 84 * u, 84 * u), 55 * 16, 50 * 16)

    # ---- 状态灯(绿色 LED + 光晕) ----
    led_c = QPointF(-266 * u, -76 * u)
    glow = QRadialGradient(led_c, 50 * u)
    glow.setColorAt(0.0, QColor(74, 222, 128, 150))
    glow.setColorAt(1.0, QColor(74, 222, 128, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(glow))
    p.drawEllipse(led_c, 50 * u, 50 * u)
    p.setBrush(QBrush(QColor("#3FDD7E")))
    p.setPen(QPen(QColor("#0E5A33"), 3 * u))
    p.drawEllipse(led_c, 18 * u, 18 * u)

    p.restore()


def render(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    draw_background(p, size)
    draw_signal_arcs(p, size)
    draw_camera(p, size)
    p.end()
    return pm


ICONSET_SIZES = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]


def write_pngs(pm_master: QPixmap) -> None:
    iconset = os.path.join(ASSETS, "AppIcon.iconset")
    os.makedirs(iconset, exist_ok=True)
    for name, px in ICONSET_SIZES:
        out = os.path.join(iconset, name)
        pm_master.scaled(px, px, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation).save(out, "PNG")
    # 窗口 logo
    logo = os.path.join(ASSETS, "app_logo.png")
    pm_master.scaled(512, 512, Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.SmoothTransformation).save(logo, "PNG")


def build_icns() -> None:
    iconset = os.path.join(ASSETS, "AppIcon.iconset")
    icns = os.path.join(ASSETS, "AppIcon.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns], check=True)


def _write_ico(path: str, sizes=(16, 24, 32, 48, 64, 128, 256)) -> None:
    """手工打包 ICO(条目内嵌 PNG,Vista+ 支持)。"""
    from PySide6.QtCore import QBuffer

    pngs = []
    for px in sizes:
        pm = render(px)
        buf = QBuffer()
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        pm.save(buf, "PNG")
        pngs.append((px, bytes(buf.data())))

    count = len(pngs)
    out = bytearray()
    out += struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    entries = bytearray()
    body = bytearray()
    for px, data in pngs:
        w = 0 if px >= 256 else px
        h = 0 if px >= 256 else px
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset)
        body += data
        offset += len(data)
    out += entries + body
    # 规范化路径并限制在目标目录内;mkstemp 临时文件 + 原子替换
    from pathlib import Path

    target = Path(path).expanduser().resolve()
    if ".." in target.parts:
        raise ValueError(f"非法输出路径: {path}")
    parent = str(target.parent)
    fd, tmp = tempfile.mkstemp(prefix=".appicon-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(out)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()

    master = render(MASTER)
    write_pngs(master)
    build_icns()
    try:
        _write_ico(os.path.join(ASSETS, "AppIcon.ico"))
    except Exception as e:
        print(f"[warn] ico 生成失败(仅影响 Windows 打包): {e}")

    if args.preview:
        master.scaled(256, 256).save("/tmp/icon_preview.png", "PNG")
    print("icon ok: AppIcon.icns / AppIcon.ico / app_logo.png / AppIcon.iconset/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
