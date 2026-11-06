# 桌面宠物 WisePet 技术设计 v1.20

- **对应需求**：[FR v1.19](../../FR/v1.19/desktop-pet-PRD-v1.19.md)
- **上一版本**：[v1.19](../v1.19/desktop-pet-tech-design-v1.19.md)
- **状态**：已实现（待评审）

## 0. 定位

四项全部按 DDD 分层落地，且都是**新增旁路能力**：`ui` 不碰仓储、配置经 provider lambda 注入、组装根 `main.py` 统一装配。所有新增配置节 `from_dict` 宽松解析、`getattr` 兜底旧配置文件，任一功能关闭或环境不支持时**行为回落到旧版**。

## 1. 配置层（domain/config_model.py）

新增四个 dataclass，挂进 `AppConfig`（`dnd` 之后），并补 `_int_clamp` 整数钳制助手：

| 节 | 字段（默认） | 解析口径 |
|----|--------------|----------|
| `TtsSettings` | `enabled=False`、`volume=100`、`rate=0` | `volume` `_int_clamp(0,100)`、`rate` `_int_clamp(-10,10)` |
| `NotificationSettings` | `use_system=False` | `_bool` |
| `WeeklySettings` | `enabled=True`、`weekday=6`、`hour=20` | `weekday %7`、`hour %24` |
| `GrowthSettings` | `enabled=True` | `_bool` |

## 2. C30 语音说话（TTS）

| 落点 | 做法 |
|------|------|
| `infra/tts.py::create_synthesizer(platform, *, enabled)` | 平台工厂：`enabled=False`/未知平台 → `NullSynthesizer`；`win32` → `WindowsSynthesizer`；`darwin` → `MacSynthesizer` |
| `WindowsSynthesizer` | `subprocess.run(["powershell","-NoProfile","-NonInteractive","-Command",cmd], input=text.encode("utf-8"), ...)`；命令首句 **`Add-Type -AssemblyName System.Speech`**（否则 `New-Object` 报 TypeNotFound，真机踩过），再 `New-Object SpeechSynthesizer; $s.Volume=..; $s.Rate=..; $s.Speak([Console]::In.ReadToEnd())`；**文本走 stdin 不拼进命令**（防注入）；`stdout/stderr=DEVNULL` 屏蔽子进程报错外泄 |
| `MacSynthesizer` | `say -r <wpm> -- "[[volm: 0.xx]]<text>"`；`wpm = max(80, 175 + rate*8)`，音量走 `[[volm]]` 内联指令 |
| `truncate` / `strip_emoji` | `MAX_SPEAK_CHARS=600` 截断；`_EMOJI_RE` 覆盖象形文字/杂项符号/箭头/变体选择符/零宽连接符，`strip_emoji` 删表情并收敛多余空白 |
| `app/tts_announcer.py::TtsAnnouncer` | `announce(text)`：读 `config().tts` 门控 → `truncate(strip_emoji(...))` 空则不念 → 起后台线程 `synth.speak(volume, rate)`，异常吞掉 |
| `main.py` 接线 | `_on_speak` 与 `_on_chunk` 的 done 分支、`send_message` 成功后各调 `self._tts.announce(...)`；`create_synthesizer(enabled=True)` 装配、门控交给 config |

## 3. C31 通知接入系统通知中心

| 落点 | 做法 |
|------|------|
| `ui/tray_icon.py::show_notification` | `isSystemTrayAvailable()` 为假直接返回 `False`；否则 `showMessage(title, body, icon, max(1000,msec))` 返回 `True`（跨平台，Windows 动作中心/macOS 通知中心） |
| `main._notify_via_system` | `getattr(config.notification,"use_system",False)` 关则返回 `False`；开且托盘可用则系统通知并 `return True`；`_on_speak` 据此**跳过自绘气泡**。提醒进系统通知后不带「知道啦」（R4 取舍，确认语义靠下次间隔重排） |

## 4. C32 回忆录 / 每周周报

| 落点 | 做法 |
|------|------|
| `app/weekly_report.py::WeeklyReportService` | 复用 News/Cheer 骨架：`clock/bus/config_provider/records_provider/store/summarizer/deferral_provider/tokenizer`；`tick` 判 `tm_wday==weekday and tm_hour>=hour` + `week_id` 去重 + 勿扰跳过 → 后台线程 `_run_once` |
| 去重 | `_week_id(ts)` 用 `date.isocalendar()` 生成 `f"{year}-W{week:02d}"`；`weekly.json` 记 `last_week`，同周只推一次、重启不重推；`_inflight` 防并发 |
| 统计 | `summarize_week(records, tokenizer)`：`messages=len(records)`、`active_days`（按 session_id 或日期）、`top_topic`（role=user 词元经 `STOPWORDS`/`has_cjk`/`len>=2` 过滤，需 `count>=2`）；默认 `Tokenizer(bigram_split)` |
| 文案 | `main._summarize_week(stats)` 有 Key 时 AI 润色（≤80字宠物口吻），失败/未配返回 `None` → `local_report(stats)` 本地模板（含 0 消息「安静待在一起」兜底） |
| 推送 | `publish(SpeakRequestedEvent(text, source="system", title="本周回忆录"))`，复用气泡/TTS/系统通知通道；`records_provider=lambda: chat_history.recent_turns(7)` |
| `infra/weekly_store.py` | `weekly.json`，`load()->dict`/`save(dict)`，走 `json_store` 原子写 |

## 5. C33 成长体系

| 落点 | 做法 |
|------|------|
| `domain/growth.py`（纯函数） | `FEED/PET/CHAT/COMPANION_DAY_POINTS = 2/1/1/10`；`LEVEL_BASE_COST=20` 逐级递增门槛（升到 L+1 需 `20*L`）；`level_from_points` while 扣减；`stage_for_level`（Lv4→成长期、Lv10→成熟期）；`GrowthVO.from_points` 带 `level_points/level_need/stage_progress` |
| `infra/growth_store.py` | `growth.json`：`GrowthData(points,companion_days,last_day,fed,petted,chatted)`，`atomic_write` |
| `app/growth_service.py` | `on_feed/on_pet/on_chat` 累加；`tick`→`_touch_day` 跨自然日 +10；`_bump` 检测 `level_from_points` 前后，升级发 `SpeakRequestedEvent`（换形态文案不同）；`__init__` 开机 `_touch_day(publish=False)` 计入首日；一切写失败静默 |
| `ui/stats_dialog.py` | `set_values(..., level, stage)` 底部显示 `成长：LvX · 形态`，无则「成长：–」；`main._show_stats` 传 `self._growth.current()` |
| `main.py` 接线 | 右键 `on_feed/on_pet_head` 在调用 controller 后追加 `growth.on_feed/on_pet`；`send_message` 成功 `growth.on_chat()`；`tick` 调 `growth.tick(now)` |

## 6. 设置窗（ui/settings_dialog.py）

- 新增第五分区 **「陪伴进阶」** `_build_companion_group`：语音开关/音量/语速、系统通知开关、周报开关/推送日/时刻、成长开关。
- `_chat_idle_hide` 等数值框入 `value_widgets`（滚轮只滚页面）；`_weekly_weekday` 用 `_NoWheelComboBox`（周一~周日 data 0..6）。
- `_load_from` 用 `getattr(cfg,"tts",None)…` 回填、`collect` 用 `replace(...)` 构造四节，向后兼容。
- 窗口 `resize(640,600)`、`setMinimumSize(560,460)`（长标签不再截断；高度守 600 以不超小屏工作区）。

## 7. 否决方案

| 方案 | 否决理由 |
|------|----------|
| TTS 文本拼进 PowerShell 命令 | 引号/特殊字符破坏命令且有注入面；改走 stdin |
| 系统通知用第三方 toast 库带按钮 | 引外部依赖、跨平台不一致；先 `QSystemTrayIcon.showMessage`，接受「无知道啦」取舍 |
| 周报联网取数 / 上云汇总 | 违背「本地数据不上传」底线；只读本地按天记录 |
| 成长曲线用外部数值/经验表 | 简单可预期优先（用户能算「再喂几次升级」），纯函数线性门槛足够 |
| 成长/周报直接同步阻塞主线程跑 AI | 复用 News/Cheer 后台线程骨架，UI 不卡 |

## 8. 验证

- 新增单测：`test_tts`（工厂/Win/Mac/截断/`strip_emoji`/`Add-Type` 回归）、`test_tts_announcer`（门控/音量语速/纯 emoji 不念/失败吞掉）、`test_growth`（纯函数换算）、`test_growth_service`（累加/跨日/升级播报/禁用）、`test_weekly_report`（`summarize_week`/`local_report`/推送去重）、`test_domain_contracts`（四节配置默认/钳制/非法回退/往返）、`test_settings_dialog`（五分区 + roundtrip）、`test_tray_icon`（`show_notification` 可用/不可用）、`test_stats_dialog`（等级/形态展示）。
- 全量 **1147 passed**，覆盖率 **94.80%**（≥90 门槛，ui 层按 pyproject 口径排除）。
- 真机待目检：Windows 勾「语音说话」能朗读且不念表情；开系统通知提醒进通知中心；周报按周日 20:00 推；喂食/抚摸/聊天涨分并在陪伴统计显示等级形态。
