# 打包与分发（Windows / macOS）

目标：**安装即用**，含状态巡检、深度音视频抽检、多配置档案、手动设备管理。

> 更完整的部署说明（发布流程、数据路径、验收清单、排障）见 **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**。  
> 产品路线与 UI 迁移计划见 **[docs/PLAN.md](docs/PLAN.md)**。

---

## 1. 运行 GUI（开发模式）

```bash
cd cam
uv sync
uv run python run_gui.py
# 或
uv run nvr-gui
```

配置档案保存在：

| 系统 | 路径 |
|------|------|
| macOS | `~/Library/Application Support/NVRStatus/profiles.json` |
| Windows | `%APPDATA%\NVRStatus\profiles.json` |
| 抽检片段默认 | 同上目录下 `av_samples/` |

首次启动若项目里有 `nvr_config.json`，会自动导入为「默认」档案。

---

## 2. 捆绑 ffmpeg（深度抽检必用）

打包前把二进制放进 `bin/`：

见 [bin/README.md](bin/README.md)。

- macOS: `bin/ffmpeg` + `bin/ffprobe`
- Windows: `bin/ffmpeg.exe` + `bin/ffprobe.exe`

不捆绑也能跑 GUI，但勾选「深度抽检 / 保存片段」时会提示缺少工具。

---

## 3. 构建安装包

### 3.1 macOS

```bash
./build/build_mac.sh
```

产物：

- `dist/NVRStatus.app`（若 BUNDLE 成功）
- 或 `dist/NVRStatus/` 目录

分发：

1. 压缩为 `NVRStatus-macOS.zip` 发给用户  
2. 用户解压后拖到「应用程序」  
3. 若提示无法打开：`xattr -cr /Applications/NVRStatus.app`，或在「隐私与安全性」中允许  

正式对外分发需 Apple 开发者签名 + 公证（本仓库默认不签名）。

### 3.2 Windows

在 **Windows 机器**上：

```powershell
powershell -ExecutionPolicy Bypass -File build\build_win.ps1
```

产物：`dist\NVRStatus\`（内含 `NVRStatus.exe`）

可整夹打成 zip，或用 Inno Setup / NSIS 做成安装程序（可选）。

> **注意：必须在目标系统上打包。** Mac 打不出原生 Windows exe，反之亦然。可用两台机器或 GitHub Actions 双矩阵 CI。

### 3.3 手动 PyInstaller

```bash
uv pip install pyinstaller customtkinter
uv run pyinstaller --noconfirm NVRStatus.spec
```

---

## 4. 软件功能对照

| 功能 | 说明 |
|------|------|
| 多配置档案 | 新建 / 另存 / 重命名 / 删除 / 导入 / 导出 |
| 手动设备 | 每档案多台 NVR：名称、IP、端口、用户、密码、SSL |
| 日常巡检 | 在线、计划、含音频、落盘、硬盘、健康汇总 |
| 深度抽检 | 短时 RTSP，优先 10:00–18:00，可选保存 mkv |
| 参数记忆 | 扫描选项随档案保存 |

CLI 仍可用：`./nvr 1`，见 [USAGE.md](USAGE.md)。

---

## 5. 体积与依赖粗估

| 内容 | 约 |
|------|-----|
| 仅 GUI + requests | 40–80 MB |
| + 捆绑 ffmpeg | 再 +50–120 MB |

---

## 6. 安全说明

- 配置与密码存在**本机用户目录**，不写死在安装包内  
- 请勿把含密码的 `profiles.json` 提交到公开仓库  
- 应用仅访问你配置的 NVR 地址，需网络可达  

---

## 7. 验收清单

- [ ] 无 ffmpeg 时可完成状态/落盘巡检  
- [ ] 有 ffmpeg 时可深度抽检且 OSD 时间为本地繁忙时段  
- [ ] 可新建第二套档案、切换、互不覆盖  
- [ ] 可增删设备并保存  
- [ ] Win / Mac 各自构建出可双击启动的应用  
