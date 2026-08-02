"""HikvisionNVR 组合类：ISAPI 客户端 + 存储 + 录像 + 抽检 + 健康。

B2 拆分：各业务模块为 mixin，本类组合成对外统一的 HikvisionNVR。
"""

from __future__ import annotations

from nvr_core.av_probe import AVProbeMixin
from nvr_core.health import HealthMixin
from nvr_core.isapi_client import ISAPIClient
from nvr_core.recording import RecordingMixin
from nvr_core.storage import StorageMixin


class HikvisionNVR(ISAPIClient, StorageMixin, RecordingMixin, AVProbeMixin, HealthMixin):
    """海康 NVR/DVR 状态巡检客户端（组合全部能力）。"""

    pass
