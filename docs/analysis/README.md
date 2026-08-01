# PySide6 重写 — 技术分析

GUI 已确定使用 **PySide6** 重写。本目录只保留与该决策相关的基线与落地分析。

| 文档 | 内容 |
|------|------|
| [01-baseline.md](01-baseline.md) | 重写基线：现有架构、须复用模块、功能与痛点 |
| [02-pyside6-notes.md](02-pyside6-notes.md) | PySide6 注意细节、控件映射、改进项、打包要点 |

**执行计划（阶段 / 里程碑 / 清单）：** [../PLAN.md](../PLAN.md)

## 决策摘要

```text
· 框架：PySide6（不用 wxPython，不再以 CTk 为 GUI 主线）
· 范围：重写 GUI + 打包；复用 hikvision_status / config_store / CLI
· 方式：新 ui/ 包 + Signal/Slot；通道表走 Model/View
· 节奏：POC 关卡 → 功能对等 → 体验 → 双平台 v2.0.0
```

## 修订记录

| 日期 | 说明 |
|------|------|
| 2026-08 | 初版含 wx 对比等多文档 |
| 2026-08 | **精简为仅 PySide6 相关**；wx 文档移除 |
