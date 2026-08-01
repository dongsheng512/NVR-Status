# UI 与架构分析文档

本目录汇总对 **当前技术栈**、**wxPython 重构**、**PySide6 对比选型**、**PySide6 落地注意点** 的评估结论，供计划与评审使用。

| 文档 | 内容 |
|------|------|
| [01-current-stack.md](01-current-stack.md) | 当前技术栈、架构、代码规模、痛点 |
| [02-wxpython-eval.md](02-wxpython-eval.md) | 使用 wxPython 重构的优缺点与可行度 |
| [03-wx-vs-pyside6.md](03-wx-vs-pyside6.md) | wxPython vs PySide6 项目级选型建议 |
| [04-pyside6-details.md](04-pyside6-details.md) | PySide6 注意细节与可改进项、迁移顺序 |

## 核心结论（一页纸）

```text
1. 当前 CustomTkinter 方案对「内网巡检工具 + 已通打包」足够用。
2. 业务（hikvision_status / config_store）与 UI 已分离，换 UI 壳技术可行。
3. 全量换框架默认 ROI 偏低；优先局部修表与工程化拆分。
4. 若必须离开 Tk：选 PySide6，不选 wxPython。
5. 迁移前强制 1–2 天 POC（真机 + 至少一平台打包）。
```

## 与计划的关系

路线图与里程碑见 [../PLAN.md](../PLAN.md)。  
部署与分发见 [../DEPLOYMENT.md](../DEPLOYMENT.md)。

## 修订记录

| 日期 | 说明 |
|------|------|
| 2026-08 | 初版：整理会话中的选型与 PySide6 分析入仓 |
