# 在 Windows 上构建 NVRStatus
# 用法:
#   powershell -ExecutionPolicy Bypass -File build/build_win.ps1
#   powershell -ExecutionPolicy Bypass -File build/build_win.ps1 -Lite
# 环境变量 NVR_LITE=1 / NVR_BUNDLE_FFMPEG=0 同样生效
param(
    [switch]$Lite
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if ($Lite -or $env:NVR_LITE -eq "1" -or $env:NVR_BUNDLE_FFMPEG -eq "0") {
    $env:NVR_LITE = "1"
    $env:NVR_BUNDLE_FFMPEG = "0"
    Write-Host "==> Lite 模式：不捆绑 ffmpeg/ffprobe"
} else {
    if (-not (Test-Path "bin\ffmpeg.exe")) {
        Write-Host "提示: 未找到 bin\ffmpeg.exe。深度抽检需自行放置 ffmpeg 到 bin\ 或安装后加入 PATH"
    } else {
        Write-Host "==> 将捆绑 bin\ffmpeg.exe / ffprobe.exe"
    }
}

Write-Host "==> 安装依赖"
uv sync
uv pip install "pyinstaller>=6.0.0"

Write-Host "==> PyInstaller"
uv run pyinstaller --noconfirm NVRStatus.spec

Write-Host "==> 体积"
if (Test-Path "dist\NVRStatus") {
    $size = (Get-ChildItem -Recurse "dist\NVRStatus" | Measure-Object -Property Length -Sum).Sum
    Write-Host ("dist\NVRStatus: {0:N1} MB" -f ($size / 1MB))
}

Write-Host "==> 完成: dist\NVRStatus\"
Get-ChildItem dist
