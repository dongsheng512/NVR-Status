# 文档目录

| 文档 | 说明 |
|------|------|
| [DEVELOPMENT.md](DEVELOPMENT.md) | **开发交接**：重写摘要、当前状态、已知问题、优化方向 |
| [PLAN.md](PLAN.md) | **PySide6 GUI 重写计划**（主计划：阶段、清单、里程碑） |
| [optimization/](optimization/) | **v2 优化计划**：现状评估、分阶段任务、架构演进、风险 |
| [DEPLOYMENT.md](DEPLOYMENT.md) | 部署与分发（路径、打包、验收、排障） |
| [analysis/](analysis/) | PySide6 重写技术分析（基线 + 落地注意点） |

使用说明：[USAGE.md](../USAGE.md) · 打包速查：[PACKAGING.md](../PACKAGING.md)

## 结构

```
docs/
├── README.md
├── DEVELOPMENT.md         # 开发交接（摘要/问题/优化）
├── PLAN.md                # 重写主计划
├── DEPLOYMENT.md          # 部署
├── optimization/          # v2 代码优化计划（评估后整理）
│   ├── README.md
│   └── OPTIMIZATION-PLAN.md
└── analysis/
    ├── README.md
    ├── 01-baseline.md     # v1 基线与复用边界
    └── 02-pyside6-notes.md # PySide6 细节与改进
```

## 当前方向

- **GUI：** CustomTkinter（v1）→ **PySide6 重写（v2.0.0，已完成代码，待真机 + 打包验收）**
- **业务 / CLI：** 保持
- 状态、已知问题与下一步见 **[DEVELOPMENT.md](DEVELOPMENT.md)**
- 重写阶段清单见 [PLAN.md](PLAN.md)
- **后续优化路线与任务看板见 [optimization/OPTIMIZATION-PLAN.md](optimization/OPTIMIZATION-PLAN.md)**
