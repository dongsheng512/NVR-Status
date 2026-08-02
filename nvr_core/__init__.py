"""nvr_core 包：海康 NVR 巡检核心逻辑（自 hikvision_status 拆分）。

对外统一入口：
- HikvisionNVR：组合 ISAPI 客户端 / 存储 / 录像 / 抽检 / 健康
- 工具纯函数与异常（Colors、_which_tools、ScanCancelled 等）见 nvr_core.util
"""

from __future__ import annotations

from nvr_core.nvr import HikvisionNVR

__all__ = ["HikvisionNVR"]
