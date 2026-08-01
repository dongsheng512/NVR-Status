# 文档目录

| 文档 | 说明 |
|------|------|
| [PLAN.md](PLAN.md) | **PySide6 GUI 重写计划**（主计划：阶段、清单、里程碑） |
| [DEPLOYMENT.md](DEPLOYMENT.md) | 部署与分发（路径、打包、验收、排障） |
| [analysis/](analysis/) | PySide6 重写技术分析（基线 + 落地注意点） |

使用说明：[USAGE.md](../USAGE.md) · 打包速查：[PACKAGING.md](../PACKAGING.md)

## 结构

```
docs/
├── README.md
├── PLAN.md                 # 重写主计划
├── DEPLOYMENT.md           # 部署
└── analysis/
    ├── README.md
    ├── 01-baseline.md      # v1 基线与复用边界
    └── 02-pyside6-notes.md # PySide6 细节与改进
```

## 当前方向

- **GUI：** CustomTkinter（v1）→ **PySide6 重写（v2.0.0）**  
- **业务 / CLI：** 保持  
- 细节执行以 [PLAN.md](PLAN.md) 为准  
