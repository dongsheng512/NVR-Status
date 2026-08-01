# 02 — PySide6 落地注意点与改进项

重写实施时的技术备忘。执行顺序与里程碑见 [../PLAN.md](../PLAN.md)。

---

## 1. 线程与 UI（最高优先级）

| 规则 | 说明 |
|------|------|
| 禁止 | 工作线程直接操作任何 QWidget |
| 推荐 | `ScanWorker(QObject)` + `moveToThread`，或 `threading` + 仅 `Signal.emit` |
| 信号 | `log_line` / `progress` / `scan_failed` / `scan_finished` |
| 进度 | 建议 50ms 节流，避免深抽检刷爆主线程 |
| 取消 | 独立 `threading.Event` / 原子标志；业务循环检查；勿覆盖 QThread 内部名 |

对齐 v1 的 `progress` 字段：`msg`、`phase`、`current`、`total`、`overall`，便于复用阶段插值逻辑。

---

## 2. 控件与结构映射

| v1（CTk/Tk） | v2（PySide6） |
|--------------|----------------|
| `CTk` 主窗 | `QMainWindow` |
| 左栏 `CTkScrollableFrame` | `QScrollArea` + `QWidget` + layout |
| 扫描设置折叠 | `QGroupBox` / `QToolBox` / `QTabWidget` |
| `CTkOptionMenu` | `QComboBox` |
| `CTkEntry` / CheckBox / Button | 标准控件 |
| 数值字符串 | `QSpinBox` / `QDoubleSpinBox` |
| `CTkProgressBar` | `QProgressBar` |
| 日志 `CTkTextbox` | `QPlainTextEdit`（只读 + 格式 + 行数上限） |
| `ttk.Treeview` | **`QTableView` + `QAbstractTableModel`**（首选） |
| 行 tag 着色 | `ForegroundRole` / `BackgroundRole` / Delegate |
| `filedialog` / `messagebox` | `QFileDialog` / `QMessageBox` |
| 设备弹窗 | `QDialog` + `QFormLayout` + `QDialogButtonBox` |
| `after` | Signal 或 `QTimer` |

---

## 3. 建议模块划分

```text
ui/
  app.py              # QApplication、全局字体、异常钩子
  main_window.py      # 布局拼装、槽函数
  scan_worker.py      # 后台巡检 + Signals
  theme.py            # 调色板、QSS、亮暗
  widgets/
    channel_table.py  # Model + View + 筛选
    device_editor.py
    profile_bar.py
    status_bar.py
services/
  export_report.py    # CSV/TXT，无 Qt 依赖，可单测
```

---

## 4. 通道表（重写核心收益）

```text
ChannelTableModel(QAbstractTableModel)
  set_records(records: list[dict], deep: bool)
QSortFilterProxyModel
  仅异常 / 仅离线 / 文本搜索（可选）
QTableView
  排序、选中复制、双击详情
```

深度巡检列随 `deep` 显示或留空；详情窗尽量共享同一 model。

---

## 5. 主题与中文

- 提供：浅色 / 深色 / 跟随系统（`Qt.ColorScheme` 或样式钩子）  
- 状态色（ready/running/ok/warn/error）集中在 `theme.py`  
- 默认字体跨平台回退（如 Windows「Microsoft YaHei UI」）  
- 勿散落硬编码色值  

---

## 6. 打包（PyInstaller）

| 点 | 说明 |
|----|------|
| 平台插件 | Win `qwindows`；Mac `qcocoa` — 缺失则无法启动 |
| 精简 | `excludes`：`QtWebEngine*`、`Qt3D*`、`QtBluetooth`、`QtMultimedia` 等本项目不用模块 |
| UPX | 出问题先关闭 |
| 图标 | icns/ico + 窗口 `QIcon` |
| console | 正式包 `False`；调试可临时 `True` |
| 许可 | 包内附 PySide6/Qt **LGPL** 相关文本 |
| ffmpeg | 继续 `bin/` 捆绑逻辑；与 Qt 无关但需回归 |
| 脚本 | 更新 `build_mac.sh` / `build_win.ps1`，安装 `PySide6` 而非仅 customtkinter |

---

## 7. License（LGPL）

- PySide6 为 LGPL：闭源内网工具常见做法为 **动态链接** Qt，并允许用户替换库。  
- 分发包内保留许可证文件。  
- 不要与 GPL 版 PyQt 混用。  

---

## 8. 随重写应做的改进（非可选建议）

| 改进 | 原因 |
|------|------|
| 抽出导出逻辑 | 可测、CLI/GUI 共用 |
| 表单 SpinBox | 减少参数错误 |
| 日志封顶 | 深扫日志暴涨拖垮 TextEdit |
| 崩溃日志目录 | 无 console 包可诊断 |
| 取消巡检 | 深扫耗时长，必备 |
| QSettings | 窗体大小、分割条、主题 |

---

## 9. 明确不做

- QML、应用内实时视频预览  
- asyncio + qasync（除非有强需求）  
- 与 CTk 长期双 UI  
- 为像素级复刻 CTk 皮肤无限打磨  

---

## 10. 依赖示意

```toml
dependencies = [
  "requests>=2.32",
  "rich>=15",
  "PySide6~=6.8.0",
]
# 移除: customtkinter, tksheet（入口切换并回归后）
```

---

## 11. 实施检查摘录

POC：

- [ ] 工作线程零直接碰控件  
- [ ] 真机 quick 完成  
- [ ] （可选）单平台包能启动  

发版前：

- [ ] §P0 功能对等  
- [ ] 档案与 v1 兼容  
- [ ] Win + Mac 包 + ffmpeg  
- [ ] 无 console 时错误可查日志  

完整清单见 [PLAN.md](../PLAN.md)。
