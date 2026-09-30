# 海康威视 NVR 状态查询与音视频抽检 — 使用说明

本项目通过 ISAPI 查询 NVR 运行状态，可选做短时 RTSP 音视频抽检。

| 方式 | 入口 | 说明 |
|------|------|------|
| **GUI（推荐给同事）** | `uv run python run_gui.py` | 图形界面、多配置档案、手动设备、安装包 |
| **CLI** | `./nvr` / `./nvr all` | 命令行巡检（**默认查全部设备**；`./nvr 1` 只查一台） |
| **打包** | 见 [PACKAGING.md](PACKAGING.md) | Win / Mac 安装即用 |

---

## 0. 图形界面（GUI）

### 启动

```bash
uv sync
uv run python run_gui.py
```

### 功能

- **多配置档案**：下拉切换；「新建」与「管理」菜单（另存为 / 重命名 / 删除 / 导入 / 导出）
- **手动设备**：每档案可配置多台 NVR（名称、IP、端口、用户、密码、SSL）  
- **一键巡检**：在线、计划、含音频、落盘、硬盘、健康汇总  
- **深度抽检**：可选；需 ffmpeg；优先本地 10:00–18:00；可选保存片段  
- **单路抽检**：巡检完成后，在通道列表点选一行（可多选），点「单路抽检」
  或右键「单路深度抽检」，只对选中通道重跑音视频抽检并就地刷新该行 ——
  复查某一路的音视频不必等整机重新巡检（需 ffmpeg；设备取自当前档案中
  与结果相同 IP 的那台；进行中可点「取消巡检」中止）
- 档案与密码保存在本机用户目录（非安装包内）

配置目录：

- macOS: `~/Library/Application Support/NVRStatus/`
- Windows: `%APPDATA%\NVRStatus\`

打包成 Win/Mac 软件见 **[PACKAGING.md](PACKAGING.md)**。

---

## 1. 环境与依赖

| 依赖 | 说明 |
|------|------|
| Python ≥ 3.11 | 见 `pyproject.toml` |
| [uv](https://github.com/astral-sh/uv) | 运行 `uv run ...` |
| `requests` | 项目依赖，`uv` 会自动安装 |
| `ffmpeg` / `ffprobe` | **仅深度抽检 / 保存片段时需要** |

```bash
# macOS 安装 ffmpeg（如未安装）
brew install ffmpeg

# 进入项目目录
cd /path/to/cam
```

---

## 2. 设备配置

编辑 `nvr_config.json`：

```json
{
  "devices": [
    {
      "name": "NVR1",
      "ip": "192.168.1.64",
      "port": 80,
      "username": "admin",
      "password": "你的密码",
      "ssl": false
    },
    {
      "name": "NVR2",
      "ip": "192.168.1.65",
      "port": 80,
      "username": "admin",
      "password": "你的密码",
      "ssl": false
    }
  ],
  "default": 0
}
```

| 字段 | 说明 |
|------|------|
| `devices` | NVR 列表；`./nvr` 会**顺序查全部**；`./nvr 1` 只查第 1 台 |
| `default` | 保留字段（兼容旧配置）；当前默认行为已改为查全部，不再依赖此项 |
| `ssl` | `true` 时使用 HTTPS |

---

## 3. 快速开始（`./nvr`）

```bash
# 查看帮助与设备列表
./nvr -h

# 日常巡检：顺序查询配置中全部设备（默认）
# 展示：站点摘要 → 每台结论/指标 → 仅异常通道 → 硬盘使用率条
./nvr
./nvr all

# 只查某一台（编号从 1 开始）
./nvr 1
./nvr 2

# 输出全部通道与设备详情
./nvr all --verbose
./nvr 1 -v

# 全部设备 + 深度抽检 / 其它参数（参数会应用到每一台）
./nvr all --deep-av-check --av-seconds 5
./nvr --lookback 30
./nvr all --av-save --av-limit 4
```

说明：

- **不带编号**或写 `all`：按 `devices` 顺序逐台查询，结束后打印站点汇总与完成统计
- **数字编号**：只查指定一台
- **默认展示**为结论前置、异常优先；全绿时不刷 64 路清单
- **`-v` / `--verbose`**：输出全部通道表 + 摄像头清单 + 设备字段
- 附加参数（如 `--deep-av-check`）会应用到每一台
- 管道重定向时 Rich 会自动降级为纯文本（无 ANSI 杂讯）

---

## 4. 检查层级说明

| 层级 | 内容 | 默认是否执行 | 对 NVR 影响 |
|------|------|--------------|-------------|
| L1 配置 | 在线、录像计划、`SaveAudio`、硬盘 | 是 | 仅 HTTP 查询 |
| L2 落盘 | 近 N 分钟 CMSearch 是否有录像片段 | 是 | 仅检索，较轻 |
| L3 深度抽检 | 短时 RTSP 拉流 + ffprobe 音视频轨 | **否**（需开关） | 低并发短回放，**不写 NVR 盘** |

---

## 5. 参数一览

### 5.1 连接参数（`hikvision_status.py` 直接调用时必填）

| 参数 | 默认 | 说明 |
|------|------|------|
| `-i` / `--ip` | （必填） | NVR IP |
| `-v` / `--verbose` | 关 | 全量通道/设备详情（默认仅摘要+异常） |
| `-p` / `--port` | `80` | 端口 |
| `-u` / `--username` | `admin` | 用户名 |
| `-w` / `--password` | （必填） | 密码 |
| `-s` / `--ssl` | 关 | 使用 HTTPS |

使用 `./nvr` 时以上由配置文件提供，一般不必手写。

### 5.2 落盘检查（L2）

| 参数 | 默认 | 说明 |
|------|------|------|
| `--lookback` | `60` | 检查「近多少分钟」是否有录像，单位：分钟 |
| `--no-search` | 关 | 跳过 CMSearch，只查计划/音频配置（更快） |
| `--workers` | `8` | 落盘检索并发数 |

### 5.3 深度音视频抽检（L3）

| 参数 | 默认 | 说明 |
|------|------|------|
| `--deep-av-check` | 关 | 启用深度抽检（短时 RTSP） |
| `--av-seconds` | `6` | 每路抽检时长（秒），**最大 12** |
| `--av-workers` | `2` | 抽检并发，**最大 3**（保护 NVR） |
| `--av-limit` | 全部 | 最多抽检通道数（抽样用） |
| `--av-channels` | 全部 | **只抽检指定物理通道号**，逗号分隔，如 `31,64` |
| `--av-at` | 繁忙时段 | **定点抽检时刻**（本地 `HH:MM`，逗号分隔可多个），如 `19:05,16:20` |
| `--silence-db` | `-80` | 音频 `mean_volume` 低于该值标为「警告」 |
| `--busy-start` | `10` | 优先抽检时段开始（**本地小时**） |
| `--busy-end` | `18` | 优先抽检时段结束（**本地小时**） |

**定点抽检规则（`--av-at`）：**

- 忽略繁忙时段逻辑，直接钉在指定时刻检查；**每个时刻各查一遍**全部候选通道，
  适合区分「持久故障 vs 间歇抖动」（同一通道多时刻都未知 → 先怀疑 IPC 端可恢复故障）
- 指定时刻距现在 **不足 10 分钟**时自动前移到「现在−10分钟」，并在结果里注明
- 时刻必须是**已过去**的时间（那一段录像才存在），未来时刻/写错格式直接报错不静默
- 多时刻时结论取「有明确结果中最优」的一窗，各时刻轨迹写入抽检详情（如
  `多时刻: 16:20=未知/未知; 19:05=正常/正常`）
- 定点模式**不自动换时段复检**；需要补测就多给几个时刻
- 与 `--av-channels` 叠加 = 只查指定通道的指定时刻（单通道定点复查）
- 抽检日与 `--busy-days-ago` 同口径：`--busy-days-ago 1 --av-at 19:05` = 昨天 19:05

**繁忙时段规则（默认 10:00–18:00 本地时间）：**

- 抽检点距现在 **至少 10 分钟**（避开刚写入、尚未封口的回放，减少 RTSP 超时）
- 初检视频/音频异常或未确认的通道，会 **换一个时段再抽一次**；复检通过则覆盖结果，仍失败则保留异常并记下两次时间
- 当前在时段内 → 取今日 `10:00`～现在减 10 分钟；进入繁忙不足 10 分钟则改用昨天
- 当前早于 10:00 → 取**昨天** 10:00–18:00（优先约 14:00）
- 当前晚于 18:00 → 取**今天** 10:00–18:00（优先约 14:00）

RTSP 的 `starttime`/`endtime` 使用**设备本地墙钟**（与画面 OSD 一致），避免出现「逻辑是下午、画面显示凌晨 5 点」的问题。

### 5.4 保存抽检片段

| 参数 | 默认 | 说明 |
|------|------|------|
| `--av-save` | 关 | 保存抽检 mkv；**会自动开启深度抽检** |
| `--av-save-root` | `项目/av_samples` | 保存根目录 |

保存路径示例：

```text
av_samples/
  └── 20260729_134613/                    # 按运行时间命名
        ├── ch1_Room_51-1_track101_134536.mkv
        └── ch2_大办公室-1_track201_134538.mkv
```

文件名：`ch{通道}_{名称}_track{trackId}_{本地时分秒}.mkv`  
`av_samples/` 已在 `.gitignore` 中忽略。

---

## 6. 常用命令示例

### 6.1 日常巡检（推荐）

```bash
# 顺序查询全部设备（默认）
./nvr
./nvr all

# 只查某一台
./nvr 1
./nvr 2
```

输出包含：设备/系统状态、健康汇总、摄像头在线、录像计划与含音频、近 60 分钟落盘、硬盘容量。

### 6.2 只查配置、不查落盘（最快）

```bash
./nvr --no-search          # 全部设备
./nvr 1 --no-search        # 仅第 1 台
```

### 6.3 深度抽检（不落盘保存）

```bash
# 全部设备、每台全通道（耗时较长，并发 2，每路约 5s）
./nvr all --deep-av-check --av-seconds 5 --av-workers 2

# 全部设备、每台只抽 4 路（适合快速抽查）
./nvr all --deep-av-check --av-limit 4 --av-seconds 5

# 只对第 1 台做深度抽检
./nvr 1 --deep-av-check --av-seconds 5

# 只抽检指定通道（32 路 NVR 上的 31、64 路）——排查某几路时用它，
# 不必等全量 64 路跑完
./nvr 1 --deep-av-check --av-channels 31,64 --av-seconds 6

# 单通道定点复查：只查 31 路、钉在 19:05 和 16:20 两个时刻各查一遍——
# 复核「音频间歇未知」是不是 IPC 重启后已恢复
./nvr 1 --deep-av-check --av-channels 31 --av-at 19:05,16:20

# 查昨天 19:05（配合 --busy-days-ago）
./nvr 1 --deep-av-check --av-channels 64 --busy-days-ago 1 --av-at 19:05
```

> `--av-channels` 只影响**抽检范围**，不影响落盘/状态检查——所有通道仍会做
> 计划与近期录像检查，只有未列出的通道不拉流，其抽检结果标为「跳过」。
> 与非候选（未配置录像 / 近期无录像）不同，过滤理由会写明「不在指定抽检通道列表」。
> 两个参数可以叠加：`--av-channels 31,64 --av-limit 1` = 在这 2 路里最多抽 1 路。

### 6.4 深度抽检并保存片段

```bash
# 全部设备全量保存
./nvr all --av-save --av-seconds 5 --av-workers 2

# 全部设备、每台抽样保存 4 路
./nvr all --av-save --av-limit 4

# 只保存第 1 台
./nvr 1 --av-save --av-seconds 5

# 自定义保存根目录
./nvr all --av-save --av-save-root /data/nvr_clips
```

### 6.5 自定义繁忙时段

```bash
# 例如优先 9:00–17:00（应用到全部设备）
./nvr all --deep-av-check --busy-start 9 --busy-end 17

# 保存时同样可用
./nvr all --av-save --busy-start 10 --busy-end 18
```

### 6.6 调整音频静音阈值

```bash
# mean_volume 低于 -70dB 记警告（更严）
./nvr all --deep-av-check --silence-db -70
```

### 6.7 不经过 `./nvr`、直接调脚本

```bash
uv run hikvision_status.py \
  -i 192.168.1.64 -p 80 -u admin -w '密码' \
  --deep-av-check --av-save --av-limit 4
```

---

## 7. 输出字段含义（简要）

| 项目 | 含义 |
|------|------|
| 摄像头在线 | 以 `InputProxy/channels/status` 的 `online` 为准，并用 `chanDetectResult`（`检测状态`）交叉校验：`online` 缺失时用 `connect`/`notExist` 等补判；两者都非明确取值记「未确认」而非离线。在线但通道级检测异常（IP 冲突 / 网络不可达等）另记「通道检测异常」 |
| 含音频 | 录像计划扩展字段 `SaveAudio`（配置层） |
| 录像 | 综合计划 + 落盘（+ 深度抽检视频是否失败） |
| 落盘 | 近 `--lookback` 分钟 CMSearch 是否有片段（检索失败为「未知」） |
| 视频抽检 | 短时流中是否有可解析视频轨 |
| 音频抽检 | 独立短时拉音频轨：有声为「正常」；无音轨为「异常」；电平过低为「警告」；超时未能确认为「未知」 |
| 健康状态 | 良好 / 警告 / 严重（磁盘满多为警告；离线/无落盘/抽检失败等更重）。「落盘未知」「音频抽检未知」多为瞬时抖动：**部分通道**未知只进统计与结果区、不参与判定；**实际检查到的通道全部未知**（整体失败）才计警告 |

---

## 8. 安全与负载约定

为避免影响录像机正常录像与存储，脚本默认遵守：

1. **不做**整段 HTTP `ContentMgmt/download`（单段可达约 1GB）
2. 深度抽检仅 **数秒 RTSP**、`ffmpeg -c copy`，临时文件在本机 `/tmp`，用后删除
3. 抽检并发默认 **2**、上限 **3**
4. 单路时长默认 **6s**、上限 **12s**
5. 片段只写入本机项目目录（`--av-save`），**不写 NVR 硬盘**
6. 日常巡检不加 `--deep-av-check` / `--av-save`，仅 HTTP 状态查询

建议：

- 工作日白天做人流较多时段的深度抽检（默认已优先 10:00–18:00）
- 全量 64 路抽检大约数分钟～十几分钟，错峰或先用 `--av-limit` 抽样

### 8.1 凭证安全（A7 / B7）

- **macOS / Windows**：密码优先写入系统 keyring（Keychain / Credential Manager），
  `profiles.json` 中对应字段为空；读取档案时自动补全到运行时。
- **其它平台或 keyring 不可用**：回退为 JSON **明文**保存（与 v1 行为一致）。
- 请勿把 `profiles.json` / `nvr_config.json` / **导出的配置 JSON**（导出为便于迁移
  可能含明文密码）提交、共享或上传到不受控环境。
- 多用户机器：为系统账号设置登录密码，并收紧目录权限（macOS 保持
  `~/Library/Application Support/NVRStatus/` 仅本人可读写）。

| 平台 | profiles.json 路径 | 密码存储 |
|------|--------------------|----------|
| macOS | `~/Library/Application Support/NVRStatus/profiles.json` | Keychain（优先） |
| Windows | `%APPDATA%\NVRStatus\profiles.json` | Credential Manager（优先） |
| 其它 | `~/.config/NVRStatus/profiles.json` | JSON 明文 |

---

## 9. 项目文件

| 路径 | 说明 |
|------|------|
| `nvr` | 包装脚本：选设备 + 转发参数 |
| `nvr_config.json` | 设备列表与账号 |
| `hikvision_status.py` | 主程序 |
| `av_samples/` | 抽检片段输出（可选，gitignore） |
| `USAGE.md` | 本文档 |
| `pyproject.toml` / `uv.lock` | Python 依赖 |

---

## 10. 故障排查

| 现象 | 处理 |
|------|------|
| 深度抽检提示无 ffmpeg | `brew install ffmpeg` |
| 全部落盘未知 / 超时 | 检查网络、账号、IP；降低 `--workers` |
| 画面时间不对（旧版本） | 已按设备本地时区写 RTSP；请用修复后脚本重新 `--av-save` |
| 繁忙时段未命中 | 日志会提示回退近期片段；确认该通道当日 10–18 是否有录像 |
| 磁盘 100% 警告 | 常见于循环覆盖；硬盘状态仍为 ok 时一般可继续录 |

查看脚本全部参数：

```bash
uv run hikvision_status.py -h
./nvr -h
```
