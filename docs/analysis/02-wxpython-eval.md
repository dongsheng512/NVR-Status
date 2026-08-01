# 02 — wxPython 重构 UI：优缺点与可行度

> 评估对象：本仓库 cam-gui / NVR Status v1.0.0  
> 对照基线：CustomTkinter + ttk.Treeview + PyInstaller  

---

## 1. 评估前提

| 维度 | 现状 |
|------|------|
| UI | CustomTkinter 现代皮肤 + 原生 Treeview |
| 架构 | UI 与 `HikvisionNVR` / `ConfigStore` 分离 |
| UI 复杂度 | 中高：档案、设备弹窗、折叠设置、指标卡、表、日志、进度 |
| 打包 | Win/Mac 已通路 |
| 用户 | 内网同事，安装即用 |

---

## 2. 优点

### 2.1 原生控件

wxPython 使用平台原生控件（macOS Cocoa / Windows Win32），菜单、对话框、列表滚动、焦点、高 DPI、无障碍通常更贴系统。

### 2.2 表格 / 列表

相对「CTk 壳 + ttk.Treeview」：

- `wx.ListCtrl` / `DataViewListCtrl` / `wx.grid.Grid` 更正统  
- 排序、多列、虚拟列表在大量通道时往往更省心  
- 减少两套滚动条/主题粘合  

### 2.3 布局

Sizer 体系对「左配置 + 右结果 + 底状态」表达清晰，折叠区、固定底栏较自然。

### 2.4 线程模型

`wx.CallAfter` / 自定义事件成熟，可映射现有 Worker + 消息协议。

### 2.5 桌面工具资料

传统企业桌面工具向文档较多（但近年整体热度不如 Qt/Web）。

---

## 3. 缺点与风险

### 3.1 重写成本

`gui_app.py` ~2500 行，功能对等迁移粗估：

| 范围 | 有 wx 经验 | 主要会 Tk/CTk |
|------|------------|----------------|
| 主窗与基本交互 | 3–5 人天 | 5–8 |
| 表/详情/导出/进度 | 2–4 | 4–6 |
| 打包双平台 | 2–4 | 3–5 |
| 真机回归 | 2–3 | 2–3 |
| **合计** | **约 1.5–2.5 周** | **约 3–4+ 周** |

### 3.2 视觉风格

默认更「系统原生管理端」，圆角卡片、统一现代感要额外做；**不保证比 CTk 更好看**。

### 3.3 打包

捆绑 wxWidgets，体积通常大于纯脚本、与现有 Tcl 路径不同；spec 与脚本需重做验证。

### 3.4 痛点可局部解决

Treeview 滚动/样式等问题可在 CTk 内修或换 tksheet，**不必为局部不爽全量迁 wx**。

### 3.5 与 Qt 对比

若目标是现代工具 UI + 强表格，社区与示例密度上 **PySide6 往往更占优**（见 03）。

---

## 4. 可行度评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 技术可行性 | 8/10 | 业务可复用，控件可映射 |
| 工期可控性 | 6/10 | UI 大、打包要重踩 |
| ROI（无强烈原生痛点） | 4/10 | 默认不划算 |
| ROI（表/原生已成交付瓶颈） | 7/10 | 可作候选，但仍建议对比 Qt |

**综合：** 技术可行、工程可控；**默认不建议全量换 wx**。

---

## 5. 控件映射（若仍要做）

| 现 CTk / Tk | wx |
|-------------|-----|
| `CTk` 主窗 | `wx.Frame` |
| `CTkScrollableFrame` | `ScrolledPanel` / `ScrolledWindow` |
| `CTkOptionMenu` | `wx.Choice` / `ComboBox` |
| `CTkEntry` / CheckBox / Button | 标准控件 |
| `CTkProgressBar` | `wx.Gauge` |
| `CTkTextbox` | `wx.TextCtrl` multiline readonly |
| `ttk.Treeview` | ListCtrl / DataView / Grid |
| `filedialog` / `messagebox` | FileDialog / MessageDialog |
| `after` 轮询 | `CallAfter` / Timer |

---

## 6. 若探索 wx 的稳妥路径

1. 冻结 `ScanWorker` 消息协议与 `ConfigStore` API  
2. **1–2 天 POC**：主窗 + 一键巡检 + 进度 + 简表 + 一平台打包  
3. 功能对等清单打勾  
4. 视觉策略：接受原生 或 轻量配色（勿一上来完美卡片）  
5. 短期可 `gui_wx.py` 与 CTk 并存，验证后再切默认入口  

---

## 7. 结论

- **能**用 wxPython 重构，业务层阻力小。  
- **难**在 UI 细节与双平台打包，不在 NVR 逻辑。  
- **默认不值得全量换**；更优默认是维持 CTk，或在换栈时优先评估 **PySide6**。  
