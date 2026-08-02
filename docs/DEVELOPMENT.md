# 开发记录与后续方向（v2.0.0）

> 本文是 **PySide6 重写完成后的交接文档**：本次重写做了什么、当前状态、已知问题、后续优化方向。
> 计划与清单见 [PLAN.md](PLAN.md) · 部署见 [DEPLOYMENT.md](DEPLOYMENT.md) · 技术备忘见 [analysis/](analysis/)

---

## 1. 本次重写摘要

**范围：** GUI 壳从 CustomTkinter（v1）重写为 **PySide6（v2.0.0）**；业务层（`hikvision_status` / `config_store` / CLI）复用，未推倒。

### 1.1 目录落位

```text
cam-gui/
├── hikvision_status.py      # 兼容门面：CLI 入口 + 旧公共 API 再导出（核心在 nvr_core/）
├── nvr_core/                # 业务核心（无 Qt）
│   ├── isapi_client.py      # 会话、_get/_parse、缓存、cancel
│   ├── storage.py           # 硬盘、循环覆盖
│   ├── recording.py         # 计划、CMSearch 落盘
│   ├── av_probe.py          # ffmpeg 深度抽检
│   ├── health.py            # 健康规则汇总
│   ├── scan_runner.py       # 无 Qt 巡检编排（单机 scan + 多设备队列 scan_queue）
│   ├── nvr.py               # HikvisionNVR 组合类
│   └── util.py              # Colors、_which_tools、ScanCancelled、解析工具
├── config_store.py          # 配置档案（schema 不变，与 v1 兼容）
├── cli_report.py / nvr      # CLI（委托 scan_runner）
├── services/
│   └── export_report.py     # CSV(utf-8-sig)/TXT 导出，无 Qt 依赖（GUI/CLI 共用）
├── ui/                      # PySide6 GUI
│   ├── app.py               # QApplication 入口、全局字体、异常/崩溃日志钩子
│   ├── main_window.py       # 主窗组装 + 协调（APP_VERSION = "2.0.0"）
│   ├── panels/              # B1 拆分：left_panel / results_panel / log_panel
│   ├── scan_worker.py       # 后台巡检线程（threading）+ Qt Signal
│   ├── theme.py             # 调色板、QSS、亮/暗/跟随系统、下拉箭头 SVG
│   └── widgets/
│       ├── channel_table.py # QAbstractTableModel + QSortFilterProxyModel + QTableView
│       ├── device_editor.py # 设备编辑对话框（IP/端口校验）
│       ├── profile_bar.py   # 档案下拉 + 新建/另存/重命名/删除/导入/导出
│       └── status_bar.py    # 状态点 + 文案 + 百分比 + 进度条
├── run_gui.py               # GUI 入口 → ui.app.main
├── NVRStatus.spec           # PyInstaller 规格（PySide6，excludes 未用 Qt 模块）
├── build/build_mac.sh       # macOS 构建
├── build/build_win.ps1      # Windows 构建
└── pyproject.toml           # 2.0.0；PySide6~=6.8.0；[project.scripts] nvr-gui
```

### 1.2 关键实现决策

| 决策 | 说明 |
|------|------|
| 线程模型 | `ScanWorker(QObject)` + `threading.Thread`；业务回调只 `Signal.emit`，绝不直接碰 QWidget |
| 取消机制 | `hikvision_status` 新增 `ScanCancelled` 异常 + `HikvisionNVR.cancel()`；循环内 `_check_cancel()` 真正中断，非吞异常 |
| 通道表 | Model/View：数值排序、仅异常/仅离线筛选、右键复制/详情/导出、`Ctrl+C`、空态占位 |
| 进度 | 业务 `progress_callback` → `progress_update` 信号 → `_PHASE_RANGES` 阶段插值成整体 0~100% |
| 主题 | `theme.py` 集中状态色；`QSettings` 记忆 theme/geometry/splitter；跟随系统用 `styleHints().colorScheme` |
| 崩溃日志 | 未捕获异常/`QtMsgType` 写入 `app_data_dir()/logs/crash_*.log` |
| 导出 | `services/export_report.py` 无 Qt 依赖，可单测；CSV 用 `utf-8-sig` 兼容 Excel |
| 下拉箭头 | 运行时生成 SVG chevron（亮/暗/禁用三态）供 QSS `url()` 引用 |

### 1.3 与 v1 对照

| 项 | v1（CTk） | v2（PySide6） |
|----|-----------|---------------|
| 日志更新 | `queue` + `after` 轮询 | Signal/Slot 直连 |
| 通道表 | `ttk.Treeview` + tag 着色 | `QAbstractTableModel` + 排序/筛选 |
| 扫描设置折叠 | CTk 滚动区 | 两个可折叠 `QGroupBox`（基础 / 深度抽检） |
| 窗体记忆 | 无 | `QSettings` |
| 导出 | GUI 内实现 | `services/export_report.py` 共用 |
| 取消巡检 | 无 | 业务层 `ScanCancelled` 真取消 |

---

## 2. 当前状态

### 2.1 已完成并验证（off-screen 冒烟）

- 主窗布局、档案 CRUD/导入导出、设备列表/编辑、扫描目标选择
- 快速/深度巡检、无 ffmpeg 降级、后台不卡 UI、进度 + 阶段文案、取消
- 结果区：汇总、指标卡（tone 彩色左条）、预警（彩色富文本）、通道表、日志（分级着色 + 封顶 5000 行 + 自动滚动）
- 主题切换、窗体/分割条记忆、崩溃日志
- CSV/TXT 导出、打开目录
- UI 细节：侧栏宽度自适应（不再溢出遮挡）、Splitter 手柄 6px 无重叠、下拉框原生箭头、扫描设置填写框原生样式
- 冒烟脚本（`QT_QPA_PLATFORM=offscreen`）：窗口构建、表排序/筛选/复制、渲染结果、主题循环、导出
- **优化计划（阶段 A）**：进度/日志信号 80ms 节流（`ScanWorker._SignalThrottle`）；主题切换重绘预警 HTML；`nvr-gui` 入口已可安装（补 `[build-system]`）；密码明文风险写入 README/USAGE
- **优化计划（阶段 B）**：B1 拆 `main_window` 至 `ui/panels/`（左配置/结果/日志，主窗 584 行只组装）；B2 拆 `hikvision_status` 至 `nvr_core/`（7 模块 + 兼容门面）；B3 抽 `scan_runner`（GUI/CLI/入口共用编排）；B8 最小窗 + 详情窗单例；B4 删除 `gui_app.py`（CTk 遗留，无引用）；B5 多设备队列（`scan_queue` + `QueueScanWorker` + 目标下拉「全部设备」）；B6 历史报告（`services/history.py` 归档 + `HistoryDialog` 查看/再导出）；B7 凭证安全（`services/credentials.py` macOS Keychain / Windows Credential Manager，不可用回退明文；`ConfigStore.resolve_devices` 补全 + `update_profile` 迁移）

### 2.2 自动化测试（A4）

```bash
QT_QPA_PLATFORM=offscreen uv run pytest   # 33 例：导出 / 通道筛选排序 / 覆盖解析 / lookback 换算 / 取消 / 节流
```

用例位于 `tests/`，非 Qt 用例无需 QApplication；Qt 用例用 `tests/conftest.py` 的 session 级 `qapp` 夹具（offscreen）。

### 2.3 未完成 / 待验收（阻塞 v2.0.0 发布）

| 项 | 状态 | 说明 |
|----|------|------|
| **真机 NVR 验收** | 待做 | 阶段 1 关卡；跑一次完整快速/深度巡检，核对进度文案与结果区 |
| **PyInstaller 打包验收** | 待做 | spec/脚本已更新，未在 Win + Mac 实际构建；PySide6 体积粗估 80–160 MB（+ffmpeg 再 +50–120 MB） |

---

## 3. 已知问题与技术债

> 按影响排序。前两条不影响开发运行，但影响发布体验。

### 3.1 `nvr-gui` 入口脚本（已修复）

`pyproject.toml` 已补 `[build-system]`（setuptools）+ `[tool.setuptools]`（py-modules/packages），`uv sync` 后 `nvr-gui` console 脚本可正常安装：

```bash
uv sync && uv run nvr-gui        # 与 uv run python run_gui.py 等价
```

### 3.2 打包未实测（中）

- PyInstaller 对 PySide6 会自动带 Qt 平台插件（Win `qwindows` / Mac `qcocoa`），spec 已 `excludes` 未用模块，但**未经真机打包回归**。
- 建议打包验收时记录：冷启动耗时、体积、无 console 下报错能否从 `logs/crash_*.log` 定位。

### 3.3 进度/日志信号节流（已实现 A1）

`ui/scan_worker.py` 新增 `_SignalThrottle`：后台线程每 80ms 合并一次 emit —— 日志逐条保留、进度只留最新一帧，巡检结束/失败/取消前 `flush()` 保证末帧与终态信号有序。深抽检 64 路场景 UI 不再高频刷信号。

### 3.4 主题切换后局部残留（warn_box 已修复 A5）

- `warn_box` 的彩色 HTML 已修复：`_render_result` 缓存 `_last_warn_lines`，`_sync_theme_widgets` 切主题时按当前主题重绘。
- `ChannelDetailDialog` 的 muted 色仍在新建时才取当前主题（打开时读 `effective_dark()`，打开期间不随切换刷新）——可接受，如需可改为全局注册刷新。
- 已覆盖的动态刷新：`warn_box`、`device_sub_label`、日志提示、图例、设备行 IP/工具提示、指标卡、状态栏、通道表。

### 3.5 侧栏折叠展开后的 2px 剪裁（低）

展开「扫描设置」后出现垂直滚动条，viewport 变窄约 10px，内容在右侧被剪裁约 2px（被 viewport 裁掉，不侵入右面板）。可接受，但若想更精细可加大侧栏最小宽度余量。

### 3.6 细节若干

| 项 | 说明 |
|----|------|
| 通道详情窗 | 每次打开新建顶层窗，多次双击会叠加多个窗口（非模态） |
| chevron SVG | 每次运行在 `tempfile.gettempdir()` 生成 `nvr_chevron_*.svg`，可写、不清理（可忽略） |
| 字体告警 | off-screen 下 `Populating font family aliases … "Sans Serif"`，仅测试环境噪音 |
| off-screen 告警 | `This plugin does not support propagateSizeHints()`，仅测试环境噪音 |
| 密码明文 | 沿用 v1 设计，`profiles.json` 明文保存账号密码；文档已提示风险 |
| 窗口最小尺寸 | 未显式限制；小屏 / 高分缩放异常时可能过窄（右面板最小宽度由内容决定） |

---

## 4. 后续优化方向

> **完整优化计划（评估 + 分阶段任务 + 架构演进 + 看板）已整理至：**  
> **[optimization/OPTIMIZATION-PLAN.md](optimization/OPTIMIZATION-PLAN.md)**  
> 以下为摘要；执行以 optimization 文档为准。

### 4.1 发布前必做（v2.0.0 阻塞）

1. **真机回归**：固定 1–2 台 NVR，快速 + 深度巡检各一次，核对进度/预警/导出。
2. **双平台打包**：跑 `build_mac.sh` / `build_win.ps1`，验证启动、图标、无 console、ffmpeg 捆绑。
3. **（可选）修 `nvr-gui` 入口** 或明确「仅 `run_gui.py`」。

### 4.2 短期体验（P2，随缘但收益高）

| 方向 | 说明 |
|------|------|
| 进度节流 | §3.3，深抽检不卡 UI |
| 主题即时刷新 | §3.4，`warn_box`/详情窗跟随主题重绘 |
| 批量巡检队列 | PLAN §4.3：多设备顺序/并发一键巡检 |
| 历史报告列表 | 巡检结果归档 + 列表查看/导出 |
| 窗口约束 | 设定合理最小尺寸，兼容小屏 |
| 完成通知策略 | 少弹窗：状态栏高亮 + 可选通知 |

### 4.3 工程质量

| 方向 | 说明 |
|------|------|
| 自动化测试 | 目前仅有临时 off-screen 冒烟脚本；建议 `pytest` + `pytest-qt`（`QT_QPA_PLATFORM=offscreen`），覆盖模型/导出/线程取消 |
| QSS 资源化 | `theme.py` 的 f-string QSS 过大，可拆到 `ui/resources/app.qss` 模板 |
| 日志持久化 | 现仅崩溃日志；可加每日滚动运行日志（`logs/`）便于远程诊断 |
| 图标/资源管理 | 用 `qrc` 或 `importlib.resources` 收拢 assets，替代路径猜测 `_find_icon` |

### 4.4 产品增强（P2+，按需）

- 凭证安全：接入系统 keyring（macOS Keychain / Windows Credential Manager），明文转加密存储。
- 代码签名 / 公证（Mac）、代码签名（Win）：消除 Gatekeeper / SmartScreen 拦截。
- 多语言（i18n）：目前文案全中文硬编码。
- 自动更新检查：内网地址 + 版本对比。
- CI：GitHub Actions 双平台矩阵构建（Private 仓库 artifact 注意保留策略）。
- ffmpeg 增强：版本检测、缺失时引导安装/下载。

### 4.5 明确不做

- QML、应用内视频播放器、asyncio+qasync、与 CTk 长期双 UI、像素级复刻 CTk 皮肤。

---

## 5. 常用开发命令

```bash
uv sync                                   # 安装依赖
uv run python run_gui.py                  # 启动 GUI（唯一可靠入口）
QT_QPA_PLATFORM=offscreen uv run python - <<'PY'  # 无头冒烟
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow
app = QApplication([])
w = MainWindow(); w.show(); app.processEvents()
print("ok"); w.close()
PY
uv run python -c "import PySide6; print(PySide6.__version__)"
./build/build_mac.sh                      # macOS 打包（目标机）
powershell -ExecutionPolicy Bypass -File build\build_win.ps1   # Windows 打包
```

用户数据位置：macOS `~/Library/Application Support/NVRStatus/` · Windows `%APPDATA%\NVRStatus\`（崩溃日志在 `…/logs/`）。

---

## 6. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-08 | 创建：PySide6 重写交接文档（摘要 / 现状 / 已知问题 / 优化方向） |
| 2026-08 | 阶段 A 落地：A1 信号节流、A4 pytest 33 例、A5 主题重绘预警、A6 nvr-gui 可装 + gui_app 标注 legacy、A7 密码风险文档 |
| 2026-08 | 阶段 B 落地：B1 拆 main_window → ui/panels/；B2 拆 hikvision_status → nvr_core/；B3 scan_runner 共用；B8 最小窗+详情单例；B4 删除 gui_app.py（CTk 遗留）；B5 多设备队列巡检；B6 历史报告归档；B7 凭证 keyring（macOS Keychain / Windows CM），阶段 B 收官 |
