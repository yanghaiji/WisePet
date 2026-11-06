# 桌面宠物 AI Bot 技术方案 v1.1（MVP 实现偏差登记）

- **版本**：v1.1
- **状态**：待评审
- **对应需求**：docs/FR/v1.0/desktop-pet-PRD-v1.0.md（已确认，MVP 范围未变）
- **基线文档**：docs/NFR/v1.0/desktop-pet-tech-design-v1.0.md（架构与设计意图以 v1.0 为准）
- **文档性质**：**实现偏差登记 + 细节补全**。按 docs/规约.md 与 NFR/README 约定，修订不回改 v1.0 历史文件，故升版本新增本文件；v1.0 中未被本文覆盖的章节（§1~§3、§5~§7 的决策与风险表）继续有效。

---

## 1. 为什么要出 v1.1

MVP 编码完成后，实际落地的目录结构、少数技术方案与 v1.0 存在**实现层面的偏差**（架构分层、依赖方向、领域模型均未变）。这些偏差若不登记，后来者按 v1.0 读代码会找不到文件、或误以为单实例仍用 QLocalServer。本文只做一件事：**让文档与代码一致**，并补齐 v1.0 未写清的线程模型与测试基线。

| 类别 | 条目 |
|------|------|
| 目录/命名/依赖边偏差 | §2（8 项） |
| 方案替换 | §3 单实例、§4 默认配置来源 |
| 职责边界确认 | §5 工作区几何 vs 全屏检测 |
| 设计补全 | §6 线程与事件桥、§7 领域事件实际契约、§8 端口与 infra 补充件 |
| 实现期修正确认 | §9 交互/配置生效时序 |
| 度量基线 | §10 覆盖率与测试、§11 未实现/降级项 |
| 缺陷登记 | §12 验收期发现并修复的真实缺陷 |

## 2. 分层与文件清单偏差（对照 v1.0 §4）

| # | v1.0 规划 | 实际落地 | 理由 |
|---|-----------|----------|------|
| 2.1 | `domain/model/pet.py`、`vitals.py`、`position.py` 分包 | 扁平 `domain/pet.py`、`domain/vitals.py`、`domain/position.py` | 单文件 <100 行，`model/` 只多一层目录而无分组收益 |
| 2.2 | `domain/ports/`（目录，按端口分文件） | 单文件 `domain/ports.py`（4 个 Protocol + `AIConfig`） | 端口总数少，分文件反而增加跳转成本；`runtime_checkable` 便于契约测试 |
| 2.3 | 未列出 | **新增** `domain/config_model.py` | 承载 §4 的默认值单一来源与 `from_dict` 宽松解析（v1.0 §6 的 JSON 结构对应类型化 dataclass） |
| 2.4 | 未列出 | **新增** `domain/timeutil.py`（`is_in_night`，支持跨午夜）、`domain/errors.py`（`AIGatewayError` 及 kind 常量） | 夜间衰减判定与错误分类需被 domain/app/infra 共用，且必须零 Qt 依赖 |
| 2.5 | `infra/single_instance.py` | 更名 `infra/instance_guard.py`（与 §3 方案一致） | 名称与实现语义（守卫端口占用）对齐 |
| 2.6 | 未列出 | **新增** `infra/json_store.py`（原子写 + 损坏备份）、`infra/system_clock.py`（`Clock` 实现）、`infra/logger_setup.py`（滚动日志 + `SecretMaskFilter`） | 多个 JSON 仓储共用写盘/损坏恢复逻辑，避免复制粘贴；日志脱敏属安全要求，需独立可测单元 |
| 2.7 | `ui/` 6 个组件 | 实有 9 个：另有 `ui/qt_bridge.py`、`ui/ai_worker.py`、`ui/screen_bounds.py` | 见 §5、§6 |
| 2.8 | 依赖方向只画了 ui → app → domain ← infra | 实际允许一条有界例外边 **ui → infra**：`settings_dialog.py` 直接用 `config_repo.mask_key` 与 `openai_client.probe` | 两者均为无状态工具（一个格式化、一个独立探活请求），不经 app 转发不会引入业务判断；若要链路严格纯净，二期可把 `probe` 改为组装根注入的回调 |

`app/` 层保持 v1.0 的 `pet_controller.py` / `chat_service.py` / `reminder_service.py` / `event_bus.py` 四件，无偏差。

## 3. 单实例互斥：QLocalServer → 回环端口 bind（替换 v1.0 §4）

- **实现**：`infra/instance_guard.py`，`socket.bind(("127.0.0.1", DEFAULT_PORT=45789))` + `listen(1)`；`OSError` 即判定"已有实例"，`acquire()` 返回 False。
- **变更理由**：`QLocalServer` 在 Windows 走命名管道、在 macOS 走文件系统 socket，两者的残留清理与权限语义不一致（macOS 异常退出后残留 socket 文件会导致二次启动失败）。回环端口 bind 由内核管理，进程退出即释放，**跨平台行为一致**且实现约 40 行。
- **约束**：守卫实例必须在进程存活期持有引用（`main.py` 的模块级 `_instance_guard`），否则被 GC 关闭套接字后互斥失效。
- **失败路径**：`main()` 在 `InstanceGuard.acquire()` 失败时弹 `QMessageBox` 提示"已有一个桌宠在跑"并返回码 1（PRD M1 异常项）。
- 端口固定值属工程约定而非业务阈值，故留在 infra 常量而未进 `AppConfig`。

## 4. 取消 `config/default.yaml`，默认值由 dataclass 承载（替换 v1.0 §4/§6）

- v1.0 在 §4 目录树中列了 `config/default.yaml`（§6 已注明"或使用内置常量"）。**实际不做**：默认值全部写在 `domain/config_model.py` 的 `AppConfig / AIConfig / PetParams / VitalsSettings / ReminderSettings` dataclass 字段默认值上。
- **理由**：默认值出现两处（yaml + dataclass）即双源真相，改一个忘另一个会造成"用户看到的默认值"与"落盘值"不一致；dataclass 单源同时能被 `from_dict` 直接引用作回落。
- **兼容**：`AppConfig.from_dict` 对缺失/类型非法字段逐个回落默认值（`_num` 兜底 `TypeError/ValueError`），因此旧文件或手工编辑的残缺文件均可加载；解析失败整份文件由 `infra/json_store.py` 改名为 `*.bak-{ts}` 后写默认（PRD M5 异常项）。
- 用户配置仍存 `~/.ai-bot/config.json`（含 api_key，不入项目、不入 git），状态快照 `~/.ai-bot/pet_state.json`。

## 5. 工作区几何 vs 全屏检测的职责划分（正式确认 v1.0 §4 括注）

- **工作区几何归 ui 层**：`ui/screen_bounds.py::ScreenBounds` 经 `QGuiApplication.primaryScreen().virtualGeometry()` 提供 `geometry()` / `bounds_for(w,h)` / `clamp()` / `watch_changes()`；宠物窗、气泡、输入条共用同一个实例（`main.py` 持有一份并注入），避免各自取屏幕导致多屏基准不一致。无屏幕实例（offscreen/异常）时回落 `UNBOUNDED` 而非抛错，保证测试与降级路径可用。
- **`infra/platform_util.py` 只做两件事**：全屏检测（Windows ctypes；macOS 由 `NullFullscreenDetector` 恒返回 False）与纯函数 `rect_covers_workarea`（容差 `_FULL_SCREEN_TOLERANCE=4`）。**不读工作区几何**——v1.0 §4 的括注在此确认。
- **注入方式**：`PetController(bounds_provider=lambda: screen.bounds_for(PET_SIZE, PET_SIZE))`，app 层不感知 Qt 类型，只拿 `(x1,y1,x2,y2)` 元组；`screen.geometryChanged` → `PetWindow.clamp_into_screen()`（PRD M1 边界：分辨率变化后位置重钳制）。

## 6. 线程模型细化（补全 v1.0 §2 / D1）

v1.0 只写"QThread worker 跑 httpx 同步流，经 Signal 回主线程"。实现落地为**一个消费线程 + 一个事件桥**，全部 Qt 跨界集中在这两处：

| 组件 | 职责 | 关键点 |
|------|------|--------|
| `ui/ai_worker.py::AiWorker` | 常驻消费 `ChatService` 队列 | v1.0 设想 `while chat_service.process_next(): pass`；实际为 `while not isInterruptionRequested()` + 空队列 `msleep(IDLE_POLL_MS=100)` 轮询。原因：`send()` 随时可能从主线程入队，一次性循环会让线程反复起停且入队时机撞上"线程已退出"。`run()` 内 try/except 保证消费异常**绝不退出线程** |
| `ui/qt_bridge.py::QtEventBridge` | EventBus → `Signal(object)` | `bus.subscribe(None, ...)` 通配订阅全部领域事件；`emit` 线程安全，接收方按队列连接在主线程执行——**全项目唯一跨线程点** |
| `ui/qt_bridge.py::EventRouter` | 事件 → 处理器分派 | 按 `isinstance` 分派表（含子类匹配）而非事件名字符串；单个处理器异常被捕获并 `logger.exception`，不级联影响其它订阅者 |

- 取消与退出：`AiWorker.cancel()` = `chat_service.cancel_inflight()` + `requestInterruption()`；`main.py` 在 `aboutToQuit → shutdown()` 里 `cancel()` → `wait(2000)`（PRD M4 异常：退出不等在途请求）。
- 超时只在网关一处生效：`AIConfig.timeout_s` 传入 `httpx.Client(timeout=...)`，worker 与 ui 均不另起计时器（v1.0 D1 保持一致）。
- worker 线程**绝不触碰控件**：所有 UI 更新路径为 `worker → EventBus → bridge.event(主线程) → EventRouter → 控件`。
- 桥销毁竞态：`emit` 外层捕获 `RuntimeError` 丢弃事件（`shiboken6.delete` / 退出时序下不崩）。

## 7. 领域事件的实际契约（对照 v1.0 §3.5）

| v1.0 事件 | 实现差异 |
|-----------|----------|
| `VitalsChangedEvent` | 字段扁平化为 `pet_id, hunger, mood, energy, changed, threshold_crossed`（不携带 VO 实例，便于 ui 直接读与测试断言） |
| `BehaviorChangedEvent` | 一致（`pet_id, old, new`） |
| `ChatReplyChunkEvent` | 增加 `reply` 字段：`done=True` 时携带**完整回复**，ui 以它做终值刷新，避免依赖节流缓冲区顺序假设 |
| `ChatErrorEvent` | 一致（`kind, detail`）；`kind` 取值的唯一来源是 `domain/errors.py::AIGatewayError` 的字符串常量 `AUTH/NETWORK/RATE/TIMEOUT/SERVER`（构造时校验未知 kind 抛 `ValueError`），ui 层不比较字面量 |
| `ReminderDueEvent` | 一致（`kind, text`）；播报与"知道啦"经 `SpeakRequestedEvent(source="reminder")` 承载 |
| `HelpRequestEvent` | 一致（`pet_id, reason`）；ui 不直接订阅它，由 `PetController` 转成 `SpeakRequestedEvent(source="help")`，从而求助/提醒/AI 回复共用一条气泡通道 |
| **新增** `SpeakRequestedEvent` | `text` 空白时构造即 `ValueError`（不变量：不发空气泡）；`source ∈ {system, reminder, help, ai, fallback}` 决定气泡驻留时长与是否带"知道啦" |

## 8. 端口与 infra 补充件（对照 v1.0 §3.6）

- `AIChatGateway.stream_chat(messages, cfg)` 实现为 `infra/openai_client.py::OpenAICompatClient`；`AIConfig` 落在 `domain/ports.py` 并增加两个派生属性：`chat_endpoint`（自动补 `/chat/completions`、容忍尾斜杠）、`configured`（api_key 非空白）。
- `Clock` 端口要求 `now()` 与 `monotonic()` 两个方法：`now()` 驱动业务（衰减、夜间、提醒到期），`monotonic()` 只用于落盘节拍（不受系统时间回拨影响）。测试分别用 `FakeClock` 注入。
- **新增非端口工具** `infra/openai_client.py::probe(cfg)`：设置窗"测试连接"用的极小非流式请求（`max_tokens=1`、`PROBE_TIMEOUT_S=10`），与正式调用**共用同一套状态码→错误分类映射**（`_raise_status_code`），因此探活失败原因与真实失败一致可信。
- 素材加载 `infra/frame_loader.py::FrameAssetLoader`：`frames(action)` 按文件名排序返回帧路径；新增 `missing_actions()` 供启动期一次性告警（不阻塞，占位渲染兜底）。

## 9. 实现期确认的时序与生效约定（v1.0 §5 未展开部分）

| 场景 | 实际约定 |
|------|----------|
| 发送消息 | **入队成功才进入 THINKING**：`ChatService.send()` 返回 False（空白消息）时不改状态、不开气泡（否则宠物被卡在思考态） |
| 提醒气泡确认 | `SpeechBubble` "知道啦" → `ReminderService.notify_ack(kind)` + `PetController.on_action_finished()`（气泡关闭即状态回 IDLE，不留 SPEAKING） |
| 设置保存生效 | `apply_config()` 替换 `AppConfig` 实例（各组件经 `config_provider` 取当前值，无需重启）→ 落盘 → 刷新 `ChatSession.max_turns` → 提醒逐类 `set_enabled`/`set_interval` |
| 已关闭的提醒 | `ReminderService.set_interval()` **仅在 `item.enabled` 时重新排期**；否则保存设置会把用户刚关掉的提醒"叫活" |
| 间隔下限 | 表单硬性下限 = `ReminderSettings.min_interval_min`（默认 5min），service 层再校验一次（越界抛 `ValueError`，对话框红字提示） |
| 气泡驻留 | `DURATION_MS_BY_SOURCE`：ai 12s / reminder 60s（PRD US-5）/ help 10s / fallback 6s / system 5s；非固定类来源按文本长度追加（`3s + 0.05s/字`，取与来源上限的较小者） |
| 流式节流 | `CHUNK_THROTTLE_MS = 80`（v1.0 R3 落地），`end_stream` 立刻停止节流并以完整回复刷新 |
| 状态落盘 | 单个 `QTimer(1000ms)` 同时驱动 `controller.tick` / `reminder.tick` / 窗口同步；每 `vitals.save_interval_s`（默认 60s，按 `monotonic` 判定）落盘一次，`aboutToQuit` 再落盘一次 |
| 人设模板 | `system_prompt.format(name=…, vitals_summary=…)`；占位符写错时降级为"原文 + 换行拼状态摘要"，不抛异常 |
| api_key 安全 | 界面：`QLineEdit.Password` 回显 + `mask_key(已存值)` 作 placeholder，留空即保留原值；日志：`SecretMaskFilter` 对 `sk-*`/`Bearer *` 落盘脱敏（含 `%`格式化参数与 dict 参数值） |

## 10. 测试与覆盖率基线（对照 v1.0 §8）

- 执行环境：**统一用项目内 `.venv`**（PySide6 6.12 / pytest-qt 4.5 / respx 0.23 / pytest-cov），与 AGENT.md §2 一致。
- 规模：`python -m pytest` **266 项全绿**（unit 领域/应用 + integration infra + ui 冒烟）。
- 覆盖率（`--cov`，ui 层不入门槛）：domain **100%**、app 99%（仅 `pet_controller.py:240-241` 防御分支未触达）、infra 99%（`json_store.py:53-56` 为 POSIX `chmod` 分支，Windows 不可达；`platform_util.py:57` 为 Win32 API 成功返回后的比对行，需真实全屏前台窗口），合计 **99%**。
- 门槛固化：`pyproject.toml [tool.coverage.report] fail_under = 90`，高于规约底线（domain ≥80% / app ≥70%）并留出平台专属分支余量。
- ui 冒烟策略：`QT_QPA_PLATFORM=offscreen`，只断言"控件属性/信号发出/组件与 controller 的调用契约"，行为判断仍由 app 层单测负责；`tests/ui/conftest.py` 复用 `tests/unit/fakes.py` 的 `FakeClock/FakeGateway`。
- v1.0 §8 提到的 hypothesis 未引入（边界已由参数化与显式矩阵用例覆盖，避免新增依赖与不确定性）。

## 11. 未实现 / 降级项（进入 Phase D 或待真机验证）

| 项 | 现状 | 归属 |
|----|------|------|
| 系统托盘图标 | **未做** `QSystemTrayIcon`；右键菜单 `ui/tray_menu.py::PetMenu`（QMenu：设置/投喂/抚摸/隐藏/退出）承担同等入口职责，模块名沿用 v1.0 叫法 | 可接受，若要托盘图标属 FR v1.1 |
| macOS 全屏勿扰 | `NullFullscreenDetector` 恒 False（PRD 已接受降级） | Phase D / D4 |
| 打包与图标 | 未做 PyInstaller、代码签名 | Phase D / D1 |
| 素材 | `assets/pet/<action>/` 为程序生成的纯色占位 PNG（8 个动作目录 × 4 帧，128×128 透明底），正式美术不在本期 | Phase D / D2 |
| 性能读数（冷启动 <3s / 空闲 CPU <3% / 内存 <300MB / 24h 常驻） | 冷启动已在 Windows 真机实测：**0.42s**（取 `main()` 的 cold start 日志）；CPU/内存读数与 24h 常驻未采集 | Phase C1，需人工记录 |
| 双平台手测清单 | Windows 已真机启动跑通（无异常），但六项未逐条人工确认；macOS 无验证机 | Phase C2，需人工执行 |

## 12. 验收期发现并修复的真实缺陷（登记以免回归）

| 缺陷 | 现象 | 修复 | 防回归用例 |
|------|------|------|------------|
| 空白消息卡住状态 | `send_message` 先 `start_thinking()` 再 `chat.send()`，空文本入队失败仍进入 THINKING（宠物僵住） | 改为**入队成功才进思考**（§9 第 1 行） | `tests/ui/test_main_assembly.py`：空白消息不改状态/不开气泡 |
| 已关提醒复活 | `ReminderService.set_interval()` 无条件重新排期，保存设置后被关掉的提醒又活 | 仅 `item.enabled` 时排期（§9） | `tests/unit/test_reminder_service.py::should_not_schedule_when_kind_disabled` 直接断 `_due_at` 无该键（因 `tick()` 本身就跳过禁用项，只看播报无法区分新旧行为） |
| 数值参数日志被丢 | `SecretMaskFilter` 把 `record.args` 强转 `str`，`%.2f` 收到字符串 → 格式化失败 → **整条日志静默消失**（真机启动时 cold start 行不见；早期靠 `%s` 行侥幸输出，`Path` 参数被拆成元组） | 只处理字符串内容（含递归到 list/tuple/dict 的字符串叶子），其余类型与结构原样保留；非元组 str 参数仍脱敏 | `tests/integration/test_logger_setup.py`：`should_keep_numeric_arg_type_when_filtered`、`should_write_formatted_line_when_args_are_numeric`、`should_mask_bare_string_args_when_filtered`、`should_mask_secret_inside_container_when_filtered`、映射参数含数值断言 |

## 13. 二期入口

扩展项（打包、皮肤热切换、开机自启、macOS 全屏、多后端 profile、历史持久化、TTS、状态曲线）已按优先级登记在收尾规划 Phase D，进入实现前需先升 FR v1.1「发布与个性化」范围，并按本文同样方式在 NFR 登记后续偏差。
