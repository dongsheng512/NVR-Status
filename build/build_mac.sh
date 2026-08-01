#!/usr/bin/env bash
# 在 macOS 上构建 NVRStatus.app
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> 安装依赖"
uv sync
uv pip install pyinstaller customtkinter

# 可选: 复制系统 ffmpeg 到 bin/ 以便捆绑
if [[ ! -f bin/ffmpeg ]] && command -v ffmpeg >/dev/null; then
  mkdir -p bin
  echo "==> 复制 ffmpeg/ffprobe 到 bin/"
  cp "$(command -v ffmpeg)" bin/ffmpeg
  cp "$(command -v ffprobe)" bin/ffprobe
  chmod +x bin/ffmpeg bin/ffprobe
fi

echo "==> PyInstaller"
uv run pyinstaller --noconfirm NVRStatus.spec

echo "==> 完成: dist/NVRStatus.app 或 dist/NVRStatus/"
ls -la dist/ || true
echo "首次打开若被拦截: 系统设置 → 隐私与安全性 → 仍要打开"
echo "或: xattr -cr dist/NVRStatus.app"
