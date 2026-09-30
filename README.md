# NVR Status（cam-gui）

海康威视 NVR **状态巡检**与**音视频深度抽检**工具，提供 **GUI** 与 **CLI** 两种入口，支持 Windows / macOS 打包分发。

> 版本：`2.3.0` · 语言：Python ≥ 3.11

---

## 2.3.0 更新

- **CLI 新增 `--av-channels`**：只抽检指定物理通道（如 `--av-channels 31,64`），排查单点问题不必等全量 64 路跑完；通道号写错直接报错，不静默丢弃
- **CLI 新增 `--av-at`**：定点抽检时刻（如 `--av-at 19:05,16:20`），每个时刻各查一遍，与 `--av-channels` 叠加即「单通道定点复查」——区分持久故障与间歇抖动（如 IPC 重启后音频是否恢复）；未来时刻直接报错，距现在不足 10 分钟自动前移并注明
- **GUI 新增「单路抽检」**：巡检完成后在通道列表选中一行（可多选），点「单路抽检」或右键菜单即可只对选中通道重跑音视频抽检并就地刷新，复查单路不必整机重新巡检
- **回放 RTSP 增加 `:554` 端口回退**：部分环境 `:80` 端口的 RTSP 被设备静默拒绝（TCP 通、`DESCRIBE` 即断），自动补一个同路径 `:554` 兜底候选，正常链路不受影响
- **判定更准**：通道检测状态（`chanDetectResult`）纳入摄像头在线判定并细分离线原因；「近期录像状态未知」「音频未确认」只有**全部**未确认才降级为警告，单路抖动不再把整机拉低
- **音频抽检更实**：音频改为独立短拉 `-map 0:a:0` + `volumedetect`，不再与视频捆在一次全流里；报告区分 正常 / 静音警告 / 无音轨 / 未确认

---

## 2.2.0 更新

- **档案条**：下拉切换当前档案；「新建」+「管理」菜单（另存 / 重命名 / 删除 / 导入 / 导出）
- **布局**：左侧顺序为扫描目标 → 设备列表 → 开始巡检 → 扫描设置；空闲态右侧更紧凑
- **巡检结果**：通道表优先；预警收成一行摘要（可展开）；日志默认可折叠；完成后隐藏进度条
- **外观**：亮/暗双层画布（窗口底 vs 卡片）；macOS 透明标题栏，空白处可拖动窗口
- **安装包**：macOS arm64 完整包（含 ffmpeg），见 [Releases](https://github.com/dongsheng512/NVR-Status/releases)

---

## 功能概览

| 能力 | 说明 |
|------|------|
| 状态巡检 | 在线、录像计划、音频、落盘、硬盘、健康汇总 |
| 深度抽检 | 可选 RTSP 短时抓流；可指定通道（`--av-channels`）与定点时刻（`--av-at`）；ffmpeg 检测音视频；可保存片段 |
| 多配置档案 | 下拉切换；新建 + 管理（另存 / 重命名 / 删除 / 导入 / 导出） |
| 多设备 | 每档案可维护多台 NVR（名称、IP、端口、账号、SSL） |
| 双入口 | GUI（同事友好）+ CLI（脚本/批量） |
| 安装包 | PyInstaller 打 Win / Mac 安装即用包（可捆绑 ffmpeg） |

---

## 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| 语言 / 运行时 | **Python 3.11+** | 主程序 |
| 包管理 | **[uv](https://github.com/astral-sh/uv)** + `pyproject.toml` / `uv.lock` | 依赖与可复现环境 |
| GUI | **PySide6**（Qt） | 主界面；通道表用 `QTableView` + Model/View |
| HTTP / 设备协议 | **requests** + 海康 **ISAPI**（Digest 认证） | 设备信息、通道、录像、存储等 |
| 流媒体抽检 | **ffmpeg / ffprobe**（RTSP） | 深度音视频抽检、样片保存 |
| CLI 输出 | **rich** | 终端彩色报告 |
| 打包 | **PyInstaller** | `NVRStatus.app` / `NVRStatus.exe` |
| 配置存储 | 本机用户目录 JSON 档案 | macOS `~/Library/Application Support/NVRStatus/` · Windows `%APPDATA%\NVRStatus\` |

### 架构要点

```
┌───────────────────┐     ┌──────────────┐
│  ui/  (PySide6)   │     │  nvr / CLI   │
│  run_gui.py       │     │ cli_report.py│
└────────┬──────────┘     └──────┬───────┘
         │                       │
         └──────────┬────────────┘
                    ▼
┌─────────────────────────────────────────┐
│  nvr_core/scan_runner.py   统一编排     │
│  build_nvr · run_nvr · scan_queue       │
└─────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│  nvr_core/  业务核心（无 Qt 依赖）      │
│  isapi_client · storage · recording     │
│  av_probe · health · util               │
│  hikvision_status.py = 兼容门面 + CLI   │
└─────────────────────────────────────────┘
         │
         ▼
┌──────────────────┐   ┌──────────────────┐
│  config_store.py │   │  services/       │
│  多档案配置读写  │   │  导出 · 历史 ·   │
│                  │   │  凭证 (keyring)  │
└──────────────────┘   └──────────────────┘
```

- GUI 用后台线程 `ScanWorker` + Qt Signal 更新进度，避免卡界面  
- 业务与 UI 分离，CLI / GUI 共用同一套巡检逻辑  
- 导出逻辑在 `services/export_report.py`（无 Qt 依赖，GUI/CLI 共用）

---

## 快速开始

### 开发运行（GUI）

```bash
uv sync
uv run python run_gui.py
```

### CLI

```bash
# 复制并编辑设备配置（勿把真实密码提交进 Git）
cp nvr_config.example.json nvr_config.json

./nvr -h
./nvr          # 默认巡检配置中全部设备
./nvr 1        # 只查第 1 台

# 只抽指定通道（不必等全量 64 路跑完）
./nvr 1 --deep-av-check --av-channels 31,64

# 单通道定点复查：钉在指定时刻各查一遍，区分持久故障与间歇抖动
./nvr 1 --deep-av-check --av-channels 31 --av-at 19:05,16:20
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
├── run_gui.py           # GUI 入口 → ui.app.main
├── hikvision_status.py  # 兼容门面：CLI 参数/入口 + 旧公共 API 再导出
├── nvr_core/            # 业务核心（无 Qt）：ISAPI / 存储 / 录像 / 抽检 / 健康 / 编排
├── config_store.py      # 配置档案
├── cli_report.py        # CLI 报告
├── nvr                  # CLI 启动脚本（多设备）
├── services/            # 无 Qt 依赖的纯逻辑（导出 / 历史 / 凭证）
├── ui/                  # PySide6 GUI（app / main_window / panels / widgets / theme）
├── nvr_config.example.json
├── NVRStatus.spec       # PyInstaller 规格
├── build/               # 打包脚本
├── assets/              # 图标与 logo
├── scripts/             # 图标生成等辅助脚本
├── tests/               # pytest（offscreen）
├── pyproject.toml
├── USAGE.md
└── PACKAGING.md
```

---

## 安全说明

- **不要**将含真实密码的 `nvr_config.json` 提交到 Git（已在 `.gitignore` 中忽略）。  
- GUI 档案保存在本机用户目录，不在安装包内写死密码。  
- **凭证存储（B7）**：macOS / Windows 优先把 NVR 密码写入系统 keyring
  （Keychain / Credential Manager），`profiles.json` 中密码字段为空；
  其它平台或 keyring 不可用时回退为 JSON 明文。
  - 请勿把 `profiles.json`、导出的配置 JSON（导出可能含明文密码）或用户数据目录
    拷贝、共享或上传到不受控环境；
  - 多用户机器上建议为系统账号设置登录密码并保持目录仅本人可读写。
- 内网工具：请仅在可信网络环境使用。

---

## 文档

| 文档 | 内容 |
|------|------|
| [USAGE.md](USAGE.md) | GUI / CLI 使用说明 |
| [PACKAGING.md](PACKAGING.md) | Windows / macOS 打包速查 |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | **开发交接：重写摘要、现状、已知问题、优化方向** |
| [docs/PLAN.md](docs/PLAN.md) | **PySide6 GUI 重写计划**（阶段、对等清单、里程碑） |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | 部署与分发全文（路径、发布流程、验收、排障） |
| [docs/analysis/](docs/analysis/) | PySide6 重写技术分析（基线 + 落地注意点） |
| [docs/README.md](docs/README.md) | 文档目录索引 |
| [bin/README.md](bin/README.md) | 捆绑 ffmpeg 说明 |

---

## License

Private / 内部使用（按仓库可见性为准）。
