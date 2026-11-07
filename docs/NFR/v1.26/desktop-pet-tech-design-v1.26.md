# 桌面宠物 WisePet 技术设计 v1.26

- 版本：v1.26
- 状态：已实现（待评审）
- 日期：2026-11-07
- 对应需求：[FR v1.25](../../FR/v1.25/desktop-pet-PRD-v1.25.md)（C42）
- 性质：落地登记，不回改历史版本

## 1. P0 落点

| 文件 | 改动 |
|---|---|
| `tools/package.py` | `EXCLUDE_MODULES` 裁剪 27 个未用 Qt/标准库模块（**不排 lxml/PIL**，python-pptx 依赖）；PNG/图标 `ZIP_STORED` 免二次压缩；`verify_bundle()` 自检 exe/素材/文件数/体积 + `--verify` + 打包结束强制自检；`clean()` 清掉所有 `dist/WisePet-*` 目录与旧包 |
| `.github/workflows/build.yml` | 上传/发布路径从通配 `dist/WisePet-*` 改成精确 `*.zip` / `*-setup.exe` / `*.dmg`，不再把历史解压目录当 artifact |
| `infra/telemetry_store.py`（新） | `telemetry.json`：install_id / first_seen / `days{事件名:计数}` / `firsts`；`crash.log` 独立追加；`reset()` 供 app 层清空而不反向 import infra |
| `app/telemetry_service.py`（新） | `compute_metrics()` 纯函数算 TTFV / 活跃天数 / D7 / 提醒关闭率 / 功能渗透 / 崩溃数；`track/once/clear/prune(30天)/install_crash_hook/record_crash`；崩溃堆栈正则脱敏 |
| `tests/integration/test_package.py`（新，6 项） | 裁剪白名单、产物校验、历史目录清理、分级压缩 |
| `tests/unit/test_telemetry.py`（新，7 项） | 指标、隐私边界、首次事件、清空、裁剪、崩溃脱敏 |

`main.py` 接线：`__init__` 建 `TelemetryService` 并装崩溃钩子，`start()` 记 `app_start/first_seen`，
`_on_chunk(done)` 记 `first_chat_done`，`_handle_quick_reminder` 记 `first_reminder_done`，
`_on_speak` 记 `reminder_shown`、`_ack_reminder` 记 `reminder_dismissed`。

## 2. P1 落点

| 文件 | 改动 |
|---|---|
| `ui/command_palette.py`（新） | 贴纸风命令面板，`COMMANDS`（16 条）由组装根注册；子串过滤 / 回车执行 / Esc 关闭 / `executed` 信号 |
| `app/data_steward.py`（新） | `render_records()` 纯函数（md/json）、`export_all/export_day/delete_day/usage/clear_telemetry` |
| `infra/sqlite_chat_repo.py` | 新增 `delete_day(day)`，对应旧「删某天 JSON 文件」语义 |
| `ui/tray_menu.py` | 新增「命令面板…」入口 + `status_action`（disabled 常驻状态）+ `set_companion_status()` |
| `app/quiet_budget.py` / `config_model` / `settings_dialog` | `overflow_mode(block/trickle)` + `trickle_every`：额度满后每 N 次拦截放行 1 条，放行不重复计数；默认 block 保持既有契约 |
| `tools/verify_assets.py`（新） | 纯标准库读 PNG 头：10 动作 × 6 帧、256×256 RGBA、帧间字节互异；`--skin/--strict/--assets` |
| `main.py` | `COMMANDS`、`_run_command()` 16 分支、`_companion_status_text()`、`_report()`、`DataSteward/CommandPalette` 装配；`_apply_scene` 记 active scene 并刷新状态 |

**状态常驻**：`_companion_status_text()` 输出「当前场景 / 新闻鼓励周报开关 / 今日推送 N/M / 免打扰」，
在 `__init__`、`apply_config`、`_apply_scene`、`_on_speak` 后刷新 —— 上一轮「弹窗全没了」的
根因（场景包 + 额度静默）现在不用等出问题就能看见。

## 3. P2 落点

| 文件 | 改动 |
|---|---|
| `infra/product_store.py` | `MemoryStore` 加 `history` 时间线（`save(facts, history)` 兼容旧调用）、新增 `ActionAuditStore` |
| `app/long_term_memory.py` | `learn_from()` 追加 `{day,key,value}`、`timeline()`、关掉记忆时不暴露、`clear()` 连时间线一起清 |
| `app/action_audit.py`（新） | `record/recent/clear`，只记类型/目标/结果与时间，不记正文 |
| `main.py::_handle_action` | 成功 / 拒绝 / 取消 / 失败四个分支全部留痕 |
| `domain/config_model` / `app/pet_controller` / `ui/settings_dialog` | `PetParams.activity` 白名单 `quiet/normal/lively`；`_random_idle_gap()` 乘 1.8 / 1.0 / 0.6；设置窗「活泼度」下拉 |
| `infra/platform_idle.py`（新） / `domain/product.py` / `main.py` | Windows `GetLastInputInfo` 取空闲秒数（失败/非 Windows 返回 None）；`BehaviorContext.idle_s`；`context_hint` 空闲 ≥600s 返回 REST |

## 4. 否决方案

| 否决 | 理由 |
|---|---|
| 排除 `lxml` / `PIL` 来瘦身 | python-pptx 传递依赖，排除会让打包版的「生成 PPT」静默坏掉 |
| 换 `TimedRotatingFileHandler` / 改交付格式 | 与本批无关，日志治理已在 v1.24 完成 |
| 埋点上报云端或记录消息内容 | 与「本地不上传、BYOK」底线冲突，且会炸掉隐私卖点 |
| 改 `Ctrl+Alt+P` 热键语义为命令面板 | 用户已习惯一键直达聊天，改成面板会多一步；命令面板走托盘菜单入口 |
| 默认把额度策略改成 trickle | 会破坏既有「超额拦截」契约与用户预期；做成可配置、默认 block |
| 素材校验用 PySide6 解码像素 | 慢且把离线工具绑到 Qt；只读 PNG 头与字节即可 |
| 天气 / 日历外部 API 做因果 | 引入联网依赖与隐私口径；先用系统空闲等本机事实 |
| 移除旧 `ChatLogRepository` / 改埋点写到聊天库 | 前者丢迁移回退，后者混淆结构化指标与聊天正文 |

## 5. 验证

- 新增 **37 项**回归：打包 6、埋点 7、数据管家 7、命令面板 3、素材校验 5、
  P1/P2 增量 9；既有 1229 项零改动通过。
- 全量 **1266 passed / 0 failures / 0 errors**（JUnit），覆盖率 **94.37%**
  （`fail_under = 90`，4103 stmts / 231 miss），改动文件 lints 0。
- `python tools/verify_assets.py` 对真实素材：`cat / dog / monkey` 三套均
  「10 动作 × 6 帧合格，合计 0 项问题」。
- **真机待目检**：命令面板搜索与执行、托盘常驻状态、导出文件可打开、
  `trickle` 降频观感、活泼度三档差异、空闲 10 分钟后安静。
