# NVR Status 代码优化计划

> **版本基准：** v2.0.0（PySide6 GUI + 复用业务层）  
> **整理日期：** 2026-08-02  
> **范围：** 架构、工程质量、体验、安全、发布就绪  
> **非目标：** QML、应用内播放器、与 CTk 长期双 UI

---

## 1. 现状摘要

### 1.1 规模与结构

| 模块 | 约行数 | 角色 |
|------|--------|------|
| `hikvision_status.py` | ~2200 | 业务核心：ISAPI、落盘、深度抽检、健康汇总、CLI 参数 |
| `ui/main_window.py` | ~2000 | 主窗：布局、设备、扫描、结果渲染、日志、大窗 |
| `gui_app.py` | ~2500 | **遗留** CustomTkinter GUI（默认入口已切 PySide6） |
| `cli_report.py` | ~600 | CLI 富文本报告 |
| `ui/widgets/*` + `theme` + `scan_worker` | ~1200 | 通道表、主题、后台线程等 |
| `services/export_report.py` | ~140 | 导出 CSV/TXT（无 Qt） |
| **合计（活跃 Python）** | ~9k | 含遗留 CTk |

```text
业务层（可 CLI/GUI 共用）
  hikvision_status · config_store · services/export_report
        ↑
  ScanWorker (Signal)  /  cli_report
        ↑
  ui/main_window + widgets（PySide6）
```

### 1.2 整体评价

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构边界 | ★★★★☆ | 业务与 GUI 分离清晰；Worker + Signal 正确 |
| 功能完整度 | ★★★★☆ | 档案/设备/快深巡检/循环覆盖/导出/大窗/主题已成型 |
| 可维护性 | ★★★☆☆ | 两处「巨石」文件，职责过重 |
| 工程质量 | ★★☆☆☆ | 缺自动化测试；双 UI 并存；入口脚本未落地 |
| 安全 | ★★☆☆☆ | 密码明文存 `profiles.json` |
| 发布就绪 | ★★★☆☆ | 代码可用；真机 + 双平台包仍待验收 |

**结论：** 产品功能已够日常巡检；下一阶段优先 **拆模块、补测试、清技术债、真机/打包验收**，而非继续堆功能。

### 1.3 已有优势（保持）

1. 业务与 UI 解耦：`HikvisionNVR` / 导出可 CLI、GUI 共用  
2. 线程模型正确：后台线程 + Signal；取消走 `ScanCancelled`  
3. 通道表 Model/View：排序、筛选、复制、详情可扩展  
4. 配置与用户数据路径兼容 v1  
5. 近期体验打磨有方向（侧栏、表单、日志/预警、循环覆盖）

---

## 2. 问题清单（按优先级）

### 2.1 P0 — 发布 / 稳定性

| ID | 问题 | 现状 | 建议动作 |
|----|------|------|----------|
| P0-1 | 真机 NVR 未闭环验收 | 文档标明待做 | 固定 1–2 台设备：快速 + 深度各一次 |
| P0-2 | 双平台打包未实测 | spec/脚本已写 | 跑 `build_mac.sh` / `build_win.ps1`，记体积与冷启动 |
| P0-3 | 进度/日志信号未节流 | 深抽检可能刷 UI | 50–100ms 合并 emit，或主线程 QTimer 批量刷新 |
| P0-4 | 密码明文存储 | `profiles.json` | 短期文档+权限；中期系统 keyring |

### 2.2 P1 — 可维护性

| ID | 问题 | 现状 | 建议动作 |
|----|------|------|----------|
| P1-1 | `main_window.py` 过大 | ~2000 行 | 拆 results / device_list / scan_settings / log_panel |
| P1-2 | `hikvision_status.py` 过大 | ~2200 行 | 拆 isapi / storage / recording / av_probe / health |
| P1-3 | 双 GUI 并存 | ✅ 已删除 `gui_app.py`（B4） | 归档到 `legacy/` 或删除，避免改漏 |
| P1-4 | 无自动化测试 | 仅手测/临时冒烟 | pytest + pytest-qt（offscreen） |
| P1-5 | CLI 与 GUI 巡检编排分叉 | `scan_worker` vs `cli_report.gather` | 抽公共 `scan_runner`（无 Qt） |

### 2.3 P2 — 体验与工程质量

| ID | 问题 | 建议动作 |
|----|------|----------|
| P2-1 | 主题切换后预警 HTML 不刷新 | `_sync_theme_widgets` 用 `_last_result` 重绘 |
| P2-2 | 通道详情多窗口叠加 | 单例复用或模态 QDialog |
| P2-3 | `nvr-gui` 入口未安装 | `[build-system]` 或 `tool.uv.package = true` |
| P2-4 | QSS 全在 f-string | 拆 `ui/resources/*.qss` 模板 |
| P2-5 | 循环覆盖「推断」误报风险 | UI 标明推断；真机抓包校准字段 |
| P2-6 | 窗口无最小尺寸 | 如 `setMinimumSize(1000, 700)` |
| P2-7 | 侧栏展开滚动条裁 2px | 加大侧栏 min 宽度余量 |
| P2-8 | 详情窗/主题色新建时才采样 | 打开时读 `effective_dark()` |

---

## 3. 分阶段计划

### 阶段 A — 短期（1–2 周，发布前 / 高收益）

> **目标：** 可发布、深抽检不卡、有最小回归网。

| 序号 | 任务 | 对应 ID | 验收标准 |
|------|------|---------|----------|
| A1 | 进度与日志 50–100ms 节流 | P0-3 | 64 路深度巡检主界面无明显掉帧 |
| A2 | 真机回归（快 + 深） | P0-1 | 进度、预警、通道表、导出与 CLI 一致 |
| A3 | Mac / Win 打包验收 | P0-2 | 冷启动、图标、无 console、ffmpeg 捆绑可用 |
| A4 | 最小 pytest 集 | P1-4 | export、channel filter、overwrite 解析、lookback 换算、cancel |
| A5 | 主题切换重绘预警 | P2-1 | 切换亮/暗后预警色正确 |
| A6 | 废弃路径明确 | P1-3, P2-3 | 文档只推 `run_gui.py` 或修好 `nvr-gui`；legacy 标注 |
| A7 | 密码风险说明加强 | P0-4 | README/USAGE 安全段；可选目录权限提示 |

**阶段 A 完成定义（DoD）：**  
同事可安装包使用；核心路径有自动化保护；深抽检 UI 可接受。

---

### 阶段 B — 中期（拆分与产品债）

> **目标：** 可维护、可扩展多设备与历史报告。

| 序号 | 任务 | 对应 ID | 说明 |
|------|------|---------|------|
| B1 | 拆 `main_window` | P1-1 | `ui/panels/`：左配置、结果、日志；主窗只组装 |
| B2 | 拆 `hikvision_status` | P1-2 | `nvr_core/`：client、storage、recording、av、health |
| B3 | 公共 `scan_runner` | P1-5 | CLI 与 GUI 共用编排，减少逻辑分叉 |
| B4 | 删除或迁出 `gui_app.py` | P1-3 | 确认无引用后移除 |
| B5 | 多设备队列巡检 | PLAN §4.3 | 顺序/有限并发一键巡检 |
| B6 | 历史报告列表 | PLAN §4.3 | 结果归档 JSON + 列表查看/再导出 |
| B7 | 凭证 keyring | P0-4 | macOS Keychain / Windows Credential Manager |
| B8 | 窗口最小尺寸 + 详情窗单例 | P2-2, P2-6 | 小屏不崩布局；双击不刷屏 |

**阶段 B 完成定义：**  
单文件 < ~800 行主模块；新功能可落在独立包；密码不再默认明文。

---

### 阶段 C — 长期（产品化）

| 序号 | 任务 | 说明 |
|------|------|------|
| C1 | 代码签名 / 公证 | Mac Gatekeeper、Win SmartScreen |
| C2 | CI 双平台构建 | GitHub Actions 矩阵 + artifact |
| C3 | 运行日志落盘 | 除崩溃外，按日滚动 `logs/` |
| C4 | QSS / 资源规范化 | qrc 或 importlib.resources；模板化主题 |
| C5 | 可选 i18n | 文案抽离（当前全中文硬编码） |
| C6 | 自动更新检查 | 内网版本源（按需） |
| C7 | ffmpeg 体验 | 版本检测、缺失引导 |

### 明确不做

- QML 重写  
- 应用内实时预览 / 播放器  
- asyncio + qasync 全盘替换  
- 与 CustomTkinter 长期双维护  
- 像素级复刻 v1 皮肤  

---

## 4. 目标架构（演进图）

```text
当前：
  hikvision_status.py（巨石）  ←── ScanWorker  ←── main_window.py（巨石）
  gui_app.py（遗留）✅ 已删除
  cli_report.py（并行编排）

目标：
  nvr_core/
    isapi_client.py       # session、_get、_parse、cache、cancel
    storage.py            # 硬盘、循环覆盖
    recording.py          # 计划、CMSearch 落盘
    av_probe.py           # ffmpeg 深度抽检
    health.py             # 健康规则（纯函数为主）
  services/
    export_report.py
    scan_runner.py        # 无 Qt 的巡检编排（CLI + GUI 共用）
  ui/
    main_window.py        # 组装 + 少量协调
    panels/               # left_config / results / log
    widgets/
    theme.py / resources/
  cli/                    # argparse + rich 报告
  # legacy/ 已不需要：gui_app.py 已于 B4 删除（git 历史可查）
```

**原则：**

1. 健康规则、导出、lookback 换算 → **纯函数 / 无 UI**，便于单测  
2. GUI 只负责：表单 ↔ options、结果 dict → 展示  
3. CLI 与 GUI **共用** `scan_runner`，避免两套业务语义  

---

## 5. 测试策略

### 5.1 优先覆盖（阶段 A）

| 用例 | 模块 | 说明 |
|------|------|------|
| 导出 CSV/TXT | `services/export_report` | 字段、utf-8-sig、空结果 |
| 通道筛选/排序 | `ui/widgets/channel_table` | 仅异常/仅离线、通道号排序 |
| 循环覆盖解析 | `hikvision_status` | XML 布尔/策略；推断分支 |
| lookback 单位 | `main_window` 或抽出工具函数 | 分/时/天 ↔ 分钟 |
| 取消巡检 | `ScanWorker` + `HikvisionNVR.cancel` | 置位后不再 emit finished |

### 5.2 环境

```bash
# 已落地（A4）：33 例最小回归网
QT_QPA_PLATFORM=offscreen uv run pytest

# 建议后续：pytest-qt（可选，提升 Qt 交互测试体验）
```

### 5.3 手工验收清单（发布）

- [ ] 快速巡检：在线/计划/落盘/硬盘/循环覆盖  
- [ ] 深度巡检：有/无 ffmpeg 降级；抽检日 + 繁忙时段  
- [ ] 导出 CSV / TXT；大窗通道表  
- [ ] 主题三态；重启后 geometry/splitter/theme 记忆  
- [ ] 取消巡检；崩溃日志路径可读  
- [ ] 安装包：Mac .app / Win 目录，图标与 ffmpeg  

---

## 6. 风险登记

| 风险 | 影响 | 缓解 |
|------|------|------|
| 深抽检多通道 UI 卡顿 | 体验差、误以为卡死 | A1 节流；日志已有 5000 行上限 |
| 循环覆盖推断误判 | 误报/漏报 | 文案区分确认/推断；真机校准 ISAPI |
| 明文密码泄露 | 安全事故 | B7 keyring；A7 文档提示 |
| 双入口改漏 | 行为不一致 | 删/归档 v1 |
| 无回归测试 | 改 UI 易回退 | A4 最小网 + CI 后续 |
| 拆分引入回归 | 发布延期 | 先 A 后 B；每拆一块跑真机快检 |

---

## 7. 任务看板（可勾选）

### 阶段 A

- [x] A1 进度/日志节流（80ms 合并，`scan_worker._SignalThrottle`）
- [ ] A2 真机快/深回归（需真实 NVR，未执行）
- [ ] A3 Mac/Win 打包验收（需目标平台，未执行）
- [x] A4 pytest 最小集（33 例：导出/筛选/覆盖/lookback/取消/节流）
- [x] A5 主题切换重绘预警（缓存 `_last_warn_lines`）
- [x] A6 废弃 CTk / 统一入口（`nvr-gui` 已可装；`gui_app.py` 已删除，见 B4）
- [x] A7 密码风险文档（README / USAGE §8.1）  

### 阶段 B

- [x] B1 拆 main_window（`ui/panels/`：left_panel/results_panel/log_panel；`main_window.py` 2008→584 行，只组装；保留兼容再导出）
- [x] B2 拆 hikvision_status（`nvr_core/`：util/isapi_client/storage/recording/av_probe/health/nvr；`hikvision_status.py` 降为兼容门面）
- [x] B3 scan_runner 共用（`nvr_core/scan_runner.py`：build_nvr/run_nvr/scan；GUI `_scan`、CLI `collect_status`、`nvr_from_args` 均委托）
- [x] B4 移除 legacy GUI（`gui_app.py` 2519 行 CTk 已删除；确认无引用：仅文档/docstring 提及，运行/构建/测试零引用）
- [x] B5 多设备队列（`nvr_core/scan_runner.py::scan_queue` 顺序遍历 + 整体进度 [i/N,(i+1)/N) 映射 + on_device 逐台回调；`ui/scan_worker.py::QueueScanWorker`；左面板目标下拉「全部设备」；`tests/test_queue.py` 4 例）
- [x] B6 历史报告（`services/history.py` 归档 JSON + 上限清理；`ui/widgets/history_dialog.py` 列表查看/再导出；主窗完成后自动归档；`tests/test_history.py` 6 例）
- [x] B7 凭证安全（`services/credentials.py`：macOS Keychain / Windows Credential Manager，其他平台回退明文；`ConfigStore.resolve_devices` 读取补全、`update_profile` 保存迁移；`tests/test_credentials.py` 6 例 + 真机验证）  
- [x] B8 最小窗 + 详情单例（`setMinimumSize(1000,700)`；`ChannelDetailDialog` 单例复用）

### 阶段 C

- [ ] C1 签名/公证  
- [ ] C2 CI  
- [ ] C3 运行日志落盘  
- [ ] C4 QSS/资源  
- [ ] C5 i18n（按需）  
- [ ] C6 自动更新（按需）  
- [ ] C7 ffmpeg 引导  

---

## 8. 建议执行顺序（开工用）

若资源有限，按此顺序推进：

```text
1. A1 节流          → 立刻改善深抽检体验
2. A4 最小测试      → 后续重构安全网
3. A2 + A3 验收     → 发布门槛
4. A5 / A6 / A7     → 体验与清理
5. B1 → B2 → B3     → 架构减负
6. B5 / B6 / B7     → 产品增强
7. C* 按商业需要    → 分发与合规
```

---

## 9. 文档维护

| 变更 | 更新位置 |
|------|----------|
| 完成某阶段任务 | 本文 §7 勾选 + DEVELOPMENT 状态 |
| 架构落地后 | 更新 DEVELOPMENT §1 目录结构 |
| 发布版本 | README / pyproject 版本号 + DEPLOYMENT 验收记录 |

**修订记录**

| 日期 | 说明 |
|------|------|
| 2026-08-02 | 初版：代码评估后整理优化计划 |
| 2026-08-02 | 执行阶段 A：A1/A4/A5/A6/A7 已完成；A2（真机）、A3（打包）待资源就绪 |
| 2026-08-02 | 执行阶段 B：B1/B2/B3/B4/B5/B6/B7/B8 全部完成，阶段 B 收官 |
| 2026-08-02 | **进度复核**：对照代码/测试独立验证；见下文 §10 |
| 2026-08-05 | **除虫/收尾**：Windows keyring 多设备 TargetName、keyring 写入失败不丢密、档案 rename/delete/clone 凭证生命周期、队列失败也归档、pyproject 补 `nvr_core`/`ui.panels`、文档同步 B7 |
| 2026-08-05 | **体积优化**：spec 过滤未用 Qt 框架/插件/翻译、排除 rich/CLI；`build_mac.sh --lite` / `build_win.ps1 -Lite`；macOS arm64 实测 full 172MB/72MB zip、lite 74MB/29MB zip（基线 204/86） |

---

## 10. 进度复核报告（对照代码，2026-08-02）

> 依据当前工作区实现与 `uv run pytest` 结果复核，不依赖口头完成声明。

### 10.1 总览

| 阶段 | 计划项 | 已完成 | 未完成 | 完成率 |
|------|--------|--------|--------|--------|
| **A 短期** | 7 | 5 | 2（A2 真机、A3 打包） | **~71%** |
| **B 中期** | 8 | 8 | 0 | **100%** |
| **C 长期** | 7 | 0 | 7 | **0%** |
| **合计** | 22 | 13 | 9 | **~59%** |

**自动化测试：** `uv run pytest` → **全部通过**（当前约 **48** 例，含导出/筛选/覆盖/lookback/取消/节流/队列/历史/凭证）。

### 10.2 已落地（有代码/测试佐证）

| 任务 | 证据 |
|------|------|
| **A1 节流** | `ui/scan_worker.py`：`_SignalThrottle`，默认 **80ms** 合并 log/progress |
| **A4 测试** | `tests/`：export、channel_table、overwrite、lookback、cancel、queue、history、credentials |
| **A5 主题预警** | `ui/panels/results_panel.py`：缓存 `_last_warn_lines`，主题切换重绘 HTML |
| **A6 入口/去 CTk** | `gui_app.py` 已删；`pyproject` 含 `nvr-gui` + `[build-system]` |
| **A7 密码文档** | README / USAGE 安全段（**注意：USAGE §8.1 仍写「明文存储」+「计划 keyring」，与 B7 实现不完全同步**） |
| **B1 拆主窗** | `ui/panels/{left,results,log}_panel.py`；`main_window.py` ~723 行（组装+协调） |
| **B2 拆业务** | `nvr_core/`：isapi_client、storage、recording、av_probe、health、nvr、util；`hikvision_status.py` 降为门面 ~187 行 |
| **B3 scan_runner** | `nvr_core/scan_runner.py`；CLI `collect_status` / GUI worker 委托 |
| **B4 删 CTk** | 运行路径无 `gui_app` 引用 |
| **B5 队列** | `scan_queue` + `QueueScanWorker` + `tests/test_queue.py` |
| **B6 历史** | `services/history.py` + `history_dialog.py` + `tests/test_history.py` |
| **B7 keyring** | `services/credentials.py` + ConfigStore 迁移/补全 + `tests/test_credentials.py` |
| **B8 窗体** | `setMinimumSize(1000,700)`；详情窗单例复用 |

### 10.3 架构是否达到 B 阶段 DoD

| 指标 | 目标 | 现状 | 结论 |
|------|------|------|------|
| 单文件主模块体量 | &lt; ~800 行 | main_window ~723；left_panel ~848；recording ~553 | **基本达成**（left_panel 略超，可再拆设置区） |
| 独立包落位 | nvr_core / panels / services | 已具备 | **达成** |
| 密码默认不落明文 | keyring 优先 | 支持平台写入 keyring，不可用时回退明文 | **达成（有回退）** |
| CLI/GUI 共用编排 | scan_runner | 已委托 | **达成** |

### 10.4 仍未完成 / 风险与缺口

| 项 | 状态 | 说明 |
|----|------|------|
| **A2 真机回归** | 未完成 | 计划中的发布阻塞项；需实机快/深巡检勾 §5.3 清单 |
| **A3 打包验收** | 未完成 | 需在目标机跑 build 脚本；记体积/冷启动/ffmpeg |
| **阶段 C 全部** | 未开工 | 签名、CI、运行日志落盘、QSS 资源化、i18n、更新、ffmpeg 引导 |
| **文档漂移** | ✅ 已修 | README / USAGE / DEVELOPMENT 已改为「优先 keyring，失败回退明文」 |
| **打包清单** | ✅ 已修 | `pyproject.toml` packages 含 `nvr_core`、`ui.panels` |
| **Windows 多设备凭证** | ✅ 已修 | TargetName 改为 `NVRStatus/{account}`，兼容读旧单槽 |
| **left_panel 体量** | 观察 | ~992 行，设置/设备表单可再拆（非阻塞） |

### 10.5 建议的下一步（按优先级）

1. **补文档：** 同步 USAGE/README 凭证说明与 B7 行为一致。  
2. **补 pyproject packages：** 加入 `nvr_core`、`ui.panels`（及必要时 `ui.widgets` 已有）。  
3. **A2 + A3：** 真机与安装包验收，关闭发布门槛。  
4. **按需启动 C2（CI）** 或 C3（运行日志落盘），其余 C* 视分发需求。  

### 10.6 结论

本次优化执行质量高：**阶段 B 按计划收官，阶段 A 除真机/打包外已完成**，测试网已从「几乎为零」提升到 **48 例全绿**。  
相对原计划，进度约为 **六成（13/22）**，其中 **工程重构与功能债（A+B 的代码项）已基本做完**；剩余主要是 **发布验收（A2/A3）与产品化（C）**，以及少量 **文档/打包配置收尾**。
