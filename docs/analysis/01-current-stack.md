# 01 — 当前技术栈与架构

## 1. 技术栈表

| 层级 | 技术 | 用途 |
|------|------|------|
| 语言 / 运行时 | Python 3.11+ | 主程序 |
| 包管理 | uv + `pyproject.toml` / `uv.lock` | 可复现依赖 |
| GUI | CustomTkinter + tkinter/ttk | 主界面；通道表 `ttk.Treeview` |
| 表格扩展 | tksheet（依赖中保留） | 可选/未全面替换 Treeview |
| HTTP / 设备 | requests + 海康 ISAPI（Digest） | 设备信息、通道、录像、存储 |
| 流媒体抽检 | ffmpeg / ffprobe（RTSP） | 深度音视频抽检、样片 |
| CLI | rich + 自研脚本 | 终端报告 |
| 打包 | PyInstaller | `NVRStatus.app` / `NVRStatus.exe` |
| 配置 | 本机 JSON 档案 | 多 profile，密码不进安装包 |

`pyproject.toml` 依赖摘要：

- `requests` · `customtkinter` · `tksheet` · `rich`
- optional：`pyinstaller`（build extra）

---

## 2. 架构

```
┌─────────────────┐     ┌──────────────────┐
│  gui_app.py     │     │  nvr / CLI       │
│  (CustomTkinter)│     │  cli_report.py   │
└────────┬────────┘     └────────┬─────────┘
         │                       │
         ▼                       ▼
┌─────────────────────────────────────────┐
│  hikvision_status.py  (HikvisionNVR)    │
│  ISAPI · 录像检查 · 深度 AV 抽检          │
└─────────────────────────────────────────┘
         │
         ▼
┌──────────────────┐
│  config_store.py │  多档案读写、用户数据目录
└──────────────────┘
```

### GUI 运行模型

- `ScanWorker(threading.Thread)` 后台巡检  
- `queue.Queue` 投递 `log` / `progress` / `done` / `error`  
- 主线程 `after(100)` 轮询 `_poll_queue` 更新 UI  
- 进度：`overall` 或 phase + current/total 插值  

### 数据与打包

- 档案：`app_data_dir()` → macOS Application Support / Windows APPDATA  
- 打包入口：`run_gui.py` → `NVRStatus.spec`  
- 可选捆绑：`bin/ffmpeg`、`bin/ffprobe`  

---

## 3. 代码规模（约）

| 文件 | 行数量级 | 角色 |
|------|----------|------|
| `gui_app.py` | ~2500 | UI 几乎全部 |
| `hikvision_status.py` | ~1900 | 业务核心 |
| `cli_report.py` | ~600 | CLI 展示 |
| `config_store.py` | ~260 | 配置 |
| `nvr` | ~180 | CLI 入口脚本 |

**含义：** 换 UI 框架 ≈ 重写/改写 `gui_app.py` + 打包；业务层可复用。

---

## 4. GUI 功能清单（迁移对等用）

- 配置档案：新建 / 另存 / 重命名 / 删除 / 导入 / 导出  
- 设备：列表、添加/编辑弹窗、删除、保存  
- 扫描目标设备选择  
- 扫描设置（可折叠）：回溯、workers、深度参数、保存路径等  
- 快速巡检 / 深度巡检按钮；深度可选保存片段  
- 结果：汇总文案、指标卡、预警文本、通道表、详情窗  
- 日志：分级着色、复制、清空  
- 底栏：状态点、详情、进度条、百分比  
- 导出 CSV / TXT；打开样片目录  

---

## 5. 现状痛点（与框架相关）

| 痛点 | 说明 |
|------|------|
| Treeview + CTk 混搭 | 滚动条、触控板、深色主题需大量粘合代码 |
| 单文件 UI | 难测、难并行改 |
| 轮询 queue | 简单可靠，但不如信号驱动精细 |
| 字符串表单变量 | 数值校验偏晚 |
| 打包捆绑 Tcl/Tk | 体积与平台边角问题已有经验，但非零维护 |

---

## 6. 对后续选型的含义

1. **换框架技术可行度高**（业务边界清晰）。  
2. **工期主要在 UI 细节 + 双平台打包**，不在 ISAPI。  
3. **局部改进（表、拆分）** 往往比全量换栈更划算。  
4. 若换栈，应 **顺带** 做 Model/View 表与模块拆分，避免「换皮不换骨」。
