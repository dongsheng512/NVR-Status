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
│   ├── main_window.py       # 主窗组装 + 协调（APP_VERSION = "2.3.0"）
│   ├── panels/              # B1 拆分：left_panel / results_panel / log_panel
│   ├── scan_worker.py       # 后台巡检线程（threading）+ Qt Signal
│   ├── theme.py             # 调色板、QSS、亮/暗/跟随系统、下拉箭头 SVG
│   └── widgets/
│       ├── channel_table.py # QAbstractTableModel + QSortFilterProxyModel + QTableView
│       ├── device_editor.py # 设备编辑对话框（IP/端口校验）
│       ├── profile_bar.py   # 档案下拉 + 新建 + 管理菜单（另存/重命名/删除/导入/导出）
│       └── status_bar.py    # 状态点 + 文案 + 百分比 + 进度条
├── run_gui.py               # GUI 入口 → ui.app.main
├── NVRStatus.spec           # PyInstaller 规格（PySide6，excludes 未用 Qt 模块）
├── build/build_mac.sh       # macOS 构建
├── build/build_win.ps1      # Windows 构建
└── pyproject.toml           # 2.3.0；PySide6~=6.8.0；[project.scripts] nvr-gui
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
QT_QPA_PLATFORM=offscreen uv run pytest   # 232 例：导出 / 通道筛选排序 / 覆盖解析 / lookback 换算 / 取消 / 节流 / 档案条 / 空闲布局 / CMSearch 分页 / 音频抽检 / 音视频端到端(伪 ffmpeg) / 并发单飞 / 物理通道去重 / 指标口径 / 凭证快照 / 名称列伸缩 / 设备删除 / 通道检测状态判定 / 未知口径 / 在线卡呈现 / 回放 :554 端口回退 / 抽检通道过滤 / 落盘阶段取消 / 2026-09 回归
```

> **⚠️ 本机（agent shell）跑 pytest 必须显式指定 `--basetemp`**，否则会出现「第一次全绿、第二次起大量 ERROR」的假失败：
>
> ```
> ERROR tests/test_history.py::test_list_error_report - PermissionError: EEXIST:
>   file already exists, mkdir '.../T/pytest-of-root'
> ```
>
> 原因是本机沙箱 shim 拦截 `mkdir`，对**已存在目录**的 `mkdir(exist_ok=True)` 直接抛 `PermissionError`；
> 而 pytest 9.x 的 `getbasetemp()` 对默认根目录恰好用 `mkdir(exist_ok=True)`，
> 首次运行建目录成功，之后每次都会 `EEXIST` → 回退 `pytest-of-unknown` → 再次失败。
>
> 解法（`--basetemp` 会先 `rm_rf` 再 `mkdir`，永远不撞已存在目录）：
>
> ```bash
> QT_QPA_PLATFORM=offscreen uv run pytest --basetemp=/tmp/cam-pytest
> ```
>
> 这**不是代码问题**，CI 与本地正常终端不受影响。

用例位于 `tests/`，非 Qt 用例无需 QApplication；Qt 用例用 `tests/conftest.py` 的 session 级 `qapp` 夹具（offscreen）。

> **⚠️ 写 Qt 测试时的坑：不要用 `monkeypatch.setattr` 打在 PySide6 实例上。**
> teardown 会执行 `delattr` 去还原，对 shiboken 对象直接**段错误**（进程崩溃、无 Python 回溯）：
>
> ```
> Fatal Python error: Segmentation fault
>   File ".../_pytest/monkeypatch.py", line 409 in undo
> ```
>
> 解法：手动保存/还原，绝不做 `delattr`。打在**模块属性**上（如 `monkeypatch.setattr(left_panel, "QMessageBox", Dummy)`）是安全的。
>
> ```python
> orig = header.setSectionResizeMode
> header.setSectionResizeMode = spy          # 打实例：只用赋值
> try:
>     ...
> finally:
>     header.setSectionResizeMode = orig     # 只用赋值还原
> ```

### 2.3 未完成 / 待验收

| 项 | 状态 | 说明 |
|----|------|------|
| **真机 NVR 验收** | 待做 | 跑一次完整快速/深度巡检，核对进度文案与结果区；**新增的「音频轨独立抽检 + 初检异常换时段复检」路径需实测单台耗时**（每路多一次 ffmpeg 调用） |
| **PyInstaller 打包验收** | 部分 | 2.2.0 macOS 已构建并发布 Release（full ~149MB / zip ~61MB，Info.plist 2.2.0、新监控图标、内置 ffmpeg 8.1.2；build_mac.sh 已固化 ffmpeg 覆盖后的整体重签名）；Win 待目标机验收 |

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
| 通道详情窗 | ✅ 已单例复用（B8） |
| chevron SVG | 每次运行在 `tempfile.gettempdir()` 生成 `nvr_chevron_*.svg`，可写、不清理（可忽略） |
| 字体告警 | off-screen 下 `Populating font family aliases … "Sans Serif"`，仅测试环境噪音 |
| off-screen 告警 | `This plugin does not support propagateSizeHints()`，仅测试环境噪音 |
| 密码存储 | ✅ B7 keyring（macOS Keychain / Windows CM）；不可用回退明文；写入失败不丢密 |
| 窗口最小尺寸 | ✅ `setMinimumSize(1000, 700)`（B8） |

---

## 4. 后续优化方向

> 优化计划见 [optimization/OPTIMIZATION-PLAN.md](optimization/OPTIMIZATION-PLAN.md)；
> 2026-09 已按一次全面代码审查修复了其中的高/中优先级项（见 §6 修订记录）。
> 以下为剩余方向摘要。

### 4.1 发布前必做（按此顺序）

1. **拆分提交**（先做）：`av_probe` 的音频轨独立抽检 / 换时段复检 / 10 分钟截止线拆成独立 commit；第二轮修复（并发加锁、通道去重、指标口径、清理）另成一类。**先把工作区收干净，真机验收才有可回退的基线。**
2. **真机回归**：固定 1–2 台 NVR（**设备位置与访问方式待确认，不要预设需要异地跑现场**），快速 + 深度巡检各一次，核对进度/预警/导出。三个重点：
   - 音频轨独立抽检 + 换时段复检的**每台耗时**（每路多一次 ffmpeg 调用，64 路需实测）；
   - 日志里是否出现「已按物理通道合并 N 条重复码流轨道」——出现即说明该固件确实每通道返回主+子码流，通道去重生效；不出现则说明该机型每通道只有一条 Track，去重逻辑空转（不会出错）；
   - 结果区指标是否还有 `x/总数` 里 x 超过总数的比例。
3. **Windows 打包验收**：跑 `build_win.ps1`，验证启动、图标、无 console、ffmpeg 捆绑。

### 4.2 短期体验（P2）

| 方向 | 状态 | 说明 |
|------|------|------|
| 完成通知策略 | **待做** | 目前巡检完成的唯一反馈是日志行 + 状态栏。可加托盘气泡/系统通知（保持"少弹窗"原则） |
| 主题即时刷新 | 收尾 | `warn_box`/指标卡/通道表/日志/详情窗**打开时**都已跟随切换；仅「详情窗已打开期间再切主题」不刷新（§3.4）。可接受，如需可全局注册重绘 |
| 进度节流 | ✅ 已完成 | A1，见 §3.3 |
| 批量巡检队列 | ✅ 已完成 | B5，`nvr_core/scan_runner.scan_queue` |
| 历史报告列表 | ✅ 已完成 | B6，`services/history.py` |
| 窗口约束 | ✅ 已完成 | `setMinimumSize(1000, 700)`（B8） |

### 4.3 工程质量

| 方向 | 说明 |
|------|------|
| 自动化测试 | 已有 189 例 pytest（`QT_QPA_PLATFORM=offscreen`，本机需加 `--basetemp`，见 §2.2）。ffmpeg 抽检的**端到端**缺口已补（`tests/test_av_integration.py`，用伪 ffmpeg/ffprobe 真实走 subprocess） |
| QSS 资源化 | `theme.py` 的 f-string QSS 过大，可拆到 `ui/resources/app.qss` 模板 |
| 日志持久化 | 现仅崩溃日志；可加每日滚动运行日志（`logs/`）便于远程诊断 |
| 图标/资源管理 | 用 `qrc` 或 `importlib.resources` 收拢 assets，替代路径猜测 `_find_icon` |

#### 第二轮审核遗留（P2 代码质量）

来源：`docs/code-review-2026-09-10.md` §4（P1/P2-1/P2-2/P3 已修，见该文 §7）。
下表 1–4 项**已修复并加了回归测试**；第 5 项是有意保留的取舍，不是缺陷。

| 顺序 | 位置 | 问题 | 处置 |
|---|---|---|---|
| 1 | `config_store.get_profile` | getter 却会写 `self.data`，且返回内部对象本身 | ✅ **已修**：改为返回 `deepcopy` 快照，不再登记新档案。堵住了「GUI 把 keyring 明文写回 getter 返回值 → 下次 save 落盘 profiles.json」的明文泄漏路径。回归：`tests/test_credentials.py` 3 例 |
| 2 | `results_panel.render_result` | `total==0` 时 `audio_tone` 给 `warn`；「含音频配置」与深抽检实测数字语义易混 | ✅ **已修**：空集改 `muted`/「未检查」；深抽检模式下标题切「音频实测」，分母为「实际抽到流」的通道，跳过不计入，`bad/warn` 分级；卡片 tooltip 给出正常/静音/无音轨/未确认/跳过分解。回归：`tests/test_metric_denominator.py` 4 例 |
| 3 | `channel_table._balance_name_column` | 每次 `resize` 反复切 Stretch/Interactive，大表有 layout 抖动风险 | ✅ **已修**：缓存 `_name_mode`，只在模式真正变化时调 `setSectionResizeMode`。实测未修版一次 900→760px 拖动会切 7 次表头。回归：`tests/test_channel_table.py` 2 例 |
| 4 | `left_panel._del_device` | 「全部删除」兜底依赖 `_device_rows` 尚未重新赋值的时序 | ✅ **已修**：先定 keep 集合并写回 `_device_rows`，再动控件；顺带用 `row_data["row"]` 取代 `chk.parentWidget()` 定位行。回归：`tests/test_idle_layout.py` 3 例（含中途幽灵设备观测） |
| 5 | `services/credentials`（macOS 分支） | `security -w <password>` 把密码放命令行参数，短时可见于进程列表 | ⏸ **保留**：内网可接受；如需更严改用 keyring 库 |

> **已核实并撤回**：原「Windows `CredentialBlobSize=(len+1)*2` 多算结尾 NUL」一条**是误判**。
> MSDN 只在 `CRED_TYPE_DOMAIN_PASSWORD` / `CRED_TYPE_DOMAIN_CERTIFICATE` 下要求不含结尾零字符；
> `CRED_TYPE_GENERIC` 是 *"defined by the application"*，含 NUL 合法。**不要按那条去改。**

> **产品决策（2026-09-10 已落地）**：原先 `健康状态` 把「落盘未知」和「音频抽检未知」都无条件算 `警告`，两者成因多为**瞬时**检索/拉流超时，任一路抖动即整机降级，告警偏吵。现已**两个一起调**（保持两套「未知」口径一致）：
> - **部分通道未知** → 只进统计与结果区（在线/录像卡 tooltip 已有提示），**不参与** `健康状态` 判定；
> - **实际检查到的通道全部未知**（整体检索/拉流失败）→ 升级为 `警告`，避免变成假阴性；
> - **不新增**第 4 个严重度级别（`良好 / 警告 / 严重` 三档不变，UI/导出/CLI 退出码无需改动）。
>
> 回归：`tests/test_health_unknown_policy.py`（含「两个口径一致」用例）。

#### 2026-09-10 复查新发现（1 已落地，2–3 待办）

| 顺序 | 位置 | 发现 | 建议 |
|---|---|---|---|
| 1 | `isapi_client._get_input_proxy_cameras` | ~~采集了 `chanDetectResult` 存进 `检测状态`，但**全仓零使用**（grep 仅 3 处命中，全是赋值）~~ → ✅ **已落地（2026-09-10）**：`检测状态` 已纳入摄像头在线判定 | 采用**白名单**：`connect`=在线；`notExist`/网络异常/IP 冲突等=异常；**其余取值一律不判定**（宁可漏判不误报，适配固件差异）。判定优先级：`online=false` 权威 → `online=true` 且检测异常则另记「在线通道检测状态异常」→ `online` 缺失时用检测状态补判 → 两者都非明确取值记「未确认」（不误报离线）。离线预警附原因细分（未接入/网络不可达…）。**零额外请求**。回归：`tests/test_health_detect_state.py`、`tests/test_results_online_card.py` |
| 2 | `av_probe._run_deep_av_checks` | 视频抽检通过后才串行跑音频抽检（同一个通道两次 ffmpeg 调用先后执行） | 两者互不依赖，可并行 → 单通道抽检墙钟时间约减半。**代价**：并发 ffmpeg 进程数翻倍，需连同 `av_workers` 一起重算，并真机测 64 路负载 |
| 3 | `ui/main_window.py` / `ui/panels/left_panel.py` | 体量回升到 **1111 / 1022 行**，已超计划目标 `< ~800`（计划复核时是 723 / 848） | 主窗可再往外挪「档案管理 / 历史报告 / 导出」等编排逻辑；`left_panel` 的「扫描设置」与「设备表单」可拆成独立组件。非阻塞，但继续膨胀会拖慢后续改动 |

### 4.4 产品增强（P2+，按需）

- 凭证安全：✅ 已接入系统 keyring（见 B7）；Linux 等平台仍回退明文。
- 代码签名 / 公证（Mac）、代码签名（Win）：消除 Gatekeeper / SmartScreen 拦截。
- 多语言（i18n）：目前文案全中文硬编码。
- 自动更新检查：内网地址 + 版本对比。
- CI：GitHub Actions 双平台矩阵构建（Private 仓库 artifact 注意保留策略）。
- ffmpeg 增强：版本检测、缺失时引导安装/下载。

### 4.5 明确不做

- QML、应用内视频播放器、asyncio+qasync、与 CTk 长期双 UI、像素级复刻 CTk 皮肤。

### 4.6 建议推进顺序（2026-09-10 汇总）

| 序 | 事项 | 为什么排这 | 阻塞? |
|---|---|---|---|
| 1 | 拆分提交（§4.1.1） | 工作区脏，先拿到可回退基线 | 阻塞真机验收 |
| 2 | 真机回归（§4.1.2） | 唯一能关闭发布门槛的动作；顺带实测音频抽检耗时 | **阻塞发布** |
| 3 | Windows 打包验收（§4.1.3） | 同上（Mac 侧 2.2.0 已发布） | **阻塞发布** |
| 4 | ✅ 已完成 `chanDetectResult` 纳入快速巡检（2026-09-10） | 零额外请求、零新依赖，直接提升快速巡检的"画面异常"判断力 | 否 |
| 5 | ✅ 已完成 §4.3 产品决策（未知是否算警告，两个一起调）（2026-09-10） | 口径问题拖久了会被当成"故意设计" | 否 |
| 6 | 完成通知策略（§4.2 唯一待做项） | 体验收尾，成本低 | 否 |
| 7 | 音频抽检并行化 | 纯性能，但要连 `av_workers` 一起重算 + 真机压测 | 否 |
| 8 | 主窗/左栏拆分（§4.3 新发现 3） | 非功能，但拖着会持续抬高每次改动的成本 | 否 |
| 9 | QSS 资源化 / 运行日志落盘 / 图标收拢 | 工程债，按需 | 否 |
| 10 | 签名公证、i18n、自动更新、CI、ffmpeg 引导 | 产品化，视分发需求 | 否 |

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
| 2026-08-05 | 除虫收尾：Windows 多设备凭证 TargetName、keyring 写失败不丢密、档案 rename/delete/clone/删设备凭证生命周期、队列失败归档、pyproject 补包、文档同步 B7；pytest 55 例全绿 |
| 2026-09-06 | **应用图标重绘**：`scripts/make_icon.py` 以 QPainter 矢量绘制监控主题图标（CCTV 枪机 + 支架 + 绿色状态灯 + 信号弧，深蓝底板），一键重建 `assets/AppIcon.iconset/` → `AppIcon.icns`（iconutil）、`app_logo.png`（窗口图标）与 `AppIcon.ico`（Windows，此前 spec 引用但缺失）。改图标只需改脚本再运行 `uv run python scripts/make_icon.py` |
| 2026-09-06 | 全面代码审查与修复（pytest 84 例全绿）。**UI**：修复跟随系统主题深浅反转（`Qt.ColorScheme` 枚举误用）、日志超 5000 行后新日志被挤成一行、大窗提示标签主题残留、状态栏死代码、`_open_path` 改 Popen 不阻塞 UI、关窗时有界 join 巡检线程、结束的 worker `deleteLater` 回收、历史列表后台线程加载、qt.log 5MB 轮转。**nvr_core**：并发阶段（CMSearch/深度抽检）真正可取消（worker 检查点 + `cancel_futures` + ffmpeg `Popen` 取消即 kill）；ffmpeg stderr 中的 RTSP 凭据掩码后才进报告；`Session.close()` 逐台回收；瞬时网络失败不再负缓存；设备时区仅成功时缓存；`get_cameras` 结果缓存（每台省 4 次请求）；`_get/_post` 失败经回调进 GUI 日志；SSL 模式首次请求提示跳过证书校验（`disable_warnings` 收敛到 SSL 会话）；`search_workers` 上限 16；未配置录像的通道不再发 CMSearch；无时区设备时间按设备时区解释（修复停录误报正常）；`freeSpace` 缺失不再伪造 100% 满盘；健康判定大小写不敏感且不算「未知」为异常盘；`silence_db=0`/`busy_start=0` 不再被吞成默认值；单台设备配置异常不再中止队列；CLI 单机退出码（0 成功/1 失败/2 健康「严重」）。**安全/存储**：profiles.json 原子写入（mkstemp + os.replace）且 JSON 损坏时先留档再重置、读取占用时不覆盖；档案导出临时文件+POSIX 0o600+GUI 明文密码警示；CSV 公式注入防护；keyring `security` 调用捕获 `TimeoutExpired`（Keychain 授权框不再令启动崩溃）；设备 XML 拒绝 DOCTYPE/ENTITY 与超大响应；历史报告文件名碰撞与 `_prune` 竞态修复；pyproject 移除占位 `main` 模块、spec 清理 PyInstaller 6 废弃参数 |
| 2026-09-09 | **v2.2.0**：档案条改为「新建 + 管理」；左侧重排；空闲/结果布局压缩（日志可折叠、预警一行摘要、通道表优先、完成后隐藏进度）；macOS 透明标题栏与窗口拖动、亮/暗双层画布；pytest 96 例；macOS arm64 完整包发布 GitHub Release |
| 2026-09-10 | **第二轮代码审查与修复**（pytest 127 例全绿，新增 13 例回归）。**并发**：`_parse` / `_parse_endpoint_quiet` 缓存读写加 `_cache_lock`（RLock，允许进度回调同线程重入），`_get_device_tz` 加 `_tz_lock` 双重检查；`get_recording_status` / `_run_deep_av_checks` 启动线程池前预热设备时区 —— 消除线程池共享 `requests.Session` 与重复请求同一端点；`to_search` 成员判断由列表按值比较改为 id 集合（内容相同的记录不再被误判为「已检索」）；落盘检索结果由「以 `track_id` 为键」改为「以记录身份为键」，重复/「未知」track_id 不再互相覆盖磁盘结论。**口径**：`_physical_channel` 统一「物理通道 + 两位码流号」编码（101/102 同属通道 1，此前 102 会解析成独立通道 `102`，导致名称/在线显示「未知」且重复占行）；`get_recording_status` 按物理通道合并主/子码流轨道（保留编号最小的主码流，通道号非数字时不合并），同时减少无效 CMSearch；结果区「录像正常 / 近期有录像 / 含音频配置」指标分母改用与分子同源的 `len(records)`，原来用摄像头数，一通道多 Track 的机型会出现 `5/4` 失真比例。**清理**：删除根目录无引用死代码 `main.py`。**文档**：README 仓库结构补 `nvr_core/`、架构图改为「scan_runner 编排 + nvr_core 业务核心」；测试例数修正；补充本机 pytest 必须 `--basetemp` 的环境坑说明；修正「阻塞 v2.0.0 发布」「OPTIMIZATION-PLAN 已遗失」「仅有冒烟脚本」等过时表述。 |
| 2026-09-10 | **第二轮审查 P2 清理收官**（pytest 154 例全绿，本轮新增 27 例）。**测试补缺**：新增 `tests/test_av_integration.py`（15 例）——用环境变量驱动的伪 `ffmpeg`/`ffprobe` 真实走 subprocess，覆盖「视频/音频分两次 `-map` 拉取、原 URI 优先、低分辨率/无视频流判异常、ffprobe 失败、日志凭据掩码、无音轨在配置开/关下的不同结论、静音警告、拉流失败归未知、候选过滤 + `av_limit`、换时段复检成功/仍失败、取消传播、无 URI 跳过」整条链路（此前只有单元级覆盖）。**修缺陷**：`config_store.get_profile` 改为返回 `deepcopy` 快照并停止向 `self.data` 登记新档案 —— 原先返回内部对象本身，GUI `_load_active_profile_to_form` 会把 `resolve_devices()`（含 keyring 取回的明文）写进该对象，下一次 `save()` 即把明文落进 `profiles.json`，等于抹掉 keyring 设计；`results_panel.render_result` 空结果集不再给音频卡 `warn`（改 `muted`/「未检查」），深抽检模式下音频卡标题切为「音频实测」、分母改为「实际抽到流」的通道（`跳过` 不计入），并按 无音轨=红 / 静音·未确认=黄 分级，tooltip 给出分解；`channel_table._balance_name_column` 缓存 `_name_mode`，只在模式真正变化时调 `setSectionResizeMode`（未修版一次 900→760px 拖动触发 7 次表头模式切换 → 列宽抖动）；`left_panel._del_device` 先把保留集合写回 `_device_rows` 再动控件（原先兜底补设备时 `_refresh_scan_target()` 会在「`_device_rows` 仍含已删设备」的中途执行，扫描目标下拉短暂出现幽灵设备，实测观测到 `4` 台），并用 `row_data["row"]` 取代 `chk.parentWidget()` 定位行。**撤回误判**：原「Windows `CredentialBlobSize=(len+1)*2` 违反 MSDN」不成立 —— 不含结尾 NUL 只约束 `CRED_TYPE_DOMAIN_PASSWORD`/`CRED_TYPE_DOMAIN_CERTIFICATE`，`CRED_TYPE_GENERIC` 由应用定义，代码不改。**文档**：§4.3 遗留表改为处置表（1–4 已修、5 有意保留）；补「Qt 测试不要用 `monkeypatch` 打 PySide6 实例」的段错误坑与手写 save/restore 范式。 |
| 2026-09-10 | **高性价比两项落地**（pytest 189 例全绿，本轮新增 35 例：`test_health_detect_state.py` 23 + `test_health_unknown_policy.py` 9 + `test_results_online_card.py` 3）。**① 通道检测状态（`chanDetectResult`）纳入快速巡检判定**：该字段此前已采集进 `检测状态` 但全仓零使用。现以**白名单**归一（`connect`=在线；`notExist`/网络异常/IP 冲突等=异常；**其余取值一律不判定** —— 宁可漏判不误报，适配各固件取值差异），优先级为 `online=false` 权威 → `online=true` 且检测异常则另记「在线通道检测状态异常」（警告）→ `online` 缺失时用检测状态补判 → 两者都非明确取值计入新增统计 `摄像头状态未确认`（不误报离线）；离线预警附原因细分（通道未接入 / 网络不可达…）。**零额外请求**。结果区在线卡新增 `通道检测异常`→tone=warn、`未确认`→tooltip（数值与容器形态不变），CLI 明细行同步。**② 「未知」健康口径收敛**：`落盘未知` 与 `音频抽检未知` 两个口径**一起调** —— 部分通道未知（瞬时检索/拉流抖动）只进统计不参与判定，实际检查到的通道**全部未知**（整体失败）才升 `警告`，避免假阴性；**不新增**第 4 个严重度级别。回归另用**变异测试**反向验证（还原旧逻辑 → `test_health_detect_state.py` 红 13 例、`test_health_unknown_policy.py` 红 2 例；过度判定 → 红 5 例）。 |
| 2026-09-10 | **深度抽检两项增强**（pytest 231 例全绿，本轮新增 42 例：`test_av_rtsp_port.py` 12 + `test_av_channels.py` 26 + `test_av_integration.py` 4）。**① 回放 RTSP 增加 `:554` 端口回退**：真机排查 31/64 路时发现 CMSearch 返回的 `playbackURI` 端口是设备 **HTTP** 端口（`:80`），该端口的 RTSP 被静默拒绝 —— TCP 三次握手正常、一发 `DESCRIBE` 即断开且不回数据，ffmpeg 只报 `Invalid data found when processing input`；而同一路径换 `:554` 返回正常的 `RTSP/1.0 401 Unauthorized`。现由 `_swap_rtsp_port` 为每个候选追加一个「只换端口」的 `@554` 变体，并用 `av_candidate_order` 排序把它**一律排到最后**（正常链路不受影响，不会多等一次最长 ~30s 的超时）。`_probe_track_av` / `_pull_rtsp_map` 的 `label == "original"` 判定改为 `startswith("original")`，使 `original@554` 也走「改写短窗」的较紧超时预算。**② CLI 新增 `--av-channels`**：`hikvision_status` / `./nvr` 均可只抽检指定物理通道（如 `--av-channels 31,64`），`parse_channel_list` 统一归一化（接受 `,`/`，`/`、`/空格` 分隔与数组），**无效通道号直接报错而非静默丢弃**（静默丢弃会让人以为查过某路、其实没查）；过滤发生在「非候选标记」之后、「`av_limit` 之前」，且列表内通道若「未配置录像/近期无录像」仍报**真实问题**而非「不在列表」；实测通道标 `跳过` + 「不在指定抽检通道列表」，报告「音视频抽检」行追加「仅通道 31、64」，不会让用户误以为漏检。GUI 不设该选项（= 全部通道），`profiles.json` 的 `scan_options` 无需新增键。回归另用**变异测试**反向验证 5 处：去掉 `@554` 生成 / `@554` 不再排最后 / `_swap_rtsp_port` 恒返回 None / 过滤判定恒真 / `av_limit` 先于过滤 / 无效通道号静默丢弃 / CLI 不传参 —— 均被对应用例捕获。 |
