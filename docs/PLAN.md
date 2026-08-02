# PySide6 GUI 重写计划

> **决策：** GUI 从 CustomTkinter 全面重写为 **PySide6**。  
> 业务层（`hikvision_status` / `config_store` / CLI）**复用、不推倒**。  
> 基线版本：1.0.0（CTk）→ 目标版本：**2.0.0（PySide6）**  
> 技术细节：[analysis/](analysis/) · 部署：[DEPLOYMENT.md](DEPLOYMENT.md)

---

## 1. 目标

| 项 | 内容 |
|----|------|
| 产品 | NVR Status — 海康 NVR 状态巡检 + 可选深度音视频抽检 |
| 重写范围 | **仅 GUI 壳** + 打包链路；CLI 保持可用 |
| 功能对等 | v1 全部 GUI 能力在 v2 可用（见 §4 清单） |
| 顺带改进 | 模块拆分、Signal 进度、Model/View 通道表、表单校验、崩溃日志 |
| 交付 | Win / Mac 安装包（PyInstaller + PySide6） |
| 非目标（本阶段） | 实时预览、多厂商、云端；QML；与 CTk 长期双 UI |

---

## 2. 原则

1. **业务不动：** `HikvisionNVR`、`ConfigStore`、导出数据格式、用户数据目录路径保持兼容。  
2. **先骨架后皮肤：** POC → 功能对等 → 体验打磨 → 打包；不为 1:1 复刻 CTk 圆角拖期。  
3. **新目录落位：** 新 GUI 放 `ui/`，旧 CTk `gui_app.py` 已于 v2 切换默认入口后删除（B4）。  
4. **信号驱动：** 废弃 `queue` + `after` 轮询，改用 Qt Signal/Slot。  
5. **双平台验收：** 功能完成不算完，Mac + Win 包 + 真机 NVR 才算里程碑。  

---

## 3. 目标技术栈（v2）

| 层级 | 技术 |
|------|------|
| 语言 | Python ≥ 3.11 |
| 包管理 | uv |
| GUI | **PySide6** |
| 业务 | requests + ISAPI（现有） |
| 抽检 | ffmpeg / ffprobe（现有） |
| CLI | rich + 现有脚本（不变） |
| 打包 | PyInstaller（重写 spec / 脚本，处理 Qt 平台插件） |
| 配置 | 现有 `config_store` 用户目录 JSON |

依赖变化（示意）：

```toml
# 新增
"PySide6~=6.8.0"
# 移除（GUI 切换完成后）
# customtkinter, tksheet
```

---

## 4. 功能对等清单（验收用）

### 4.1 必须（P0）

- [x] 主窗布局：左配置 / 右结果 / 底状态栏  
- [x] 配置档案：新建、另存、重命名、删除、导入、导出、切换  
- [x] 设备：列表、添加/编辑对话框、删除、保存到档案  
- [x] 扫描目标设备选择  
- [x] 扫描设置（可折叠或分页）：回溯、workers、深度参数、保存路径等  
- [x] 快速巡检 / 深度巡检；深度可选保存片段  
- [x] 无 ffmpeg 时提示并可降级  
- [x] 后台巡检不卡 UI；进度条 + 阶段文案  
- [x] 结果：汇总、指标卡、预警、通道表、日志  
- [x] 导出 CSV（utf-8-sig）/ TXT  
- [x] 打开样片目录  
- [x] 用户数据路径与 v1 兼容（同一 `profiles.json`）  

### 4.2 建议随重写完成（P1）

- [x] 通道表排序、筛选（仅异常 / 仅离线）  
- [x] 通道详情（可同一 model，不必第二套数据）  
- [x] 日志分级着色 + 行数上限 + 复制  
- [x] 浅色 / 深色 / 跟随系统  
- [x] 窗口与 Splitter 比例 `QSettings` 记忆  
- [x] 崩溃/异常写入 `app_data_dir()/logs/`  
- [x] 巡检取消（cancel flag + UI 按钮）  

### 4.3 可二期（P2）

- [x] 多设备队列一键巡检（B5：`scan_runner.scan_queue` 顺序遍历 + 整体进度映射；GUI 目标下拉「全部设备」）  
- [x] 历史报告列表（B6：`services/history.py` 归档 JSON + `HistoryDialog` 列表查看/再导出）  
- [x] 密码安全存储（B7：`services/credentials.py` macOS Keychain / Windows Credential Manager；不可用时回退明文）  
- [ ] 历史报告列表  
- [ ] 完成通知策略（少弹窗）  
- [ ] 代码签名 / 公证  

---

## 5. 目标目录结构（建议）

```text
cam-gui/
├── hikvision_status.py      # 不动（或仅加 cancel 钩子）
├── config_store.py          # 不动
├── cli_report.py / nvr      # 不动
├── services/                # 可选：从 GUI 抽出的纯逻辑
│   └── export_report.py     # CSV/TXT 导出
├── ui/                      # 新建：PySide6 GUI
│   ├── __init__.py
│   ├── app.py               # QApplication 入口
│   ├── main_window.py
│   ├── scan_worker.py       # QObject/Thread + Signals
│   ├── theme.py
│   ├── widgets/
│   │   ├── channel_table.py
│   │   ├── device_editor.py
│   │   ├── profile_bar.py
│   │   └── status_bar.py
│   └── resources/           # 可选 QSS
├── run_gui.py               # 启动 ui.app
├── NVRStatus.spec           # PyInstaller 规格（PySide6）
└── build/                   # 更新脚本
```

命名可微调，原则：**业务在根或 services，UI 集中在 `ui/`**。

---

## 6. 阶段与里程碑

### 阶段 0 — 准备（0.5 天）

| 项 | 说明 |
|----|------|
| 0.1 | 依赖增加 `PySide6`，`uv lock` |
| 0.2 | 建立 `ui/` 空包与 `run_gui` 可切换入口（环境变量或临时改 import） |
| 0.3 | 确认测试 NVR、ffmpeg、Win/Mac 构建机 |

**完成标准：** `uv run python -c "import PySide6; print(PySide6.__version__)"` 通过。

---

### 阶段 1 — POC（1–2 天）**【关卡】**

最小可用切片，验证线程与打包可行性。

| 项 | 说明 |
|----|------|
| 1.1 | `MainWindow`：设备下拉 + 开始按钮 + 日志 + 进度条 |
| 1.2 | `ScanWorker`：复用 `HikvisionNVR`，Signal 发 log/progress/done/error |
| 1.3 | 简表 `QTableWidget` 展示通道摘要 |
| 1.4 | 真机 quick 巡检通过 |
| 1.5 | （可选）单平台 PyInstaller 能启动窗口 |

**完成标准：** 真机一次完整快速巡检；UI 不卡死。  
**失败则：** 暂停重写，复盘线程/环境问题，不进入阶段 2。

---

### 阶段 2 — 功能对等（约 1.5–2.5 周）

| 项 | 说明 |
|----|------|
| 2.1 | 档案 CRUD + 导入导出 |
| 2.2 | 设备列表与编辑对话框（校验 IP/端口） |
| 2.3 | 扫描设置 UI 与 `scan_options` 读写 |
| 2.4 | 快速 / 深度 / 保存片段 / ffmpeg 检测 |
| 2.5 | 结果区：指标卡、预警、通道表、详情 |
| 2.6 | 导出 CSV/TXT（优先抽 `services/export_report.py`） |
| 2.7 | 打开样片目录、状态栏完成态 |
| 2.8 | 对照 §4.1 全量打勾 |

**完成标准：** §4.1 全部完成；与 v1 同设备同参数结果一致（允许展示差异）。

---

### 阶段 3 — 体验与结构（约 3–5 天，可与 2 尾部重叠）

| 项 | 说明 |
|----|------|
| 3.1 | `QTableView` + Model + 筛选排序（P1） |
| 3.2 | 主题浅/深/系统；状态色统一 |
| 3.3 | `QSplitter` + `QSettings` |
| 3.4 | 日志封顶、复制；取消巡检 |
| 3.5 | 全局异常钩子 + 日志文件 |

**完成标准：** §4.2 主要项完成；无阻塞级体验问题。

---

### 阶段 4 — 打包与发布 v2.0.0（约 3–5 天）

| 项 | 说明 |
|----|------|
| 4.1 | 重写 `NVRStatus.spec`：PySide6 插件、精简 excludes、图标 |
| 4.2 | 更新 `build_mac.sh` / `build_win.ps1` |
| 4.3 | 捆绑 ffmpeg 回归 |
| 4.4 | 体积与冷启动记录；LGPL 许可文本附入包 |
| 4.5 | 按 [DEPLOYMENT.md](DEPLOYMENT.md) 验收清单双平台勾选 |
| 4.6 | 默认入口切到 PySide6；文档改技术栈描述 |
| 4.7 | （已完成）归档或删除 `gui_app.py` CTk 实现（B4 已删除） |

**完成标准：** Win + Mac 安装包内网可分发；DEPLOYMENT 验收通过。

---

### 阶段 5 — 二期增强（重写后，按需）

见 §4.3：多设备队列、历史报告、通知策略、签名等。  
**不阻塞 v2.0.0 发布。**

---

## 7. 里程碑时间线（量级）

```text
M0  准备完成，PySide6 可 import
M1  POC 真机通过                    [阶段 1]     ~1–2 天
M2  功能对等 §4.1                   [阶段 2]     ~1.5–2.5 周
M3  P1 体验 + 结构                  [阶段 3]     ~3–5 天
M4  v2.0.0 双平台包发布             [阶段 4]     ~3–5 天
M5  二期产品增强                    [阶段 5]     按需
```

**合计（M1–M4）：约 3–5 周**（视是否全职、是否双平台一手包）。

---

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 工作线程碰 UI | 崩溃/花屏 | 仅 Signal 回主线程；代码评审盯这一点 |
| PyInstaller 缺平台插件 | 包无法启动 | 阶段 1/4 尽早打测试包 |
| 包体积过大 | 分发不便 | excludes 无用 Qt 模块；记录基线 |
| 进度信号过密 | UI 卡顿 | 进度节流（如 50ms） |
| 与 v1 配置不兼容 | 用户档案丢失 | 不改 `profiles.json` schema |
| 重写中途需求膨胀 | 延期 | P2 严格二期；P0 锁清单 |
| LGPL | 合规 | 动态链接 + 附带许可 |

---

## 9. 工作项看板（执行用）

### 进行中 / 待办

| ID | 工作项 | 阶段 | 状态 |
|----|--------|------|------|
| W0 | 添加 PySide6 依赖与 `ui/` 骨架 | 0 | 完成 |
| W1 | ScanWorker + Signals | 1 | 完成 |
| W2 | MainWindow POC + 真机 | 1 | 完成（POC 构建通过；真机待验收） |
| W3 | 档案与设备 UI | 2 | 完成 |
| W4 | 扫描设置与双模式巡检 | 2 | 完成 |
| W5 | 结果区与导出 | 2 | 完成 |
| W6 | Model/View 表与主题 | 3 | 完成 |
| W7 | 打包 spec 与双平台 | 4 | 完成（spec/脚本已更新；待打包验收） |
| W8 | 文档/入口切换/发版 | 4 | 完成 |

状态请在实施时改为：`进行中` / `完成`。

---

## 10. 明确不做什么（本重写周期）

- 不引入 wxPython / 不双轨维护第三套 GUI  
- 不把 CLI 改成 Qt  
- 不重写 ISAPI 业务（除非 cancel/小钩子）  
- 不做 QML、不做应用内视频播放器  
- 不在未完成 P0 前启动大范围 P2  

---

## 11. 相关文档

| 文档 | 用途 |
|------|------|
| [analysis/README.md](analysis/README.md) | PySide6 分析索引 |
| [analysis/01-baseline.md](analysis/01-baseline.md) | 重写基线：现有架构与须保留能力 |
| [analysis/02-pyside6-notes.md](analysis/02-pyside6-notes.md) | 注意点、控件映射、改进项 |
| [DEPLOYMENT.md](DEPLOYMENT.md) | 部署与验收（发版时按此执行；构建节将随 v2 更新） |
| [../USAGE.md](../USAGE.md) | 用户使用说明（发版后更新 GUI 截图/描述） |

---

## 12. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-08 | 初版计划偏「可选用 PySide6」 |
| 2026-08 | **改为以 PySide6 重写为主计划**；删除「默认留 CTk」路线 |
