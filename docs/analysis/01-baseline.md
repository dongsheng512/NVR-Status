# 01 — 重写基线（v1 → PySide6 v2）

本文描述 **重写前** 的系统形态：哪些必须保留、哪些由 PySide6 替换、痛点如何在重写中消化。

---

## 1. 现行技术栈（v1.0.0）

| 层级 | 技术 | 重写后 |
|------|------|--------|
| 语言 | Python 3.11+ | 保持 |
| 包管理 | uv | 保持 |
| **GUI** | **CustomTkinter + ttk.Treeview** | **→ PySide6** |
| 表格依赖 | tksheet（未全面使用） | 移除 |
| HTTP / 设备 | requests + 海康 ISAPI | **保持** |
| 抽检 | ffmpeg / ffprobe | **保持** |
| CLI | rich + `nvr` / `cli_report` | **保持** |
| 打包 | PyInstaller（CTk/Tcl） | **重写 spec（Qt 插件）** |
| 配置 | `config_store` 用户目录 JSON | **保持兼容** |

---

## 2. 架构（须保持的边界）

```
┌─────────────────┐     ┌──────────────────┐
│  GUI（将重写）   │     │  CLI（不动）      │
│  现: gui_app.py │     │  nvr / cli_report │
│  新: ui/*       │     │                  │
└────────┬────────┘     └────────┬─────────┘
         │                       │
         ▼                       ▼
┌─────────────────────────────────────────┐
│  hikvision_status.HikvisionNVR          │  ← 复用
│  ISAPI · 录像 · 深度 AV                  │
└─────────────────────────────────────────┘
         │
         ▼
┌──────────────────┐
│  config_store    │  ← 复用（路径与 schema 兼容）
└──────────────────┘
```

**含义：** 换 UI 不等于重做项目；工期在壳与打包，不在协议。

---

## 3. 代码规模（约）

| 文件 | 行数 | 重写策略 |
|------|------|----------|
| `gui_app.py` | ~2500 | **整体替换**为 `ui/` 模块，不逐行翻译 |
| `hikvision_status.py` | ~1900 | 复用；可选加 cancel 检查点 |
| `cli_report.py` | ~600 | 不动 |
| `config_store.py` | ~260 | 不动 |
| 导出逻辑（现嵌在 GUI） | — | **抽出** `services/export_report.py` 供新旧共用 |

---

## 4. 现 GUI 能力（对等清单来源）

完整勾选见 [PLAN.md §4](../PLAN.md)。摘要：

- 多配置档案 CRUD / 导入导出  
- 多设备编辑、扫描目标选择  
- 扫描参数、快速/深度巡检、样片保存  
- 进度、指标卡、预警、通道表、日志  
- CSV/TXT 导出、打开样片目录  

数据协议：`ScanWorker` 类消息  
`log` / `progress` / `done` / `error`  
→ v2 映射为 Qt Signal，**语义对齐**，便于对照调试。

---

## 5. 现实现痛点 → 重写时消化

| 痛点 | v2 做法 |
|------|---------|
| Treeview 嵌 CTk，滚动/主题难 | `QTableView` + Model |
| `after` 轮询 queue | Signal/Slot |
| 单文件 2500 行 | `ui/` 分模块 |
| 字符串表单晚校验 | `QSpinBox` / Validator |
| 完成强弹窗打断 | 底栏为主，弹窗可弱化 |
| 打包绑 Tcl | 改绑 Qt 插件 + 精简模块 |

---

## 6. 兼容性承诺

| 项 | 承诺 |
|----|------|
| `profiles.json` | 同路径、同字段，用户档案无感升级 |
| `nvr_config.json` 导入 | 行为保持 |
| CLI | 继续可用，与 GUI 共用业务库 |
| 样片目录默认位置 | `app_data_dir()/av_samples` 不变 |
| 安装包名 | 仍可 `NVRStatus`（版本号 2.0.0） |

---

## 7. 相关

- 落地注意点与映射：[02-pyside6-notes.md](02-pyside6-notes.md)  
- 阶段计划：[../PLAN.md](../PLAN.md)  
