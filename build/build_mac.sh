#!/usr/bin/env bash
# 在 macOS 上构建 NVRStatus.app
#
# 用法:
#   ./build/build_mac.sh              # 完整包（有 bin/ffmpeg 则捆绑）
#   ./build/build_mac.sh --lite       # 不捆绑 ffmpeg（体积最小）
#   NVR_LITE=1 ./build/build_mac.sh   # 同上
#
# 产物:
#   dist/NVRStatus/  或  dist/NVRStatus.app
#   dist/NVRStatus-macOS-arm64.zip  （默认）
#   dist/NVRStatus-macOS-arm64-lite.zip  （--lite）
set -euo pipefail
cd "$(dirname "$0")/.."

LITE=0
for arg in "$@"; do
  case "$arg" in
    --lite|-lite) LITE=1 ;;
    -h|--help)
      sed -n '2,14p' "$0"
      exit 0
      ;;
  esac
done

if [[ "$LITE" == "1" ]]; then
  export NVR_LITE=1
  export NVR_BUNDLE_FFMPEG=0
fi

echo "==> 安装依赖"
uv sync
uv pip install "pyinstaller>=6.0.0"

if [[ "${NVR_LITE:-}" != "1" && "${NVR_BUNDLE_FFMPEG:-1}" != "0" ]]; then
  # 可选: 复制系统 ffmpeg 到 bin/ 以便捆绑
  if [[ ! -f bin/ffmpeg ]] && command -v ffmpeg >/dev/null; then
    mkdir -p bin
    echo "==> 复制 ffmpeg/ffprobe 到 bin/"
    cp "$(command -v ffmpeg)" bin/ffmpeg
    cp "$(command -v ffprobe)" bin/ffprobe
    chmod +x bin/ffmpeg bin/ffprobe
  fi
  if [[ -f bin/ffmpeg ]]; then
    echo "==> 将捆绑 ffmpeg: $(du -h bin/ffmpeg bin/ffprobe 2>/dev/null | awk '{print $1,$2}')"
  else
    echo "==> 未找到 bin/ffmpeg，完整包将不含深度抽检二进制（等同 lite）"
  fi
else
  echo "==> Lite 模式：不捆绑 ffmpeg/ffprobe"
fi

echo "==> PyInstaller"
uv run pyinstaller --noconfirm NVRStatus.spec

# 体积报告
echo "==> 体积"
if [[ -d dist/NVRStatus.app ]]; then
  du -sh dist/NVRStatus.app
  TARGET=dist/NVRStatus.app
elif [[ -d dist/NVRStatus ]]; then
  du -sh dist/NVRStatus
  if [[ -d dist/NVRStatus/_internal ]]; then
    du -sh dist/NVRStatus/_internal/* 2>/dev/null | sort -hr | head -15
  fi
  TARGET=dist/NVRStatus
else
  echo "未找到产物 dist/NVRStatus*"
  ls -la dist/ || true
  exit 1
fi

ARCH=$(uname -m)
if [[ "$LITE" == "1" || "${NVR_LITE:-}" == "1" ]]; then
  ZIP_NAME="NVRStatus-macOS-${ARCH}-lite.zip"
else
  ZIP_NAME="NVRStatus-macOS-${ARCH}.zip"
fi

echo "==> 打包 zip: dist/${ZIP_NAME}"
rm -f "dist/${ZIP_NAME}"
(
  cd dist
  # zip 根目录为 NVRStatus 或 NVRStatus.app
  base=$(basename "$TARGET")
  ditto -c -k --sequesterRsrc --keepParent "$base" "$ZIP_NAME" 2>/dev/null \
    || zip -qry "$ZIP_NAME" "$base"
)
ls -lh "dist/${ZIP_NAME}"

echo "==> 完成"
echo "首次打开若被拦截: 系统设置 → 隐私与安全性 → 仍要打开"
echo "或: xattr -cr dist/NVRStatus.app  (若产物为 .app)"
