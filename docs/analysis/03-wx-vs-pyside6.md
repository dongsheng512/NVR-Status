# 03 — wxPython vs PySide6（项目级选型）

> 问题：若离开 CustomTkinter，本项目应选 wxPython 还是 PySide6？  
> 结论：**选 PySide6；默认仍是先不换框架。**

---

## 1. 项目约束映射

| 约束 | 含义 | 更贴谁 |
|------|------|--------|
| 给同事用的内网工具 | 顺手、观感现代即可 | **PySide6** |
| 现状 CTk 圆角卡片 + 深浅色 | 用户会对比观感 | **PySide6**（QSS） |
| 核心是通道表 + 汇总 + 日志 + 进度 | 中等表单 + 列表 | 两者都行；**Qt Model/View 更强** |
| 已有 PyInstaller 双平台 | 换栈都要重做 | Qt 资料/示例更多 |
| `gui_app` ~2500 行、业务已剥离 | 重写的是壳 | 看壳的长期价值 |
| 无深度系统壳定制 | 不需要极致 Cocoa/Win32 | wx 最大优势用不上 |
| 团队偏 Python 工具链 | 学习与示例 | **PySide6** 通常更密 |

---

## 2. 分项对比

### 2.1 产品观感

| | wxPython | PySide6 |
|--|----------|---------|
| 默认观感 | 系统原生、偏传统 | Fusion/QSS，易做现代工具 |
| 贴近当前 CTk | 难 | 较易 |
| 本项目倾向 | — | **PySide6** |

### 2.2 通道表

| | wx | Qt |
|--|-----|-----|
| 控件 | ListCtrl / DataView / Grid | QTableWidget / QTableView+Model |
| 排序筛选大量行 | 够用 | **更强、更标准** |
| 本项目倾向 | — | **PySide6** |

### 2.3 线程 / 进度

| | wx | Qt |
|--|-----|-----|
| 模式 | CallAfter / 事件 | Signal/Slot 或 CallAfter 类比 |
| 映射现有 Worker | 容易 | 容易；**信号语义更清晰** |
| 本项目 | 打平，略偏 Qt | |

### 2.4 打包与体积

| | wxPython | PySide6 |
|--|----------|---------|
| 体积 | 大 | 通常 **更大** |
| PyInstaller | 可行 | 可行；注意平台插件 |
| 本项目已捆绑 ffmpeg | 包本就不小，Qt 增量通常可接受 | |

体积优势不足以让 wx 赢过 Qt 的 UI/生态（对本项目）。

### 2.5 License

| | wx | PySide6 |
|--|-----|---------|
| 许可 | 宽松 | **LGPL**（动态链接常见；保留许可文本） |
| 注意 | — | 勿与 GPL 的 PyQt 混淆 |

内网工具一般可接受 LGPL；极敏感场景需单独确认。

### 2.6 学习与维护

- 两者对 CTk 用户都是新体系。  
- Qt 布局/样式/表格示例多，AI 辅助写迁移代码往往更顺。  
- 中长期多页（设置、历史、对比）Qt 更顺（`QTabWidget` / `QStackedWidget`）。

---

## 3. wx 更合适的条件（本项目目前不满足）

- 极致系统原生（菜单/打印/系统对话框完全跟 OS）  
- 团队已有大量 wx 代码与经验  
- 强烈排斥 Qt 体积与 LGPL  

当前仓库看不出以上硬需求。

---

## 4. 与「继续 CustomTkinter」的关系

| 目标 | 建议 |
|------|------|
| 修表格滚动/样式、简单筛选 | **留 CTk**，或上 tksheet（1–3 天） |
| 2 年内 UI 大改、强表格、品牌皮肤 | **PySide6** |
| 只想更像系统设置 App 且厌 QSS | 可考虑 wx |
| 不确定 | **先不换**；最多 1–2 天 PySide6 POC |

---

## 5. 最终建议

1. **默认：不切框架**，继续 CustomTkinter 交付。  
2. **若离开 Tk：选 PySide6，不要选 wxPython。**  
3. 用 **1–2 天 POC** 验证：主窗、ScanWorker、QTableWidget、进度日志、一平台 PyInstaller。  
4. POC 过关再投 2–4 周全量；否则继续 CTk。  

### 一句话

> 从本项目（现代工具 UI、结果表为主、双平台安装包、业务已剥离、无深度原生壳需求）出发：**升级框架选 PySide6；wx 不是更优解。最优解在无强触发条件时仍是不换栈。**

路线图落地见 [../PLAN.md](../PLAN.md) 阶段 C。  
PySide6 细节见 [04-pyside6-details.md](04-pyside6-details.md)。
