# NVR Status（cam-gui）

海康威视 NVR **状态巡检**与**音视频深度抽检**工具，提供 **GUI** 与 **CLI** 两种入口，支持 Windows / macOS 打包分发。

> 版本：`1.0.0` · 语言：Python ≥ 3.11

---

## 功能概览

| 能力 | 说明 |
|------|------|
| 状态巡检 | 在线、录像计划、音频、落盘、硬盘、健康汇总 |
| 深度抽检 | 可选 RTSP 短时抓流；ffmpeg 检测音视频；可保存片段 |
| 多配置档案 | 新建 / 另存 / 重命名 / 删除 / 导入 / 导出 |
| 多设备 | 每档案可维护多台 NVR（名称、IP、端口、账号、SSL） |
| 双入口 | GUI（同事友好）+ CLI（脚本/批量） |
| 安装包 | PyInstaller 打 Win / Mac 安装即用包（可捆绑 ffmpeg） |

---

## 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| 语言 / 运行时 | **Python 3.11+** | 主程序 |
| 包管理 | **[uv](https://github.com/astral-sh/uv)** + `pyproject.toml` / `uv.lock` | 依赖与可复现环境 |
| GUI | **CustomTkinter** + **tkinter / ttk** | 主界面；通道表用 `ttk.Treeview` |
| 表格相关 | **tksheet**（依赖中保留） | 可选/扩展表格能力 |
| HTTP / 设备协议 | **requests** + 海康 **ISAPI**（Digest 认证） | 设备信息、通道、录像、存储等 |
| 流媒体抽检 | **ffmpeg / ffprobe**（RTSP） | 深度音视频抽检、样片保存 |
| CLI 输出 | **rich** | 终端彩色报告 |
| 打包 | **PyInstaller** | `NVRStatus.app` / `NVRStatus.exe` |
| 配置存储 | 本机用户目录 JSON 档案 | macOS `~/Library/Application Support/NVRStatus/` · Windows `%APPDATA%\NVRStatus\` |

### 架构要点

```
┌─────────────────┐     ┌──────────────────┐
│  gui_app.py     │     │  nvr / CLI       │
│  (CustomTkinter)│     │  cli_report.py   │
└────────┬────────┘     └────────┬─────────┘
         │                       │
         ▼                       ▼
┌─────────────────────────────────────────┐
│  hikvision_status.py  (HikvisionNVR)    │
│  ISAPI 查询 · 录像检查 · 深度 AV 抽检   │
└─────────────────────────────────────────┘
         │
         ▼
┌──────────────────┐
│  config_store.py │  多档案配置读写
└──────────────────┘
```

- GUI 用后台线程 `ScanWorker` + 消息队列更新进度，避免卡界面  
- 业务与 UI 分离，CLI / GUI 共用同一套巡检逻辑  

---

## 快速开始

### 开发运行（GUI）

```bash
uv sync
uv run python run_gui.py
# 或
uv run nvr-gui
```

### CLI

```bash
# 复制并编辑设备配置（勿把真实密码提交进 Git）
cp nvr_config.example.json nvr_config.json

./nvr -h
./nvr          # 默认巡检配置中全部设备
./nvr 1        # 只查第 1 台
```

更完整的参数与说明见 **[USAGE.md](USAGE.md)**。

### 打包

见 **[PACKAGING.md](PACKAGING.md)**。

```bash
# macOS
./build/build_mac.sh

# Windows（在 Windows 上执行）
powershell -ExecutionPolicy Bypass -File build\build_win.ps1
```

深度抽检打包前可将 `ffmpeg` / `ffprobe` 放入 `bin/`（见 [bin/README.md](bin/README.md)）。二进制默认不纳入版本库。

---

## 仓库结构（精简）

```
cam-gui/
├── gui_app.py           # GUI 主程序
├── run_gui.py           # GUI 入口
├── hikvision_status.py  # 海康 ISAPI / 抽检核心
├── config_store.py      # 配置档案
├── cli_report.py        # CLI 报告
├── nvr                  # CLI 启动脚本
├── nvr_config.example.json
├── NVRStatus.spec       # PyInstaller 规格
├── build/               # 打包脚本
├── assets/              # 图标与 logo
├── pyproject.toml
├── USAGE.md
└── PACKAGING.md
```

---

## 安全说明

- **不要**将含真实密码的 `nvr_config.json` 提交到 Git（已在 `.gitignore` 中忽略）。  
- GUI 档案保存在本机用户目录，不在安装包内写死密码。  
- 内网工具：请仅在可信网络环境使用。

---

## 文档

| 文档 | 内容 |
|------|------|
| [USAGE.md](USAGE.md) | GUI / CLI 使用说明 |
| [PACKAGING.md](PACKAGING.md) | Windows / macOS 打包与分发 |
| [bin/README.md](bin/README.md) | 捆绑 ffmpeg 说明 |

---

## License

Private / 内部使用（按仓库可见性为准）。
