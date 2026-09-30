# NVR Status（cam-gui）代码审核与功能分析

- 审核日期：2026-09-10
- 代码版本：工作区 HEAD = `6c83a4f`（v2.2.0）+ 未提交改动（751 insertions / 188 deletions）
- 规模：自有 Python 约 **12,528 行**（不含 .venv / build / dist）
- 动态验证：`QT_QPA_PLATFORM=offscreen pytest` → 审核时 **113 例全绿**；修复后 **127 例全绿**（新增 14 例回归，见 §7）
- 修复状态：P1-1 / P1-2 已修，P2-1 / P2-2 已修，P3 全部清理；P1-3 经复查决定不改（理由见 §7）

---

## 1. 项目定位与功能全貌

海康威视 NVR/DVR 的**状态巡检**与**音视频深度抽检**工具，双入口（PySide6 GUI + Rich CLI），可打包 Windows / macOS 分发。

| 能力模块 | 实现位置 | 说明 |
| --- | --- | --- |
| 设备信息 / 系统状态 | `nvr_core/isapi_client.py` | ISAPI Digest 认证；内存、运行时长、设备时间 |
| 摄像头在线状态 | `isapi_client.get_cameras` | 优先 `InputProxy/channels`（真实在线态），不支持则回退 `Streaming/channels`（仅已配置） |
| 硬盘与循环覆盖 | `nvr_core/storage.py` | 多端点探测循环覆盖字段（固件命名差异达 13 种）；读不到时按「满盘是否仍 ok」推断，并显式标注「推断」 |
| 录像计划 / 落盘 | `nvr_core/recording.py` | 解析 Track/Actions 的 Record 标志与 SaveAudio；CMSearch 检索近期落盘，含**末页翻页**与「近 90 分钟」二次兜底 |
| 音视频深度抽检 | `nvr_core/av_probe.py` | ffmpeg 短时 RTSP 拉流 + ffprobe 判轨；`-c copy` 不重编码；视频/音频**分轨独立拉取**；繁忙时段定点；失败换时段复检 |
| 健康规则汇总 | `nvr_core/health.py` | 内存 / 硬盘 / 离线 / 计划 / 音频 / 落盘 / 抽检 汇总为 良好<警告<严重 |
| 统一编排 | `nvr_core/scan_runner.py` | `build_nvr` / `run_nvr` / `scan` / `scan_queue`，GUI 与 CLI 共用，消除逻辑分叉 |
| 配置档案 | `config_store.py` | 多档案 JSON 原子写入；损坏先留档再重置；密码走系统 keyring |
| 凭证安全 | `services/credentials.py` | macOS Keychain（`security` CLI）/ Windows Credential Manager（ctypes）；不可用回退明文 |
| 历史报告 | `services/history.py` | 巡检结果自动归档 JSON，保留最近 500 份，元数据列表 + 再导出 |
| 导出 | `services/export_report.py` | CSV（utf-8-sig，Excel 友好）/ TXT，无 Qt 依赖，含公式注入防护 |
| GUI | `ui/` | 主窗组装 + 左配置 / 右结果 / 日志三面板 + Model/View 通道表 + 亮暗主题 |
| CLI | `nvr` / `hikvision_status.py` / `cli_report.py` | 兼容门面 + Rich 报告；退出码 0/1/2 便于 cron 告警 |

---

## 2. 架构评估：分层干净，无跨层反依赖

依赖方向实测（grep 验证）：

```
ui/  ──(惰性 import)──>  nvr_core.scan_runner  ──>  nvr_core.*  （无 Qt）
services/  ──>  config_store（仅 app_data_dir）
nvr_core/  ──>  ✗ 不引用 ui / cli_report
ui/        ──>  ✗ 不直接 import nvr_core（仅 scan_worker 惰性取编排入口）
```

结论：**分层纪律良好**。`nvr_core` 完全无 Qt 依赖，可单测、可被 CLI 复用；`services` 为纯逻辑；`ui` 只做展示与编排。`hikvision_status.py` 已退化为「薄门面 + 兼容再导出」，旧 import 不破坏。

### 数据流（单台巡检）

```
run_nvr
  ├─ get_device_info            → 失败即返回错误报告
  ├─ get_system_status          （顺带预热 /System/status 缓存）
  ├─ get_cameras                （InputProxy 优先，带缓存）
  ├─ get_recording_status
  │    ├─ 解析 /record/tracks → 通道/模式/音频/计划
  │    ├─ ThreadPoolExecutor(8) → CMSearch 近期落盘（末页+90min 兜底）
  │    └─ 可选 _run_deep_av_checks → 短时 RTSP 抽检 + 换时段复检
  ├─ get_health_summary         （复用上述全部缓存）
  └─ get_storage_status / get_alarm_status
进度回调 → ScanWorker(信号节流 80ms) → _PHASE_RANGES 阶段插值 → 状态栏
```

---

## 3. 值得肯定的工程细节

1. **取消是真取消**：`ScanCancelled` 异常在业务循环检查点抛出；线程池 `shutdown(cancel_futures=True)` 立即丢弃排队任务；ffmpeg 用 `Popen` 包装成可取消版，点「取消」即 kill 子进程，而非等全量跑完。
2. **负缓存语义精确**：`_parse` 只对「确定性失败」（HTTP 非 200 / 解析失败）入负缓存，瞬时网络失败不缓存，网络恢复后可重试。
3. **设备时区只在成功时缓存**：解析失败回退本机时区但**不缓存**，避免把错误时区固化成后续 RTSP 时间改写基准——这是个很容易写错、且会导致「停录却误报正常」的坑。
4. **不伪造数据**：`freeSpace` 缺失时不再算成 100% 满盘（否则连锁误报「硬盘已满」）；硬盘状态缺失/未知一律不计为异常。
5. **安全防护到位**：设备 XML 拒绝 `DOCTYPE/ENTITY` + 8MB 上限（防实体扩展）；CSV 单元格防公式注入（`= + - @` 前缀加 `'`）；错误串里的 `rtsp://user:pass@` 掩码后才进报告/历史；HTTP 响应做大小限制。
6. **持久化可靠**：配置原子写入（mkstemp + `os.replace`）；JSON 损坏先改名留档（保留 3 份）再重置；读取被占用时不覆盖；导出文件 POSIX 下 `0o600` 且 GUI 明示「含明文密码」。
7. **注释质量高**：几乎每个非直觉决策都写了「根因 / 为什么这么写」，对交接极友好（例：为何 Track 顶层 `Enable` 不可信、为何并发 CMSearch 必须各用不同 `searchID`、为何只 `-map 0:v:0`）。
8. **测试**：113 例（修复后 127 例）覆盖导出、通道筛选排序、循环覆盖解析、lookback 换算、取消、信号节流、档案条、空闲布局、CMSearch 翻页、RTSP 构造、凭证、音频判定；修复后补上并发单飞、物理通道去重、指标口径、结果键唯一性。

---

## 4. 发现的问题（按严重度排序）

### P1 — 建议修（正确性 / 行为）

**P1-1 并发共享 `requests.Session`**
`get_recording_status` 用 8 线程跑 `_search_track_recent`，其内部 `_get_device_tz()` → `get_system_status()` → `_parse()` 走的是**共享的 `self.session`**（虽然 CMSearch 本身用了线程本地 Session）。`requests.Session` 官方声明非线程安全；且 `_parse` 的「查缓存→请求→写缓存」非原子，冷缓存时多线程会重复请求同一端点。
当前被 `run_nvr` 的执行顺序（先 `get_system_status` 预热缓存）掩盖，属**潜在竞态**：一旦有人直接调 `get_recording_status()` 或调整编排顺序就会踩。
→ 建议：给 `_parse` 加锁，或在 `get_recording_status` 并发前显式预热设备时区。

**P1-2 两套「通道」口径混用，指标分母可能失真**
`health.统计.通道总数 = len(records)`（Track 数），而 `摄像头总数 = len(cameras)`（通道数）。`results_panel` / `cli_report` 用 `摄像头总数` 作分母渲染「录像正常 x/total」。
若某固件 `/ContentMgmt/record/tracks` 每通道返回多条 Track（主码流 + 子码流），`records` 会多于 `cameras`，出现 `x/4` 中 x>4 的比例失真，通道表也会出现重复通道号（`get_recording_status` 未按物理通道去重）。
→ 建议：在 `get_recording_status` 内按 `_physical_channel` 去重，或 UI 分母统一改用 `通道总数`。

**P1-3「音频抽检未知」整体拉低健康等级**（本次未提交改动）
新增的 `音频抽检未知` 在 health 里 `raise_to("警告")`。但「未知」的成因是**拉流超时/失败**——本质瞬时。任一路抖动就让整台设备从「良好」变「警告」，告警会偏吵。
→ 建议：明确产品意图。若要保留，考虑只进统计与结果区、不参与 `健康状态` 判定；或单独给一个更轻的提示级别。

### P2 — 可选优化

| 编号 | 位置 | 问题 | 建议 |
| --- | --- | --- | --- |
| P2-1 | `recording.get_recording_status` | `if r not in to_search` 是 O(n²) 字典比较 | 改用索引/`id` 集合 |
| P2-2 | `recording` 结果字典 | 以 `track_id` 为键，多 Track 同为「未知」会互相覆盖磁盘结论 | 键改为 `id(r)` 或稳定唯一键 |
| P2-3 | `config_store.get_profile` | getter 却会写 `self.data`（补全 profile / 合并 scan_options）且不 save | 拆出显式 `ensure_profile` |
| P2-4 | `results_panel.render_result` | `audio_tone` 在 `total==0` 时给 `warn`；且「含音频配置」用配置值，与深抽检数字语义易混 | 标题区分「配置」/「实测」 |
| P2-5 | `channel_table._balance_name_column` | 每次 resize 反复切换 Stretch/Interactive，大表有 layout 抖动风险 | 仅在实际越界时切换 |
| P2-6 | `left_panel._del_device` | 「全部删除」兜底依赖 `_device_rows` 尚未重新赋值的时序 | 先构造 keep 再重建行 |
| P2-7 | `services/credentials` | ~~Windows `CredentialBlobSize=(len+1)*2` 含结尾 NUL，MSDN 要求不含~~ **⚠️ 本条为误判，已撤回** | **无需修改。** 复核 MSDN [CREDENTIALW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw)：「不含结尾零字符」只适用于 `CRED_TYPE_DOMAIN_PASSWORD` / `CRED_TYPE_DOMAIN_CERTIFICATE`；`CRED_TYPE_GENERIC` 原文是 *"this member is defined by the application"*，`CredentialBlob` 格式完全由应用自定义。故含结尾 NUL 合法，且读取端已 `rstrip("\x00")`，往返一致。**改反而会偏离惯例并可能影响其他工具读取。** |
| P2-8 | `services/credentials` | macOS 用 `-w <password>` 把密码放命令行参数，短时可见于进程列表 | 内网可接受；如需更严换 keyring 库 |

### P3 — 文档 / 工程一致性

| 编号 | 问题 |
| --- | --- |
| P3-1 | **README 的「仓库结构」漏了 `nvr_core/`**，且架构图仍把 `hikvision_status.py (HikvisionNVR)` 画成核心——实际核心已迁入 `nvr_core/`，该文件只是兼容门面。属文档漂移。 |
| P3-2 | **根目录 `main.py` 是死代码**：内容仅 `print("Hello from cam!")`，已从 `pyproject` 的 `py-modules` 移除，全仓无引用。建议删除。 |
| P3-3 | `docs/DEVELOPMENT.md` 写「96 例」，实际 113 例；§2.3 标题仍写「阻塞 v2.0.0 发布」，与 2.2.0 已发布不一致。 |
| P3-4 | `docs/DEVELOPMENT.md` §4 称 `optimization/OPTIMIZATION-PLAN.md`「未入库，已遗失」，但该文件实际存在于 `docs/optimization/`。文档自相矛盾。 |

---

## 5. 未提交改动（WIP）评估

工作区有 **751 行新增 / 188 行删除**未提交，与 HEAD 的 `6c83a4f` 相比：

| 文件 | 变化 | 内容 |
| --- | --- | --- |
| `nvr_core/av_probe.py` | +591 | 音频轨独立抽检（`-map 0:a:0 -vn`）、`no_stream` 判定、失败换时段复检、音频「未知」态 |
| `nvr_core/util.py` | +64 | `AV_SAMPLE_MIN_AGE`（10 分钟截止线）、`alternate_clip_times`（换时段选点） |
| `nvr_core/recording.py` | +62 | 繁忙窗右沿距现在 ≥10 分钟；新增 `_pick_retry_clip_times` |
| `nvr_core/health.py` / `cli_report.py` / `export_report.py` | +15 | 新增「音频抽检未知」统计与展示 |
| `ui/widgets/channel_table.py` | +85 | 未知音频 → `warn` 着色 |
| `tests/test_av_audio.py` | 新增 192 行 | 判定函数 / `_pull_rtsp_map` / 复检 / 换时段 12 例 |
| 其余 | — | 图标重绘、文案、提示 |

评价：改动**自洽且有测试**，主题集中（「音频独立抽检」+「复检」+「10 分钟截止线」）。提交前建议：
1. 拆成 2–3 个 commit（音频抽检重构 / 换时段复检 / 截止线），便于回溯；
2. 实测耗时影响：音频轨独立拉流使每路抽检多一次 ffmpeg 调用，64 路 ×（视频 + 音频 + 复检）的墙钟时间需要真机确认；
3. 补一条「整链路」集成测试或真机验收记录（现有测试只到判定函数与单次拉流）。

---

## 6. 可执行的下一步（按优先序）

1. **修 P1-1**：给 `_parse` 加锁或在并发前预热设备时区，消除共享 Session 竞态。
2. **修 P1-2**：`records` 按物理通道去重，或统一「通道」分母口径。
3. **定 P1-3**：明确「音频抽检未知」是否应拉低整体健康（建议降为提示或不参与判定）。
4. **清理 P3**：删 `main.py`；README 补 `nvr_core/` 并修正架构图；更新 DEVELOPMENT 例数与版本口径。
5. **WIP 收口**：拆 commit + 真机跑一次深度巡检（含复检路径）并记录耗时。

> ↓ 以上 1/2/4 已于同日执行，见 §7。

---

## 7. 修复落地记录（2026-09-10）

修复后 `QT_QPA_PLATFORM=offscreen pytest` → **127 例全绿**（原 113 例 + 14 例新增回归）。

| 编号 | 状态 | 处理方式 | 对应改动 |
| --- | --- | --- | --- |
| P1-1 并发共享 Session | ✅ 已修 | 三条一起收口：`_parse` / `_parse_endpoint_quiet` 整段（含请求）置于 `_cache_lock`（**RLock** —— 持锁期间失败提示会走进度回调，同线程可能重入）；`_get_device_tz` 加 `_tz_lock` 双重检查；`get_recording_status` 与 `_run_deep_av_checks` 在**启动线程池前**先在主线程预热设备时区。 | `nvr_core/isapi_client.py`、`nvr_core/recording.py`、`nvr_core/av_probe.py`、`tests/test_concurrency.py` |
| P1-2 通道口径混用 | ✅ 已修 | **双向修**：①`_physical_channel` 把「物理通道+两位码流号」统一映射（原实现 `101→"1"` 但 `102→"102"`，同一摄像头主子码流被拆成两个通道，子码流行的名称/在线匹配不到 → 显示「未知」）；②`get_recording_status` 按物理通道合并主/子码流轨道，保留编号最小（主码流）一条，通道号非数字时**不合并**以免误并不同摄像头；③结果区「录像正常/近期有录像/含音频配置」分母改用与分子同源的 `len(records)`，杜绝 `5/4` 失真。副作用是省掉了对子码流的无效 CMSearch。 | `nvr_core/recording.py`、`ui/panels/results_panel.py`、`tests/test_physical_channel.py`、`tests/test_metric_denominator.py` |
| P1-3 音频抽检未知拉低健康 | ✅ 已修（2026-09-10 后续，两个一起调） | 复查确认不能单点改（会造成两套「未知」口径不一致）。作为独立议题**同时调整落盘与音频**：改为**按比例**口径 —— 部分通道未知（瞬时抖动）只进统计与结果区、不参与 `健康状态`；实际检查到的通道**全部未知**（整体失败）才升 `警告`；不新增第 4 个严重度级别。回归：`tests/test_health_unknown_policy.py`。 | `nvr_core/health.py`、`tests/test_health_unknown_policy.py` |
| P2-1 `if r not in to_search` | ✅ 已修 | 改为 `id(r)` 集合。这是**真 bug**不只是性能问题：`in` 对 dict 是按**值**比较，两条内容完全相同的记录会被误判为「已在待检索列表」而**漏检落盘**。 | `nvr_core/recording.py` |
| P2-2 结果字典以 `track_id` 为键 | ✅ 已修 | 改为以记录身份（`id(rec)`）为键。原写法下重复/「未知」`track_id` 会让多条记录**互相覆盖**同一份落盘结论。 | `nvr_core/recording.py`、`tests/test_physical_channel.py` |
| P3-1 README 架构漂移 | ✅ 已修 | 仓库结构补 `nvr_core/`；架构图改为「`scan_runner` 统一编排 → `nvr_core` 业务核心」，并注明 `hikvision_status.py` 已退化为兼容门面 + CLI。 | `README.md` |
| P3-2 `main.py` 死代码 | ✅ 已删 | 已从 `pyproject` 移除且全仓无引用；删除可由 `git checkout main.py` 恢复。 | `main.py`（删除） |
| P3-3 例数 / 版本口径过时 | ✅ 已修 | 例数更新；§2.3 去掉「阻塞 v2.0.0 发布」。 | `docs/DEVELOPMENT.md` |
| P3-4 OPTIMIZATION-PLAN「已遗失」 | ✅ 已修 | 该文件确实存在于 `docs/optimization/`，改为正常链接。 | `docs/DEVELOPMENT.md` |

| P2-3 `config_store.get_profile` 写副作用 | ✅ 已修（**升级为安全修复**） | 改为返回 `deepcopy` 快照，不再登记新档案、不再改 `self.data`。原实现返回**内部对象本身**，而 GUI `_load_active_profile_to_form` 会把 `resolve_devices()`（含从 keyring 取回的**明文密码**）赋给它的返回值 → 明文写进内存 → 下一次 `save()` 落进 `profiles.json`，**等于把整个 keyring 设计抹掉**。回归：`tests/test_credentials.py` 3 例。 | `config_store.py`、`tests/test_credentials.py` |
| P2-4 结果区空集告警 / 音频口径混淆 | ✅ 已修 | ① `rec_total==0` 时音频卡给 `muted`「未检查」，不再误报黄；② 深抽检模式下标题切「音频实测」，分母改为**实际抽到流**的通道（`跳过` 不计入），分级为 无音轨=红 / 静音·未确认=黄，tooltip 给出「正常/静音警告/无音轨/未确认/跳过」分解；非深抽检仍显示配置口径「含音频配置」并在 tooltip 标出未知数。 | `ui/panels/results_panel.py`、`tests/test_metric_denominator.py` |
| P2-5 名称列 resize 抖动 | ✅ 已修 | 缓存 `_name_mode`，只在模式真正变化时调 `setSectionResizeMode`（该调用会触发表头重排 → 回调 `resizeEvent` → 再进本函数，形成正反馈）。**实测未修版：一次 900→760px 拖动触发 7 次模式切换。** 回归：`tests/test_channel_table.py` 2 例。 | `ui/widgets/channel_table.py`、`tests/test_channel_table.py` |
| P2-6 `_del_device` 时序依赖 | ✅ 已修 | 先把保留集合写回 `_device_rows`，再动控件；兜底补设备因此发生在「已清空」之后。**实测未修版：全删时 `_refresh_scan_target()` 中途观测到 4 台设备（含 2 台幽灵）。** 顺带用 `row_data["row"]` 取代 `chk.parentWidget()` 定位行。回归：`tests/test_idle_layout.py` 3 例。 | `ui/panels/left_panel.py`、`tests/test_idle_layout.py` |
| P2-7 Windows `CredentialBlobSize` | ↩️ **误判已撤回** | 经查 MSDN：不含结尾 NUL 只约束 `CRED_TYPE_DOMAIN_PASSWORD` / `CRED_TYPE_DOMAIN_CERTIFICATE`；`CRED_TYPE_GENERIC`（本代码所用）是 *"defined by the application"*，**含 NUL 合法**。代码不改。 | — |
| P2-8 macOS `security -w <password>` | ⏸ 保留 | 内网环境可接受；如需更严改用 keyring 库。属有意取舍，非遗漏。 | — |

**P2 全部处置完毕**（1/2 已在首批修复，3–6 本轮落地，7 撤回，8 有意保留）。测试规模 127 → **154 例**，新增部分包含 `tests/test_av_integration.py`（15 例）：以环境变量驱动的**伪 `ffmpeg`/`ffprobe`** 真实走 subprocess，端到端覆盖音视频抽检链路（此前只有单元级覆盖）。

**本轮新发现的环境坑（非代码问题）**：本机（agent shell）直接跑 `pytest` 会出现「第一次全绿、第二次起大量 `PermissionError: EEXIST ... mkdir pytest-of-root`」。根因是沙箱 shim 拦截 `mkdir`，对**已存在目录**的 `mkdir(exist_ok=True)` 直接抛 `PermissionError`，而 pytest 9.x 的 `getbasetemp()` 默认根目录恰用该调用。解法是显式加 `--basetemp=/tmp/cam-pytest`（pytest 会先 `rm_rf` 再创建，永不撞已存在目录）。已写入 `docs/DEVELOPMENT.md` §2.2。

**本轮新发现的测试坑（写 Qt 用例时注意）**：不要用 `monkeypatch.setattr` 打在 **PySide6 实例**上 —— teardown 会用 `delattr` 还原，对 shiboken 对象直接**段错误**（进程崩、无 Python 回溯）。改为手动 save/restore（只赋值、不删除）；打在**模块属性**上（如 `monkeypatch.setattr(left_panel, "QMessageBox", Dummy)`）是安全的。已写入 `docs/DEVELOPMENT.md` §2.2。

---

## 8. 高性价比两项落地（2026-09-10 续）

`QT_QPA_PLATFORM=offscreen pytest --basetemp=/tmp/cam-bt` → **189 例全绿**（本轮新增 35 例）。

### 8.1 `chanDetectResult` 纳入快速巡检判定

**问题**：`isapi_client._get_input_proxy_cameras` 早已把 `InputProxy/channels/status` 的 `chanDetectResult` 采进 `检测状态`，但**全仓零使用**（grep 仅 3 处命中，全是赋值），等于白拿了一层「通道级」信号却没用于判定。

**改法**：新增 `classify_detect_state()` / `detect_reason()`，采用**白名单**归一 —— `connect`（及 `connected/normal/ok/已配置`）=在线；`notExist`/`disconnect`/`netError`/`ipConflict`/`userPwdError`/`userLocked`/`unsupported` 等=异常；**其余取值（含空、`unknown`、未见过的新值）一律不参与判定**。这是刻意的保守取舍：各固件取值不统一（搜索结果佐证 `connect` ↔ `online=true`、`notExist` ↔ `online=false`），**宁可漏判也不误报**。

判定优先级（`nvr_core/health.py:get_health_summary`）：

1. `online == false` → 离线（**权威判据**，即使检测状态说 `connect` 也判离线）；
2. `online == true` → 在线；若检测状态**明确异常**，另记「N 个在线通道检测状态异常」(警告)，**不混入离线**；
3. `online` 缺失/取值异常 → 用检测状态补判（`connect`→在线，异常→离线）；
4. 两者都非明确取值 → 计入新增统计 `摄像头状态未确认`（**不误报离线**）。

离线预警还会附**原因细分**（如「通道未接入 3、网络不可达 1」），比原来只有名称更有诊断价值。新增统计键：`摄像头状态未确认`、`通道检测异常`。**零额外请求**（数据本就在手）。

**呈现（形态不变）**：结果区在线卡在 `通道检测异常>0` 时 tone=warn，`未确认>0` 时写进 tooltip；数值仍为 `online/total`，不加卡片、不改容器。CLI 明细行同步（`在线 x / 离线 y / 通道检测异常 z / 未确认 w · 共 n`）。

回归：`tests/test_health_detect_state.py`（23 例）、`tests/test_results_online_card.py`（3 例）。

### 8.2 「未知」健康口径收敛（落盘未知 / 音频抽检未知）

**问题**：两者都无条件 `raise_to("警告")`，但成因多为**瞬时**检索/拉流超时，任一路抖动即把整机降级，告警偏吵；且单改一路会造成两套「未知」口径不一致。

**改法（两个一起调）**：实际检查到的通道里**只有部分未知** → 仅进统计与结果区，**不参与** `健康状态` 判定；**全部未知**（整体检索/拉流失败）→ 升级为 `警告`，避免变成假阴性。分母只计「真正尝试过」的通道（`跳过` 不计入），因此「未开音频的通道」不会把分母灌大而掩盖整体失败。

**未引入**第 4 个严重度级别 —— `良好 / 警告 / 严重` 三档、UI 配色、导出报告与 CLI 退出码（2=严重）均**无需改动**。

回归：`tests/test_health_unknown_policy.py`（9 例，含「两个口径一致」用例）。

### 8.3 变异测试反向验证

为避免「改完测试跟着改、等于没测」，两组改动都做了**变异测试**（临时还原旧逻辑 → 期望变红 → 恢复）：

| 变异 | 结果 |
| --- | --- |
| `落盘未知` 还原为无条件告警 | `test_health_unknown_policy.py` 红 2 例 ✅ |
| `classify_detect_state` 恒返回 `""`（等价改前「零使用」） | `test_health_detect_state.py` 红 13 例 ✅ |
| `classify_detect_state` 把白名单外一律判「异常」（过度判定） | `test_health_detect_state.py` 红 5 例 ✅ |

每次变异后均 `cp` 还原，并用 `grep`（无残留标记）+ `ast.parse`（语法 OK）复核。

## 9. 深度抽检两项增强（2026-09-10 续二）

起因：真机排查 64 路 NVR 上 2 个指定通道（31 / 64）在某个具体时刻的音频情况时，暴露了两个可用性缺口。

### 9.1 回放 RTSP 的 `:554` 端口回退

**问题**：CMSearch 返回的 `playbackURI` 里端口是设备的 **HTTP** 端口（`:80`）。部分环境下该端口的 RTSP 被设备**静默拒绝** —— TCP 三次握手正常，但一发 `DESCRIBE` 就收到空响应并断连，ffmpeg 只报 `Invalid data found when processing input`（rc=183），无从判断是「取流方式不对」还是「真的没流」。同一路径换成 `:554` 则返回正常的 `RTSP/1.0 401 Unauthorized`，随后取流成功。

**改法**（`nvr_core/av_probe.py`）：

- 新增 `_swap_rtsp_port(uri, port)`：只换端口，保留 userinfo / path / query；**无显式端口或已是指定端口时返回 `None`**（无端口时 ffmpeg 本来就默认 `:554`，无需多试）；正确处理 IPv6 字面量 `[::1]:80`。
- `_build_short_rtsp_candidates` 内部改用 `_add_with_port_alt(label, url)`：先加原候选，再补 `label@554` 变体。
- 新增**模块级**排序键 `av_candidate_order(label)`，把 `@554` **一律排在最后**：

  ```python
  (1 if label.endswith("@554") else 0,
   0 if label.startswith("original") else 1,
   label)
  ```

  这是关键取舍：`@554` 只是「原端口整条链路都不通」时的兜底，若让它插队，正常链路每次都要白等一次最长 ~30s 的超时。
- `_probe_track_av` / `_pull_rtsp_map` 的超时预算判定由 `label == "original"` 改为 `label.startswith("original")`，使 `original@554` 也走「改写短窗」的较紧预算，而非 original 的大预算。

### 9.2 CLI 新增 `--av-channels`（只抽检指定物理通道）

**问题**：单机 CLI 与 `./nvr` 都**没有按通道筛选的选项**。`--av-limit` 只限数量、不选通道；想只查 31/64 两路，只能自己拼 `nvr_core` 薄脚本。而 64 路全量抽检要跑数分钟～十几分钟，排查单点问题时成本过高。

**改法**：

- `nvr_core/util.parse_channel_list(value) -> Optional[frozenset[int]]`：接受 `"31,64"` / `"31 64"` / `"31、64"` / 数组 / 单个 int；`None`、空串、纯空白 → `None`（= 不过滤）。**非空但解析不出合法通道号时抛 `ValueError` 并指出是哪个 token。**
  - 刻意选「报错」而不是两种静默降级：静默退化为「不过滤」会让用户以为只抽了指定通道、实际抽了全部；静默丢弃落单 token 会让用户以为查过某路、其实没查。两种误导都比直接报错更糟。
- `hikvision_status.build_arg_parser()` 新增 `--av-channels N[,N...]`；`nvr_from_args` 里若未启用深度抽检（`--deep-av-check` / `--av-save` 都没有），**打印警告并忽略**该参数，而不是静默留着让参数看起来生效了。`./nvr` 复用同一 parser，多设备入口自动可用。
- `nvr_core/scan_runner.build_nvr` 读取 `opt["av_channels"]`，解析失败 → `_notify` 提示后按「全部候选通道」继续（配置档案里写错了不该让整次巡检崩）。
- `nvr_core/av_probe._run_deep_av_checks` 的过滤位置：**「非候选标记」之后、「`av_limit` 之前」**。
  - 在「未配置录像 / 近期无录像」**之后** —— 那些是更值得用户看的真实问题，不该被「不在抽检列表」掩盖；
  - 在 `av_limit` **之前** —— `--av-channels 31,64 --av-limit 2` 的语义是「在这两路里最多抽 2 路」，而不是先按上限截断再过滤。
  - 实测通道标 `跳过` + 详情 `不在指定抽检通道列表`（不复用「超出上限」文案，两种原因不混淆）。
- `run_nvr` / `_error_report` 的结果 dict 增加 `av_channels`（升序 list），`cli_report` 的「音视频抽检」指标行追加「仅通道 31、64」—— 否则用户看到只抽了 2 路会误以为漏检。
- **GUI 不暴露该选项**（`profiles.json` 的 `scan_options` 不新增键）= 全部通道，前端零改动。

### 9.3 回归与变异测试

新增 42 例：`tests/test_av_rtsp_port.py`（12）+ `tests/test_av_channels.py`（26）+ `test_av_integration.py`（+4，走伪 ffmpeg 全链路验证过滤与理由优先级）。全量 **231 例全绿**。

| 变异 | 结果 |
| --- | --- |
| 去掉 `@554` 变体生成 | `test_av_rtsp_port.py` 红 ✅ |
| `@554` 不再排最后 | `test_av_rtsp_port.py` 红 ✅ |
| `_swap_rtsp_port` 恒返回 `None` | `test_av_rtsp_port.py` 红 ✅ |
| 过滤判定恒真（列表外通道也被抽） | `test_av_integration.py` 红 ✅ |
| `av_limit` 先于通道过滤执行（block 整段对调） | `test_av_integration.py` 红 ✅ |
| 无效通道号静默丢弃 | `test_av_channels.py` 红 ✅ |
| CLI 不把 `av_channels` 传下去 | `test_av_channels.py` 红 ✅ |

每次变异后均还原，并用字节级 `diff` + `ast.parse` 复核无残留。

### 9.4 真机验证（NVR-A · 通道 31 / 64 · 多个时刻）

最早一版（昨日 19:05）：

```
av_channels = [31, 64]  deep=True
通道 31  前端相机-1   落盘 正常   视频 正常 hevc 2560x1440   音频 未知
通道 64  前端相机-2   落盘 正常   视频 正常 hevc 2560x1440   音频 未知
抽检详情: 短时抽检OK@09-09 19:05:00 2560x1440 hevc;
          音频未确认(拉流超时/失败): 拉流超时(short/utc@554,12s)
过滤生效：列表外 62 路中 62 路标为「不在指定抽检通道列表」
```

- 过滤按预期生效（62/62 标记正确）。
- 详情里出现 `short/utc@554` 说明 **`:554` 候选确实被构造并尝试了**，回退链路在真机上跑通。

后续多时刻复测（今天 16:20 / 17:30 / 18:40 / 19:10，2 路共 8 次）发现 **ch64 偶发**、**ch31 持久**：
ch64 在 17:30 起恢复正常（`pcm_alaw −9.8dB`），19:10 仍正常；ch31 4 个时刻全部仍 未知。

**用户反馈**：是这两路 IPC 摄像头本身的问题，**19:00 重启后已恢复**。
工具层（`@554` 回退、独立音频轨拉流、`volumedetect`）均无问题；判断是对的 —— 「未知」就是「拿不到，不下结论」，
IPC 一恢复立即能给出 `pcm_alaw −9.8dB` 的正常有声结论。

### 9.5 遗留：抽检通道过滤 与 「音频抽检未知」口径的交互（待决策）

`--av-channels` 收窄到 31/64 后，被实际尝试的通道只剩 2 路，且**两路都未知**。按 §8.2 的口径「实际检查到的通道全部未知 → 升 `警告`（整体拉流失败）」，此时会触发告警。

逻辑上不算错（用户问的这两路确实都没确认），但 **`整体拉流失败` 这个措辞在「指定子集」语境下是误导的** —— 它本意描述「整机级」故障。两个可选处置，待定：

1. **只改措辞**：`get_health_summary` 在有过滤时（`self.av_channels is not None`）把文案改为「指定抽检的 N 个通道音频均未确认」，**升级行为不变**。
2. **连行为一起改**：过滤到子集时不升级（视作抽样性质的检查）。

倾向 1（保行为、修措辞）：告警本身是有信息量的，换成 2 会丢掉「用户点名要看的两路都没拿到音频」这个真实结论。
**IPC 故障现场已坐实这一点** —— 这两路 19:00 IPC 重启前确实两路都拉不到音，「指定抽检的 N 个通道音频均未确认」就是这件事的精确表述。

## 10. 发布前功能审查与打包（2026-09-11）

对 2026-09-10 全部未提交改动做了一遍功能审查（isapi_client / recording / health / av_probe / util / scan_runner / cli_report / results_panel / channel_table / left_panel / config_store / hikvision_status），57 个 py 文件过了 AST + 未定义名静态扫描。

### 10.1 审查发现并修复（1 项 P1）

| 级别 | 位置 | 问题 | 处置 |
| --- | --- | --- | --- |
| **P1** | `nvr_core/recording.py` 落盘检索线程池 | `except ScanCancelled:` 用到了 `ScanCancelled`，但**模块没有 import**。用户在「近期录像检查」阶段点取消 → 异常处理器自身先抛 `NameError`，把真正的取消异常吞掉，GUI 表现为「取消失败/崩溃」而非干净取消 | ✅ 补 import；新增回归 `tests/test_cancel.py::test_disk_search_cancel_propagates_not_nameerror`；**变异验证**：去掉 import → 测试红且报 `NameError`；恢复 → 绿。⚠️ 写这个测试时踩了个坑：若在**调池之前**就置位取消标志，`_progress` 自身也是取消检查点，异常从池外抛出、根本走不进这个 handler，测试会**假绿** —— 必须让取消发生在**线程池已开跑之后** |

**为什么 232 例测试没拦住**：既有的取消用例只覆盖 AV 抽检阶段（`av_probe` 自身有 import）和「启动前取消」，落盘阶段的线程池取消路径没有测试。本次补上。

### 10.2 复核无问题的点

- **锁顺序**：`_get_device_tz`（`_tz_lock`）→ `_parse`（`_cache_lock`）单向嵌套；`_cache_lock` 内的 `_get` 不会再调 `_get_device_tz`，无死锁环。`raise_to` 只升不降，「离线(严重) + 检测异常(警告)」并存时不会被降级。
- **物理通道合并**：`101/102→通道1`、`6401/6402→通道64`、`tid%100∈{1,2}` 且 `≥100`；通道号非数字不参与合并（不会误并不同摄像头）。合并数日志正确（`len(records) − len(merged) − len(unparsed)`）。
- **结果键改 `id(rec)`**：重复/「未知」track_id 不再互相覆盖；`search_ids` 用 id 集合判定。
- **健康口径**：`音频未知`（配置侧）与 `音频抽检未知`（实测侧）是两个键，results_panel 非 deep 分支读前者、deep 分支自算，没有串。
- **音频卡两套口径**：非深度抽检看 `SaveAudio` 配置，深度抽检看实测（正常/静音警告/无音轨/未确认 分级）；空结果集显示「未检查」不落黄色告警。
- **`:554` 回退**：无显式端口/已是 554 时不加变体；`original@554` 走短窗超时预算；去重由 `_add` 的 URL 集合保证。

### 10.3 记录在案、未改（有意保留 / 低风险）

1. **GUI 保存会丢手编的 `av_channels`**：`left_panel.current_options()` 从控件重建 `scan_options`，若用户手编 `profiles.json` 加了 `av_channels`，GUI 下一次保存会丢掉。GUI 本就不暴露该选项，CLI 场景不受影响；要支持需在 GUI 加输入框，暂不做。
2. **卡片级「警告」与整机「健康状态」可能不一致**：结果区「音视频抽检」卡只要有 未确认 就显示黄色，而整机健康按 §8.2 口径可能仍是「良好」。这是卡片级可见性与整机判定的刻意分工，不动。
3. **`channel_table._apply_column_layout` 冗余分支**：`if col == "name"` 与 `else` 都设 `Interactive`，顺手合并（行为不变）。
4. **`_get` 用共享 Session**（`_cache_lock` 内串行）而 `_post` 有线程局部 Session —— 既有行为，GET 串行化后实际风险低，本轮不动。

### 10.4 版本与打包

- 版本 2.2.0 → **2.3.0**（`pyproject.toml` / `ui/main_window.APP_VERSION` / `ui/app` ×2 / `NVRStatus.spec` Info.plist / README / DEPLOYMENT / DEVELOPMENT）。
- 232 例全绿后打包：`dist/NVRStatus.app` **148MB** + `dist/NVRStatus-macOS-arm64-2.3.0.zip` **61MB**（结构与 2.2.0 发布包一致：zip 根为 `NVRStatus.app`）。
- 校验：Info.plist `CFBundleShortVersionString = 2.3.0`；解包 PYZ 确认 `ui.main_window` / `ui.app` 内含 `2.3.0` 且无 `2.2.0` 残留；产物内 `ffmpeg/ffprobe 8.1.2` 可独立运行（`bin/libs` 18 个 dylib 随包）；`codesign --verify --deep --strict` 通过。
- **冒烟对照**：本机 shell 拿不到 WindowServer，`.app` 离屏启动 2.3.0 与已发布 2.2.0 基线行为**完全一致**（均 rc=134、无输出）→ 属环境限制，非本轮回归。
- 环境注意：本沙箱里 `uv sync` 会因 `mkdir EEXIST` 失败（与 pytest `--basetemp` 同根因）。替代路径：`uv sync --no-install-project` + `uv pip install "pyinstaller>=6.0.0"` + `.venv/bin/pyinstaller --noconfirm NVRStatus.spec`，再手工执行 `build_mac.sh` 的 ffmpeg 回填 / 重签名 / ditto 三步。

## 11. `--av-at` 定点抽检（可复用单通道检查，2026-09-11）

把此前「临时覆写 `_pick_busy_clip_times` 钉时刻」的做法做成正式 CLI 功能：

- **用法**：`./nvr 1 --deep-av-check --av-channels 31 --av-at 19:05,16:20`
  = 单通道定点复查；`--av-at` 可多时刻，每时刻各查一遍全部候选通道。
- **实现链**：`util.parse_instant_list`（HH:MM 解析/去重/未来时刻报错，datetime 直通幂等）
  → `hikvision_status --av-at`（未开深度抽检时提示并忽略）→ `scan_runner.build_nvr`
  预解析（非法降级为繁忙时段逻辑并提示）→ `HikvisionNVR.av_at` → `av_probe` 定点循环
  → `recording._pick_busy_clip_times(seconds, at=)` 钉时刻（距现在 <10min 自动前移并注明）。
- **多时刻合并语义（关键设计）**：结论取「有明确结论的窗口中最优」——
  新增 `_at_window_rank`，第一键是有无明确结论（正常/警告/异常），第二键才是严重度。
  **纯「未知」窗口不能顶掉已确认的「异常」**（缺证据 ≠ 恢复）；有测试守着。
  各时刻轨迹写入抽检详情（`多时刻: 16:20=未知/未知; 19:05=正常/正常`），单时刻不加前缀。
- **定点模式不自动换时段复检**：复检选点返回 None（双保险：av_probe 也有 guard），
  提示「需补测可增加 --av-at 时刻」。
- **排序**：保留用户给定顺序（不擅自按时间排），首个时刻即初检。
- 测试：`tests/test_av_at.py`（20 例：解析/CLI 贯通/选点钉住/前移/at= 覆盖）+
  `test_av_integration.py` 4 例（多时刻取最优+轨迹、未知不掩盖异常、单时刻无前缀、不触发复检）。
  全量 262 例绿。集成 harness 的 `_pick_busy_clip_times` stub 签名改为 `(seconds, at=None)`。
- 重建 2.3.0 包：解包 PYZ 确认 `--av-at`/`定点抽检` 常量进包，签名与 zip 结构不变（61MB）。
- GUI 不暴露该参数（与 `--av-channels` 同口径，CLI-only）。

## 12. GUI「单路抽检」（结果表选中通道，2026-09-11）

在检查结果通道表加入单通道深度抽检：**点选一行（可多选）→「单路抽检」按钮或右键
「单路深度抽检」→ 只对选中通道重跑音视频抽检 → 行内就地刷新**。

- **核心**：`scan_runner.run_single_av_check(recs, device, options)` —— 复用上次巡检的
  通道记录（track_id / 录像含音频 / 落盘状态都在），不重复状态/落盘全量检查；
  返回记录**副本**由调用方回写 UI，原记录不动。选项强制：deep=True、
  `av_channels=选中通道`、`av_limit=None`、`av_save=False`（单路复查不落盘）。
- **线程**：`ui.scan_worker.SingleCheckWorker`，信号与 ScanWorker 同构
  （log_line / progress_update / check_finished / scan_failed / scan_cancelled），
  复用 `_SignalThrottle`；进行中可点「取消巡检」中止（`_cancel_scan` 会兜到 single_worker）。
- **互斥**：`_worker_busy` 同时看全量扫描与单路抽检两个 worker，反向也互斥
  （同一台 NVR 不允许并发连接/拉流）。
- **回写**：`ChannelTableModel.update_record_fields` 按 **identity** 匹配原记录
  （结果表 model 只存 dict 引用），只 emit 该行 dataChanged，保住排序与选中；
  快速巡检（非深度）结果在抽检完成后自动切出「视频抽检/音频抽检」列。
  大窗（ResultsExpandWindow）也接了同一信号。健康汇总/指标卡**不**因单路复查改动
  —— 诊断动作 ≠ 整机重新巡检。
- **设备定位**：按结果 `ip` 在当前档案里找同名设备；找不到（档案改过）提示重新巡检。
- 测试：`tests/test_single_check.py` 8 例（选项强制/副本语义/多选共享连接/空值/
  连接异常传播/身份回写/按钮启停状态机/面板回写端到端）。全量 **270 例绿**。
- 重建 2.3.0 包并 PYZ 常量校验通过；签名与 zip 结构不变（61MB）。

## 13. 2.3.0 全量代码与功能审查（2026-09-12）

对 2.3.0 全部未提交改动（46 文件 / ~2476 行新增）做了一遍代码+功能审查：
**静态未定义名扫描 + 新功能通路逐条走读 + 变异测试反验**。共发现 **4 项 P1、3 项 P2、3 项 P3**，
全部修复；全量测试 **283 例绿**，静态扫描 0 命中。

### 13.1 P1 — 静默给出错误结论 / 功能失效

| # | 位置 | 问题 | 处置 |
| --- | --- | --- | --- |
| P1-1 | `nvr_core/av_probe.py::_run_av_jobs` | **结果按 `str(track_id)` 建键**。Track 缺 id/Channel 时多路 track_id 都是「未知」，重复键互相覆盖 → **一路的抽检结论被静默写到另一路头上**（两路都"看起来有结果"）。这是本轮最严重的一项：报错方向是"把好的说成坏的/把坏的挂到别人名下" | ✅ 改 **按 `id(rec)` 建键**；定点模式的 `merged`/`best_label`/`notes` 同步改键；4 处调用点（`results.get`/`retry_map.get`×2、`key = id(r)`×2）一并改。新增 2 例回归 + **变异验证**（见 13.4） |
| P1-2 | `nvr_core/scan_runner.py::run_single_av_check` | 选中记录的 `通道` 非数字（如 Track 解析失败的「未知」）时，过滤参数进 `build_nvr` 触发异常 → 被兜底成「过滤无效 → 抽全部候选」→ **点一路却拉了整台 NVR 的所有流** | ✅ 建 nvr **之前**先校验通道号，非法直接 `ValueError("选中记录的通道号无效")`；非法 token（`031`→`31`）走 `parse_channel_list` 归一。回归 `test_single_check_rejects_non_numeric_channel` |
| P1-3 | `ui/scan_worker.py::SingleCheckWorker` | `_run()` 里 nvr 实例建完即关，`self._nvr` 恒为 `None` → 点「取消」只置了标志位，**正在跑的 ffmpeg 拉流停不下来**（要等最长超时） | ✅ `run_single_av_check` 新增 `on_nvr` 回调，GUI 借此持有实例；`_cancel()` 现在真能调到 `nvr.cancel()`。回归 `test_single_check_worker_holds_nvr_and_reports_cancel` |
| P1-4 | `nvr_core/recording.py::_pick_busy_clip_times(at=)` | 定点抽检的时段标签只写"请求的时刻"，**不写实际落点**。当请求时刻距现在 <10min 会被前移到更早，用户看到的仍是原时刻 → 以为抽的是 16:20，实际抽的是 16:15 | ✅ 标签同时给出两者：`定点抽检 09-11 16:20:00(抽检点 09-11 16:20:00)` 或 `…(距现在不足10分钟,已前移至 09-11 13:35:00)`。回归 `tests/test_av_at.py` |

### 13.2 P2 — 可观测性 / 口径一致性

| # | 位置 | 问题 | 处置 |
| --- | --- | --- | --- |
| P2-1 | `cli_report.py` | 报告在**全正常且非 verbose** 时不打印「抽检时段」→ `--av-at` 的**实际落点/前移动作在正常场景下完全不可见**（恰恰是用户最需要确认的情况） | ✅ 表格前统一输出去重后的「抽检时段」（含多时刻轨迹） |
| P2-2 | `ui/panels/results_panel.py` | 本地维护了一份 `_AV_FIELDS` 副本，与 `scan_runner.AV_FIELDS` 会各自漂移（回写字段漏一个就是静默丢数据） | ✅ 改为 `from nvr_core.scan_runner import AV_FIELDS`，单一来源 |
| P2-3 | `nvr_core/util.py` | 新增的 `parse_instant_list` 用了 `List`，但 typing 导入里没有。**当前无运行时影响**（`from __future__ import annotations` 让注解惰性求值，连函数内变量注解也不求值）——属于"现在不炸、拆掉 future import 或调 `get_type_hints` 就炸"的隐患 | ✅ 补进 `from typing import Any, Dict, List, Optional, Tuple`，与文件其余部分一致 |

### 13.3 P3 — 工程一致性

1. **依赖组归位**：`pyinstaller` 从 `[project.optional-dependencies] build`（`uv sync` 默认**不装**，每次 sync 还会把手装的清掉）移入 `[dependency-groups] dev`；删掉 `build_mac.sh` / `build_win.ps1` / `PACKAGING.md` 里三处冗余的 `uv pip install "pyinstaller>=6.0.0"` 补救行。现在 `uv sync` 一步到位。
2. **`channel_table.set_records` 未同步按钮态**：`set_records` 会 reset model（**清空选中**），但没重算「单路抽检」按钮的 enable → 换一批结果后按钮可能停在旧的可用/不可用状态。✅ reset 后补 `_update_single_check_enabled()`。
3. **`scripts/undef_scan.py` 入库**：本轮用来找未定义名的 AST 扫描器收进仓库（`python scripts/undef_scan.py [path]`，有命中则 exit 1），不再是一次性脚本。

### 13.4 验证方式（关键在于"测试真的会红"）

- **静态**：`scripts/undef_scan.py` 全仓 **2414 文件 / 0 命中 / exit 0**。扫描器自身做了变异验证：植入一个未导入的 `FakeConstNotImported` → 1 命中 + exit 1；还原 → 干净。
- **回归**：`tests/test_single_check.py`（15 例，覆盖选项强制、副本语义、多选共享连接、非数字通道拒绝、token 归一、`on_nvr` 暴露与取消、身份回写、按钮状态机、面板回写端到端）、
  `tests/test_cli_report_instant.py`（2 例）、`tests/test_av_integration.py`（25 例，本轮新增 2 例重复 track_id 回归）、
  `tests/test_av_at.py`（28 例，含定点标签"请求时刻 vs 实际落点"）。全量 **283 例绿**（§12 时点为 270）。
- **变异测试（P1-1）**：把 `_run_av_jobs` 与 4 处调用点**整体还原成旧的 `str(track_id)` 键**，两个新用例立刻变红，且失败信息直接暴露病灶 ——
  `ch31 没拿到自己的结论: 多时刻: 14:00=正常/正常; 14:00=正常/正常; 16:00=正常/正常; 16:00=正常/正常; CH64@14:00`
  （一路拿到了另一路的 `CH64@` 详情，轨迹还被并成了 4 条）。还原后 `grep` + `ast.parse` 复核无残留。

### 13.5 复核确认无问题的点

- **单路抽检的"诊断动作"语义**：不回写健康汇总/指标卡是有意设计（诊断 ≠ 整机重巡），实现上 `apply_single_check_result` 只按 identity 写 AV 字段，没碰 `健康状态`，与设计一致。
- **回写用 identity 匹配**：结果表 model 存的是 dict 引用，`update_record_fields` 按 `r is rec` 命中，避免"同通道号多行"误伤；只 emit 单行 `dataChanged`，排序/选中不丢。
- **定点多时刻合并**：`_at_window_rank` 的"明确结论优先 + 严重度次之"保证**纯「未知」窗口顶不掉已确认的「异常」**，有既有用例守着；`av_at` 模式全链路 `pick_retry_clip_times` 返回 None（双保险），不会偷偷换时段。
- **`av_channels` 过滤次序**：过滤发生在「非候选标记之后、`av_limit` 之前」，所以列表内通道的"未配置录像/近期无录像"仍报真实原因，不会被"不在指定抽检通道列表"掩盖。
- **GUI 不暴露 `av_channels`/`av_at`**：与既有口径一致（CLI-only），不是遗漏。

### 13.6 记录在案、本轮不改

1. **GUI 保存会丢手编的 `av_channels`**（§10.3 已记）：`left_panel.current_options()` 从控件重建 `scan_options`。GUI 不暴露该选项，CLI 不受影响，暂不动。
2. **卡片级「警告」与整机「健康状态」可能不一致**（§10.3 已记）：卡片级可见性与整机判定的刻意分工。

### 13.7 打包（环境注意事项）

本轮修复后重建 2.3.0 包时踩到两个**沙箱环境**问题（都不是代码问题）：

- `build_mac.sh` 第一步 `uv sync` 会因 `EEXIST: mkdir` 失败 —— 与 pytest `--basetemp` 同根因（§10.4 已记）。绕法：跳过 `uv sync`，直接用 `.venv/bin/pyinstaller`。
- PyInstaller 默认输出到 `dist/` 时，清理旧的 `dist/NVRStatus`（107 个文件）**被本 shell 的 safe-delete 护栏挡下**
  （`[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":107,"threshold":50,...}`），
  且护栏信息只落在日志里、**PyInstaller 只报 exit 1 无任何 traceback**（很容易误判成"spec 写错了"）。
  绕法同 Swift 那条 `--scratch-path`：换独立输出路径
  `.venv/bin/pyinstaller --noconfirm --distpath /tmp/camdist --workpath /tmp/cambuild NVRStatus.spec`，
  全量重建、不碰工作区。

**产物（2026-09-12 重建）**：

- `dist/NVRStatus-macOS-arm64-2.3.0.zip` —— 64,006,627 B（61MB），455 条，zip 根为 `NVRStatus.app`（与 2.3.0 发布包结构一致），已覆盖旧包。
- 签名：ffmpeg/ffprobe 先单独 ad-hoc 签名，再整体 `codesign --force --deep --sign -`；`codesign --verify --deep --strict` **通过**。
- ffmpeg 回填：`bin/ffmpeg`+`bin/libs`（18 个 dylib）覆盖进 `Contents/Frameworks/bin/`，**产物内 `./ffmpeg -version` = 8.1.2**（可独立运行）。
- **产物含新代码的校验（按字节码，不用字符串搜索）**：`CArchiveReader → PYZ.pyz → ZlibArchiveReader` 取 `code` 对象后递归比对：
  8/8 项命中 —— `_run_av_jobs` 里 `track_id` **已彻底消失**、`id()` 参与建键、`on_nvr` 形参存在于 `run_single_av_check`、
  `ui.scan_worker` 的 `on_nvr` 出现 **2 次**（= 两个 worker 的调用点都在）、`已前移至` 文案、`AV_FIELDS` 导入、`List` 导入。
- `cli_report` **不在 GUI 包内属预期**（spec `excludes` 明确排除，注释写明"CLI 专用"），不是漏打。
- ⚠️ **遗留**：`dist/NVRStatus.app` 与 `dist/NVRStatus`（onedir）仍是 09-11 的旧产物 —— 覆盖它们需要先删旧目录，
  而 >50 文件的删除会被 safe-delete 护栏拦下。**新包已落在 zip 里**；要在正常终端刷新这两个目录，直接跑
  `PATH="/opt/homebrew/bin:$PATH" ./build/build_mac.sh` 即可（那个 shell 里 `uv` 在 PATH、也没有护栏）。
  → 该遗留已在 §14 中随 `dist/` 被清空而**不复存在**。

## 14. 发布构建：NVRStatus 2.3.0（2026-09-30）

**决策**：版本号**保持 2.3.0**（不升号）—— 自 09-12 审查后源码**一行未动**（无文件晚于 09-13 修改），
2.3.0 相对已发布的 v2.2.0 本身就是新版本；升号只会造出内容完全相同的新号。
**范围**：只出本地安装包，不做 commit / tag / push / GitHub Release。

### 14.1 构建前清单（DEPLOYMENT §4.1）

| 检查项 | 结果 |
| --- | --- |
| 版本号一致 | ✅ 7 文件 10 处全为 `2.3.0`；代码/toml/spec 内**无 `2.2.0` 残留** |
| 未把真实密码打进包 | ✅ spec `datas = []`（仅捆图标 PNG）；仓库内无 `nvr_config.json`（只有 `.example`）；源码无硬编码凭据字面量。真实档案在 `~/Library/Application Support/NVRStatus/profiles.json`（0600，在仓库外） |
| 依赖可重建 | ⚠️ `.venv` **已被清空**（`dist/` 同时消失），需重建：`uv sync --no-install-project` + `UV_HTTP_TIMEOUT=600`（`pyside6-addons` 有 288 MiB，默认 30s 超时会断在解包中途） |
| 构建前回归 | ✅ 283 例全绿 + `scripts/undef_scan.py` 2414 文件 0 命中 |

### 14.2 产物与校验

- 构建：`.venv/bin/pyinstaller --noconfirm --distpath /tmp/pkgdist --workpath /tmp/pkgbuild NVRStatus.spec`
  （`--workpath` 走全新目录，避免复用仓库 `build/` 里的旧分析缓存）。
- `dist/NVRStatus-macOS-arm64-2.3.0.zip` —— **63,121,690 B（60M）**，455 条，zip 根为 `NVRStatus.app`。
- `dist/NVRStatus.app` —— **146M**。
- 签名：`codesign --verify --deep --strict` **通过**；产物内 `./ffmpeg -version` / `./ffprobe -version` 均 **8.1.2** 可独立运行（18 dylib 随包）。
- Info.plist：`CFBundleShortVersionString = 2.3.0`（该 plist **没有** `CFBundleVersion`，别用 `&&` 串两条 PlistBuddy 校验，会误判成失败）。
- **字节码比对 8/8 命中**（同 §13.7 的八项）：`_run_av_jobs` 内 `track_id` 已消失、`id()` 参与建键、通道号预校验文案、
  `on_nvr` 形参、`ui.scan_worker` 两处 `on_nvr` 调用点、`已前移至`、`AV_FIELDS` 单一来源、`List` 导入。
  `ui.main_window` / `ui.app` 内含 `2.3.0`，全包**无 `2.2.0` 残留**（PYZ 496 模块）。
- **凭据泄漏反查**：从真实 `profiles.json` 取 10 条凭据候选（密码/IP/账号/档案名），
  ① 明文扫全包 + ② 解压 PYZ 扫 70,917 条字符串常量（明文 grep 对压缩模块无效）。
  **唯一命中为误报**：`device.username`（5 字符）—— 该值同时出现在仓库 4 个源文件
  （`hikvision_status.py` / `config_store.py` / `ui/panels/left_panel.py` / `ui/widgets/device_editor.py`）
  且等于示例配置占位值，是**代码里的默认账号**，不是从档案泄漏的。密码 / IP / 档案名 **0 命中**。
- 离屏启动冒烟：`rc=134`、无输出 —— 与本机 shell 无 WindowServer 的既有基线一致（§10.4），**非本轮回归**。
- 服务器/真机回归验收清单（DEPLOYMENT §7）**未执行** —— 需连真机，属现场动作。


