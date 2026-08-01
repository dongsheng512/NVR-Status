# 捆绑 ffmpeg（安装即用深度抽检）

将对应平台的二进制放到此目录后重新打包：

## macOS

```bash
cp $(which ffmpeg) bin/ffmpeg
cp $(which ffprobe) bin/ffprobe
chmod +x bin/ffmpeg bin/ffprobe
```

或从 https://evermeet.cx/ffmpeg/ 下载。

## Windows

1. 从 https://www.gyan.dev/ffmpeg/builds/ 下载 release full
2. 将 `ffmpeg.exe`、`ffprobe.exe` 复制到本目录 `bin\`

## 不打包时

把上述文件放在可执行文件同级的 `bin/` 下，或加入系统 PATH。
