# 打包与分发（Windows / macOS）

目标：**安装即用**，含状态巡检、深度音视频抽检、多配置档案、手动设备管理。

> 更完整的部署说明（发布流程、数据路径、验收清单、排障）见 **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**。  
> **PySide6 GUI 重写计划**见 **[docs/PLAN.md](docs/PLAN.md)**（v2.0.0 已重写为 PySide6）。

---

## 1. 运行 GUI（开发模式）

```bash
uv sync
uv run python run_gui.py
# 或: uv run nvr-gui
```

配置档案保存在：

| 系统 | 路径 |
|------|------|
| macOS | `~/Library/Application Support/NVRStatus/profiles.json` |
| Windows | `%APPDATA%\NVRStatus\profiles.json` |
| 抽检片段默认 | 同上目录下 `av_samples/` |

首次启动若项目里有 `nvr_config.json`，会自动导入为「默认」档案。

---

## 2. 捆绑 ffmpeg（深度抽检）

打包前把二进制放进 `bin/`（见 [bin/README.md](bin/README.md)）：

- macOS: `bin/ffmpeg` + `bin/ffprobe`
- Windows: `bin/ffmpeg.exe` + `bin/ffprobe.exe`

不捆绑也能跑 GUI（**lite 包**）；深度抽检会提示缺少工具，或使用系统 PATH 中的 ffmpeg。

---

## 3. 构建安装包

### 3.1 macOS

```bash
# 完整包（有 bin/ffmpeg 则捆绑）
./build/build_mac.sh

# 精简包（不捆绑 ffmpeg，体积更小）
./build/build_mac.sh --lite
```

产物：

- `dist/NVRStatus/` 或 `dist/NVRStatus.app`
- zip：`dist/NVRStatus-macOS-<arch>.zip` 或 `…-lite.zip`

分发：

1. 将 zip 发给用户  
2. 解压后拖到「应用程序」  
3. 若提示无法打开：`xattr -cr /Applications/NVRStatus.app`，或在「隐私与安全性」中允许  

正式对外分发需 Apple 开发者签名 + 公证（本仓库默认不签名）。

### 3.2 Windows

在 **Windows 机器**上：

```powershell
# 完整包
powershell -ExecutionPolicy Bypass -File build\build_win.ps1

# 精简包
powershell -ExecutionPolicy Bypass -File build\build_win.ps1 -Lite
```

产物：`dist\NVRStatus\`（内含 `NVRStatus.exe`）

可整夹打成 zip，或用 Inno Setup / NSIS 做成安装程序（可选）。

> **注意：必须在目标系统上打包。** Mac 打不出原生 Windows exe，反之亦然。可用两台机器或 GitHub Actions 双矩阵 CI。

### 3.3 手动 PyInstaller / 环境变量

```bash
uv pip install "pyinstaller>=6.0.0"
uv run pyinstaller --noconfirm NVRStatus.spec

# 不捆绑 ffmpeg:
NVR_LITE=1 uv run pyinstaller --noconfirm NVRStatus.spec
# 或:
NVR_BUNDLE_FFMPEG=0 uv run pyinstaller --noconfirm NVRStatus.spec
```

---

## 4. 体积优化说明

`NVRStatus.spec` 在 Analysis 之后会**过滤未使用的 Qt 原生库与插件**（仅 `excludes` 挡不住 framework/plugin）：

| 剔除 | 说明 |
|------|------|
| QtPdf / QtQml / QtQuick / QtVirtualKeyboard / QtOpenGL 等 | 业务仅用 Widgets |
| Qt translations（全部 .qm） | 界面文案中文硬编码 |
| 多余 imageformats | 保留 gif / ico / jpeg / **svg**（QSS 箭头） |
| minimal / offscreen 平台插件 | 正式 GUI 只需 cocoa / windows |
| UPX | macOS arm64 默认关闭 |

### 实测（macOS arm64，2026-08-05，本机构建）

| 包型 | 磁盘 | zip | 内容 |
|------|------|-----|------|
| 优化前（基线） | **204 MB** | **86 MB** | 未裁 Qt 插件/翻译 + evermeet ffmpeg×2 |
| **full**（`./build/build_mac.sh`） | **172 MB** | **72 MB** | Qt 裁剪 + 剔 rich/CLI + 捆绑 ffmpeg |
| **lite**（`./build/build_mac.sh --lite`） | **74 MB** | **29 MB** | 同上但不捆绑 ffmpeg |

相对基线：**full 约 −16% 磁盘 / −16% zip**；**lite 约 −64% 磁盘 / −66% zip**。  
lite 包已做冷启动冒烟（进程可起）。

> 最大剩余体积来自 **ffmpeg 全量静态构建（各 ~49 MB）**。若需再压 full 包，请改用 essentials / 精简构建替换 `bin/ffmpeg`（见 bin/README）。

---

## 5. 软件功能对照

| 功能 | 说明 |
|------|------|
| 多配置档案 | 新建 / 另存 / 重命名 / 删除 / 导入 / 导出 |
| 手动设备 | 每档案多台 NVR：名称、IP、端口、用户、密码、SSL |
| 日常巡检 | 在线、计划、含音频、落盘、硬盘、健康汇总 |
| 深度抽检 | 短时 RTSP，优先 10:00–18:00，可选保存 mkv |
| 参数记忆 | 扫描选项随档案保存 |

CLI 仍可用：`./nvr 1`，见 [USAGE.md](USAGE.md)。

---

## 6. 安全说明

- 配置与密码存在**本机用户目录**，不写死在安装包内  
- macOS / Windows 优先使用系统 keyring；失败时回退 JSON 明文  
- 请勿把含密码的 `profiles.json` 提交到公开仓库  
- 应用仅访问你配置的 NVR 地址，需网络可达  

---

## 7. 验收清单

- [ ] 无 ffmpeg 时可完成状态/落盘巡检  
- [ ] 有 ffmpeg 时可深度抽检且 OSD 时间为本地繁忙时段  
- [ ] lite 包启动正常、主题与通道表正常（含下拉箭头 SVG）  
- [ ] 可新建第二套档案、切换、互不覆盖  
- [ ] 可增删设备并保存  
- [ ] Win / Mac 各自构建出可双击启动的应用  
- [ ] 记录 full / lite 的 `du -sh` 与 zip 大小  
