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
    # 优先 Cellar 真实二进制（PATH 里常为同样路径）
    src_ff="$(command -v ffmpeg)"
    src_fp="$(command -v ffprobe)"
    if command -v brew >/dev/null 2>&1; then
      pref="$(brew --prefix ffmpeg 2>/dev/null || true)"
      if [[ -n "$pref" && -x "$pref/bin/ffmpeg" ]]; then
        src_ff="$pref/bin/ffmpeg"
        src_fp="$pref/bin/ffprobe"
      fi
    fi
    cp "$src_ff" bin/ffmpeg
    cp "$src_fp" bin/ffprobe
    chmod +x bin/ffmpeg bin/ffprobe
  fi
  # Homebrew 动态链接：用 dylibbundler 把依赖打进 bin/libs，保证可分发
  if [[ -f bin/ffmpeg ]] && command -v dylibbundler >/dev/null 2>&1; then
    if otool -L bin/ffmpeg 2>/dev/null | grep -qE 'homebrew|@rpath/libav'; then
      echo "==> dylibbundler: 将 ffmpeg 动态库打入 bin/libs/"
      /bin/rm -rf bin/libs
      mkdir -p bin/libs
      dylibbundler -od -b -x bin/ffmpeg -d bin/libs -p @executable_path/libs/
      dylibbundler -od -b -x bin/ffprobe -d bin/libs -p @executable_path/libs/
    fi
  elif [[ -f bin/ffmpeg ]] && otool -L bin/ffmpeg 2>/dev/null | grep -q homebrew; then
    echo "==> 警告: bin/ffmpeg 依赖 Homebrew 动态库，但未安装 dylibbundler"
    echo "    完整包在无 Homebrew 的机器上深度抽检可能失败。建议: brew install dylibbundler"
  fi
  if [[ -f bin/ffmpeg ]]; then
    echo "==> 将捆绑 ffmpeg: $(du -sh bin 2>/dev/null | awk '{print $1}') (含 libs)"
    du -h bin/ffmpeg bin/ffprobe 2>/dev/null | awk '{print "   ",$1,$2}'
  else
    echo "==> 未找到 bin/ffmpeg，完整包将不含深度抽检二进制（等同 lite）"
  fi
else
  echo "==> Lite 模式：不捆绑 ffmpeg/ffprobe"
fi

echo "==> PyInstaller"
uv run pyinstaller --noconfirm NVRStatus.spec

# PyInstaller 会把 @executable_path 改写成 @rpath，破坏 dylibbundler 结果。
# 打包后把已修好的 bin/ffmpeg+ffprobe+libs 覆盖回产物内。
_restore_portable_ffmpeg() {
  local dest_bin="$1"
  [[ -f bin/ffmpeg && -d bin/libs ]] || return 0
  mkdir -p "$dest_bin"
  echo "==> 恢复可移植 ffmpeg → $dest_bin"
  /bin/cp -f bin/ffmpeg bin/ffprobe "$dest_bin/"
  /bin/rm -rf "$dest_bin/libs"
  /bin/cp -R bin/libs "$dest_bin/"
  chmod +x "$dest_bin/ffmpeg" "$dest_bin/ffprobe" 2>/dev/null || true
  if command -v codesign >/dev/null 2>&1; then
    codesign --force --sign - "$dest_bin/ffmpeg" "$dest_bin/ffprobe" 2>/dev/null || true
  fi
  # 冒烟：在产物目录内执行 -version
  if ! (cd "$dest_bin" && ./ffmpeg -version >/dev/null 2>&1); then
    echo "==> 警告: 产物内 ffmpeg 无法运行（检查 libs）"
  else
    echo "==> 产物内 ffmpeg OK: $(cd "$dest_bin" && ./ffmpeg -version 2>&1 | head -1)"
  fi
}

if [[ -d dist/NVRStatus.app/Contents/Frameworks/bin ]]; then
  _restore_portable_ffmpeg "dist/NVRStatus.app/Contents/Frameworks/bin"
elif [[ -d dist/NVRStatus/_internal/bin ]]; then
  _restore_portable_ffmpeg "dist/NVRStatus/_internal/bin"
elif [[ -d dist/NVRStatus/bin ]]; then
  _restore_portable_ffmpeg "dist/NVRStatus/bin"
fi

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
