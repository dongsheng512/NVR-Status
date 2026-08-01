# 部署与分发文档

> 适用版本：1.0.0  
> 目标：同事 **安装即用**（状态巡检 + 可选深度抽检 + 多配置档案）

相关：打包脚本速查 [PACKAGING.md](../PACKAGING.md) · 使用说明 [USAGE.md](../USAGE.md) · ffmpeg [bin/README.md](../bin/README.md)

---

## 1. 部署形态一览

| 形态 | 适用对象 | 入口 | 依赖 |
|------|----------|------|------|
| **开发运行** | 开发者 | `uv run python run_gui.py` / `./nvr` | Python 3.11+、uv、可选系统 ffmpeg |
| **macOS 应用包** | 同事（Mac） | `NVRStatus.app` | 无 Python；深度抽检建议包内捆绑 ffmpeg |
| **Windows 目录包** | 同事（Win） | `NVRStatus\NVRStatus.exe` | 同上 |
| **CLI 脚本** | 运维 / 自动化 | `./nvr` | 开发环境或已配置的 Python |

> **必须在目标 OS 上打包。** macOS 不能生成原生 Windows exe，反之亦然。

---

## 2. 运行时与数据路径

### 2.1 用户数据（配置与样片）

应用**不**把密码写进安装包。档案与默认抽检输出在用户目录：

| 系统 | 配置 / 档案 | 默认抽检目录 |
|------|-------------|--------------|
| macOS | `~/Library/Application Support/NVRStatus/profiles.json` | `…/NVRStatus/av_samples/` |
| Windows | `%APPDATA%\NVRStatus\profiles.json` | `%APPDATA%\NVRStatus\av_samples\` |
| Linux（若跑源码） | `~/.config/NVRStatus/` | 同目录下 `av_samples/` |

首次启动：若工作目录存在 `nvr_config.json`，可能导入为「默认」档案（见 `config_store` 逻辑）。

### 2.2 源码侧配置（CLI / 开发）

```bash
cp nvr_config.example.json nvr_config.json
# 编辑 IP / 账号 / 密码 —— 勿提交 Git（已在 .gitignore）
```

### 2.3 打包后资源

| 资源 | 位置说明 |
|------|----------|
| 应用图标 | `assets/` → 打入包内 |
| 捆绑 ffmpeg | 构建时 `bin/ffmpeg`（及 ffprobe）→ 运行时从 `_MEIPASS` 或可执行文件旁 `bin/` 查找 |
| Tcl/Tk / CustomTkinter | 由 PyInstaller `collect_all` 打入 |

---

## 3. 开发环境部署

### 3.1 前置条件

- Python **≥ 3.11**（仓库 `.python-version` 为 `3.11`）
- [uv](https://github.com/astral-sh/uv)
- 深度抽检：系统 PATH 或项目 `bin/` 中有 `ffmpeg` / `ffprobe`

### 3.2 安装与启动

```bash
cd cam-gui   # 或本仓库根目录
uv sync

# GUI
uv run python run_gui.py
# 或
uv run nvr-gui

# CLI
./nvr -h
./nvr          # 巡检配置中全部设备
./nvr 1        # 仅第 1 台
```

### 3.3 可选构建依赖

```bash
uv sync --extra build
# 或
uv pip install pyinstaller
```

---

## 4. 生产构建（安装包）

### 4.1 构建前检查清单

- [ ] `uv sync` 成功，GUI 开发模式可启动  
- [ ] （推荐）`bin/` 已放入对应平台的 ffmpeg / ffprobe  
- [ ] 版本号与 `gui_app.APP_VERSION` / 文档一致  
- [ ] 未把含真实密码的配置打进仓库或安装包  
- [ ] 在 **目标平台** 的干净目录执行构建  

### 4.2 macOS

```bash
./build/build_mac.sh
```

脚本行为摘要：

1. `uv sync` + 安装 PyInstaller / customtkinter  
2. 若无 `bin/ffmpeg` 且系统有 ffmpeg，则复制到 `bin/`  
3. `uv run pyinstaller --noconfirm NVRStatus.spec`  

**产物：**

- 优先：`dist/NVRStatus.app`
- 或：`dist/NVRStatus/` 目录

**分发给同事：**

1. 打 zip：`NVRStatus-macOS-1.0.0.zip`  
2. 解压后拖到「应用程序」  
3. 若无法打开（未签名）：

```bash
xattr -cr /Applications/NVRStatus.app
```

或在「系统设置 → 隐私与安全性」中允许。  
正式对外分发需 Apple 开发者 **签名 + 公证**（本仓库默认不做）。

### 4.3 Windows

在 **Windows 机器**上：

```powershell
powershell -ExecutionPolicy Bypass -File build\build_win.ps1
```

**产物：** `dist\NVRStatus\`（含 `NVRStatus.exe` 与 `_internal\`）

**分发：**

1. 整夹压缩为 `NVRStatus-Windows-1.0.0.zip`  
2. 用户解压后双击 `NVRStatus.exe`  
3. （可选）用 Inno Setup / NSIS 做成安装程序  

深度抽检：打包前将 `ffmpeg.exe`、`ffprobe.exe` 放入 `bin\`（见 [bin/README.md](../bin/README.md)）。

### 4.4 手动 PyInstaller

```bash
uv pip install pyinstaller customtkinter
uv run pyinstaller --noconfirm NVRStatus.spec
```

规格文件：`NVRStatus.spec`（入口 `run_gui.py`，`console=False`，macOS 含 BUNDLE）。

### 4.5 体积粗估

| 内容 | 约 |
|------|-----|
| GUI + Python 运行时 + CTk | 40–80 MB |
| + 捆绑 ffmpeg/ffprobe | 再 +50–120 MB |

---

## 5. 发布流程建议

```text
1. 更新版本号（代码 / README / 本文）
2. 真机回归验收清单（第 7 节）
3. 目标平台构建 + 捆绑 ffmpeg（若需要深度抽检）
4. 压缩产物，命名：NVRStatus-{macOS|Windows}-{version}.zip
5. 附简短「首次打开说明」（未签名 Mac / 解压路径）
6. 内网盘或私有仓库 Release 分发（本仓库 GitHub 为 Private）
7. 记录构建机 OS 版本、Python 版本、是否含 ffmpeg
```

### 5.1 建议附带的用户说明（可复制）

**macOS**

1. 解压 zip，将 `NVRStatus` 拖到「应用程序」。  
2. 若提示来自未验证开发者：执行 `xattr -cr /Applications/NVRStatus.app` 或在隐私设置中允许。  
3. 首次在 GUI 中添加 NVR 或导入配置档案。  
4. 深度抽检需包内或系统已有 ffmpeg（完整包已捆绑则无需安装）。  

**Windows**

1. 解压到任意目录（避免仅从压缩包内直接运行导致写权限问题）。  
2. 双击 `NVRStatus.exe`。  
3. 若 SmartScreen 拦截：更多信息 → 仍要运行（内网自签/未签名常见）。  

---

## 6. 网络与安全

| 项 | 说明 |
|----|------|
| 访问范围 | 仅访问用户配置的 NVR IP/端口（ISAPI HTTP(S)、深度抽检时 RTSP） |
| 凭证 | 存本机用户目录 JSON；**勿**提交 `nvr_config.json` / `profiles.json` 到公开仓库 |
| 示例配置 | 仓库仅含 `nvr_config.example.json` |
| 权限 | 内网工具；确保客户端能路由到 NVR 网段 |
| 防火墙 | 放行到 NVR 的 80/443（及设备 RTSP 端口，常见 554） |

---

## 7. 部署验收清单

### 7.1 功能

- [ ] 无 ffmpeg：可完成状态 / 落盘巡检  
- [ ] 有 ffmpeg：深度抽检可用，OSD 时间优先本地繁忙时段  
- [ ] 可新建第二套档案、切换、互不覆盖  
- [ ] 可增删设备并保存  
- [ ] 导出 CSV / 文本结果  
- [ ] CLI `./nvr` 与 GUI 结果大方向一致（同设备同参数）  

### 7.2 安装包

- [ ] macOS：双击启动，窗口图标正常  
- [ ] Windows：双击 exe 启动，无多余控制台窗口  
- [ ] 冷启动可接受（建议记录首启耗时）  
- [ ] 捆绑 ffmpeg 时，GUI 显示工具可用  
- [ ] 杀软 / Gatekeeper 问题有文档说明  

### 7.3 回归设备

建议固定 1～2 台测试 NVR（不同固件更佳），记录：

- 通道数、是否启用音频  
- 快速巡检耗时、深度抽检抽样数  

---

## 8. 常见问题排障

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| Mac 打不开 | 隔离属性 / 未签名 | `xattr -cr`；隐私设置允许 |
| 无法获取设备信息 | IP/账号/网络/SSL | 浏览器试 ISAPI；检查端口与 Digest 密码 |
| 深度抽检跳过 | 无 ffmpeg | 安装或捆绑到 `bin/` |
| 进度卡住 | 网络慢 / 通道多 / RTSP 超时 | 减小 `av_limit`、workers；看日志阶段 |
| 配置丢失 | 清了用户目录或换了账号 | 从导出档案恢复 |
| 打包后无界面 / 闪退 | 缺资源或 Tcl | 查 `warn-*.txt`；用 `console=True` 临时诊断 |
| Win 杀软误报 | PyInstaller 常见 | 加白名单；正式可代码签名 |

开发机调试打包应用时，可临时将 `NVRStatus.spec` 中 `console=False` 改为 `True` 查看报错（勿用于正式分发）。

---

## 9. CI 展望（可选，未默认启用）

双平台矩阵示意：

```text
GitHub Actions
  matrix: [macos-latest, windows-latest]
  steps: uv sync → 可选缓存 ffmpeg → pyinstaller → upload artifact
```

注意：

- 密钥与真实 NVR 不要进 CI  
- ffmpeg 二进制用缓存或 Release 附件，勿提交进 Git  
- Private 仓库 Artifacts 注意保留策略  

---

## 10. 与源码入口对照

| 入口文件 | 用途 |
|----------|------|
| `run_gui.py` / `gui_app.py` | GUI（打包入口） |
| `nvr` / `cli_report.py` | CLI |
| `hikvision_status.py` | ISAPI + 抽检核心 |
| `config_store.py` | 多档案配置 |
| `NVRStatus.spec` | PyInstaller 规格 |
| `build/build_mac.sh` | macOS 构建 |
| `build/build_win.ps1` | Windows 构建 |

---

## 11. 变更记录（文档）

| 日期 | 说明 |
|------|------|
| 2026-08 | 初版：从 PACKAGING/USAGE 整理为完整部署文档 |
