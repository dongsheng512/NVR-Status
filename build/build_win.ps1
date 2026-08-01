# 在 Windows 上构建 NVRStatus
# 用法: powershell -ExecutionPolicy Bypass -File build/build_win.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "==> 安装依赖"
uv sync
uv pip install pyinstaller customtkinter

# 可选: 将 ffmpeg.exe / ffprobe.exe 放到 bin\ 后重新打包即可捆绑
if (-not (Test-Path "bin\ffmpeg.exe")) {
    Write-Host "提示: 未找到 bin\ffmpeg.exe。深度抽检需自行放置 ffmpeg 官方 Windows 构建到 bin\"
}

Write-Host "==> PyInstaller"
uv run pyinstaller --noconfirm NVRStatus.spec

Write-Host "==> 完成: dist\NVRStatus\"
Get-ChildItem dist
