# 04 — PySide6：注意细节与可改进点

> 适用：已决定评估或实施 PySide6 迁移时阅读。  
> 基线：当前 `gui_app.py` ScanWorker + queue + Treeview + PyInstaller。

---

## 1. 必须盯住的细节

### 1.1 线程与 UI 更新（最高优先级）

| 点 | 说明 |
|----|------|
| 禁止 | 工作线程直接改 Qt 控件 |
| 推荐 A | 保留 `ScanWorker`，用 Signal / `QMetaObject.invokeMethod` 回主线程 |
| 推荐 B | `QObject` + `moveToThread(QThread)`，进度/日志 Signal→Slot |
| 替换 | `_poll_queue` + `after(100)` → 事件驱动 |

建议信号集合：

```text
log_line(str)
progress(dict)      # 对齐现有 phase/current/total/overall/msg
scan_failed(str)
scan_finished(dict)
```

取消巡检：独立 cancel flag；勿与 QThread 内部 `_stop` 等符号冲突。  
进度可 **50ms 节流**，避免深抽检刷爆主线程。

---

### 1.2 打包体积与 PyInstaller

| 点 | 说明 |
|----|------|
| 体积 | 通常比 CTk+Tcl **更大**；用 excludes 去掉 WebEngine/3D/Multimedia 等 |
| 平台插件 | Win：`platforms/qwindows`；Mac：`platforms/qcocoa`。缺失会报 no Qt platform plugin |
| 收集方式 | 用 PySide6 官方/社区推荐 hook；勿只抄 CTk 的 `collect_all` |
| UPX | 对 Qt 库可能有问题；异常时先关 UPX |
| 高 DPI | macOS Info.plist 保留 `NSHighResolutionCapable`；勿照搬过时 Qt5 缩放开关 |
| 图标 | `QIcon` + spec 的 icns/ico 双设 |
| 无 console | 保持 `console=False`；必须另有崩溃日志路径 |

`NVRStatus.spec`、`build_mac.sh`、`build_win.ps1` 需按 Qt **重写验收**。

---

### 1.3 平台行为

| 平台 | 注意 |
|------|------|
| macOS | 原生菜单栏可选；未签名仍要 `xattr -cr` |
| Windows | 中文字体、高 DPI、SmartScreen |
| 路径 | 复用 `config_store.app_data_dir()`，或统一 `QStandardPaths`，避免两套 |
| ffmpeg | 保持现有 `_MEIPASS` / 旁路 `bin/` 查找逻辑 |

---

### 1.4 控件映射

| 当前 | PySide6 建议 |
|------|----------------|
| `CTkScrollableFrame` | `QScrollArea` + layout |
| 折叠扫描设置 | `QGroupBox` / `QToolBox` / Tab「基本|深度」 |
| `ttk.Treeview` | **`QTableWidget`（快）或 `QTableView`+Model（推荐中长期）** |
| 行色 ok/bad/warn | `ForegroundRole` / Delegate |
| 日志 `CTkTextbox` | `QPlainTextEdit` + 格式；**行数上限** |
| 进度 | `QProgressBar`；忙碌态 `setRange(0,0)` |
| 对话框 | `QMessageBox` / `QFileDialog` / `QDialog`+`QFormLayout` |
| 指标卡 | `QFrame` + QSS |
| 密码 | `QLineEdit.EchoMode.Password` |

---

### 1.5 主题与中文

- 自建「浅色 / 深色 / 跟随系统」；颜色常量集中，对齐现有 `_status_palette`  
- 一份 QSS 或 QPalette；避免散落魔法色值  
- 默认字体跨平台回退（Windows 微软雅黑 UI 等）  

---

### 1.6 License

- **PySide6 = LGPL**  
- 常见：动态链接、附带 LGPL 许可文本  
- 与 **PyQt（GPL/商业）** 区分，勿混用  

---

### 1.7 依赖

```toml
# 示意
dependencies = [
  "requests>=2.32",
  "rich>=15",
  "PySide6~=6.8.0",  # 固定次版本更稳
]
# 移除 customtkinter / tksheet（确认无引用后）
```

- 固定次版本，降低插件路径差异  
- 同进程不要混用 Tk 与 Qt  

---

## 2. 建议随迁移做的改进

### 2.1 拆分巨型 GUI

| 模块 | 职责 |
|------|------|
| `scan_worker.py` | 后台巡检 + 信号 |
| `main_window.py` | 主布局 |
| `widgets/channel_table.py` | 通道表 |
| `widgets/device_editor.py` | 设备对话框 |
| `widgets/status_bar.py` | 底栏 |
| `services/export.py` | CSV/TXT（零 Qt） |
| `theme.py` | 颜色 / QSS |

### 2.2 Model/View 通道表

- `ChannelTableModel` + `QTableView`  
- `set_records(records, deep)`  
- `QSortFilterProxyModel`：仅异常 / 仅离线  
- 详情窗与主表共享 model，避免双份数据  

### 2.3 表单强类型

- 端口、workers、lookback → `QSpinBox`  
- silence_db → `QDoubleSpinBox`  
- IP → Validator  
- `ScanOptions` dataclass ↔ UI 绑定  

### 2.4 交互

| 现状 | 可改进 |
|------|--------|
| 完成必弹窗 | 底栏 + 可选系统通知 |
| 快速/深度双按钮 | 主按钮 + 模式选择 |
| 日志与表抢高度 | `QSplitter` + `QSettings` 记比例 |
| 单设备扫描 | 预留多设备队列接口 |

### 2.5 健壮性

- `sys.excepthook` → `app_data_dir()/logs/`  
- 长 traceback：摘要弹窗 + 全文写日志  
- ffmpeg 检测结果缓存  
- 取消深度巡检（业务循环检查 cancel）  

### 2.6 导出

- 现有 CSV utf-8-sig / TXT **原样抽出**可单测  
- 导出后 `QDesktopServices` 打开目录  
- 表格复制选中行 / 仅异常  

---

## 3. 不要做的过度设计

| 项 | 原因 |
|----|------|
| QML / 重度自绘 | ROI 低 |
| 内嵌实时预览（Multimedia） | 打包与依赖重；落盘够用 |
| asyncio + qasync | 现有线程足够 |
| 1:1 像素复刻 CTk | 拖工期 |
| 长期双 UI（CTk+Qt）并行 | 仅 POC 期短暂允许 |

---

## 4. 推荐迁移顺序

```text
1. 抽出 ScanWorker + 导出/格式化（无 Qt，可测）
2. 最小主窗：设备 + 开始 + 日志 + 进度 + 简表
3. 真机 quick / deep
4. 档案 CRUD、设备对话框、导出、打开样片目录
5. Model/View + 筛选排序 + 详情
6. 主题 / QSplitter / QSettings
7. PyInstaller 双平台 + ffmpeg 回归
8. 下线 CTk 入口与依赖
```

### 验收清单

- [ ] 快速巡检进度与完成态  
- [ ] 深度抽检 + 保存片段 + 打开目录  
- [ ] 无 ffmpeg 降级交互  
- [ ] 多档案导入导出；密码不在安装包  
- [ ] 通道着色；深浅色  
- [ ] CSV Excel 可开（utf-8-sig）  
- [ ] Mac/Win 冷启动；无 console 时有日志  

---

## 5. 风险与收益

| 维度 | 评价 |
|------|------|
| 技术风险 | 中：线程与打包主雷；业务层低 |
| 工期 | 对等迁移约 2–4 周；含表结构改进 + 数天 |
| 最大收益 | 表格体验、Signal 进度、可维护性、扩展空间 |
| 最大成本 | 包体、LGPL、打包回归、放弃已调 CTk 细节 |

---

## 6. 依赖变更示意（迁移后）

| 移除 | 新增 |
|------|------|
| customtkinter | PySide6 |
| tksheet（若不用） | （可选）无 |

业务依赖保持：`requests`、`rich`；打包：`pyinstaller` + Qt 插件处理。

---

## 7. 一句话清单

**注意：** 主线程更新 UI、Signal 替 queue 轮询、平台插件与体积精简、LGPL、暗色/字体/DPI、配置路径与 ffmpeg 查找一致。  

**改进：** 拆分 gui、Model/View 表、SpinBox 校验、进度节流与日志封顶、QSettings、取消巡检、崩溃日志；导出与 HikvisionNVR 不动。  

**不要：** 为圆角硬刚、上 WebEngine/QML、双 GUI 长期并行。  

计划中的阶段划分见 [../PLAN.md](../PLAN.md) 阶段 C。
