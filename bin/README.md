# 捆绑 ffmpeg（安装即用深度抽检）

将对应平台的二进制放到此目录后重新打包。完整包会把此目录下非 `.md` 文件打进安装包。

## macOS

```bash
# 可用系统已安装的 ffmpeg（体积通常较大）
cp "$(which ffmpeg)" bin/ffmpeg
cp "$(which ffprobe)" bin/ffprobe
chmod +x bin/ffmpeg bin/ffprobe
```

或从 https://evermeet.cx/ffmpeg/ 下载静态构建。

**体积提示：** evermeet / brew 全量构建常各约 **40–50 MB**，两项合计可占安装包一半。  
业务主要用 RTSP 拉流、`-c copy` 写 mkv、`ffprobe` 与 `volumedetect`，可考虑：

- 使用更精简的静态构建（essentials，体积往往可降到约一半以内），或  
- 打 **lite 包**（`./build/build_mac.sh --lite`）不捆绑，依赖用户本机 `PATH`。

## Windows

1. 从 https://www.gyan.dev/ffmpeg/builds/ 下载  
   - 体积优先：`essentials` 构建  
   - 兼容优先：`release full`
2. 将 `ffmpeg.exe`、`ffprobe.exe` 复制到本目录 `bin\`

## 不打包时

```bash
./build/build_mac.sh --lite
# Windows: build\build_win.ps1 -Lite
```

运行时把 ffmpeg 放在可执行文件同级的 `bin/` 下，或加入系统 PATH。
