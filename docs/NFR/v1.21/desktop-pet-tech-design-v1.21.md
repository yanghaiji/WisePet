# 桌面宠物 WisePet 技术设计 v1.21

- **对应需求**：[FR v1.20](../../FR/v1.20/desktop-pet-PRD-v1.20.md)
- **上一版本**：[v1.20](../v1.20/desktop-pet-tech-design-v1.20.md)
- **状态**：已实现（待评审）

## 0. 定位

四项都落在**开机链路与通知标识**上，全部复用既有骨架、零新依赖：问候沿用 Cheer/News 的「AI 优先 / 本地兜底 / 后台线程」同构模式；元气启动只改 `main._restore_pet` 的一次性托底口径；通知标识用 `ctypes` 设 AUMID；出场特效在既有 `TechEffect` 程序化绘制上增量。均不改状态机白名单与领域事件契约。

## 1. C34 开机昂扬问候

| 落点 | 做法 |
|------|------|
| `main.py` 常量 | `STARTUP_GREETING_FALLBACK = "{name}来召唤你啦，让我们开启美好的一天吧！"` |
| `_generate_greeting()` | `ai.configured`（只看 api_key）为假返回 `None`；否则拼「你是桌面宠物『{name}』，主人刚把你打开…不超过30字直接输出正文」prompt，用 `replace(ai, timeout_s=min(ai.timeout_s, GREETING_TIMEOUT_S=6.0))` **钳短超时**（聊天默认 30s 太久会拖住问候）→ `self._ai_client.complete(msgs, greet_ai)` → `except AIGatewayError` 记 warning 返回 `None`；成功截断 60 字 |
| `_greeting_text()` | AI 口号优先，为空/未配/失败回落 `STARTUP_GREETING_FALLBACK.format(name=self._pet.name)` |
| `_greet_on_start()` | 起后台守护线程 `wisepet-greet`：`_greeting_text()` 外层再包一层 `except Exception` 兜底（问候必达）→ `bus.publish(SpeakRequestedEvent(text, source="cheer", title="开启新的一天"))`，经 Qt 事件桥 marshal 回主线程 |
| `start()` 接线 | 末尾（`_log_chat_brain(when="boot")` 之后）调 `self._greet_on_start()`，不阻塞冷启动 |

## 2. C35 系统通知来源标识

| 落点 | 做法 |
|------|------|
| `infra/platform_util.py::set_notification_app_id(app_id)` | 非 `win32` 或 `app_id` 空白返回 `False`；否则 `ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id) == 0`（HRESULT，0=S_OK），异常静默 `logger.debug` 返回 `False` |
| `main._notify_via_system` | `use_system` 关则返回 `False`；开则**每次发通知前** `set_notification_app_id("WisePet")`（tile 固定应用名，不设会回落进程名 `python`），`title = event.title or self._config.persona.name`（粗体首行为宠物名），`tray.show_notification(title, event.text, msec)` |

> 关键事实：Windows 通知中心 toast 上方的「应用名 / tile」由进程 AUMID 决定，与 `showMessage` 的 `title` 参数（toast 内粗体首行）是两个独立位置——故 tile 用 WisePet、标题用宠物名，各就各位。

## 3. C36 开机元气启动（消除开机求助）

| 落点 | 做法 |
|------|------|
| `domain/pet.py::lift_for_fresh_start(floor_value)` | 由旧 `lift_mood_to` 推广为三维：`VitalsVO(max(hunger,floor), max(mood,floor), max(energy,floor))`，只升不降；与旧值相等返回 `False`（未变化不写库） |
| `main.py` 常量 | `FRESH_START_VITALS = 60.0` |
| `main._restore_pet` | 读快照后 `if pet.lift_for_fresh_start(FRESH_START_VITALS): logger.info(...)`，随后 **`pet.last_updated = self._clock.now()`**——开机归一时间戳，`settle_decay` 据此不补算关机期间的离线衰减 |

> 根因：旧实现只 `lift_mood_to(60)` 未管 energy/hunger，而 `_maybe_request_help` 在 `energy/hunger < danger_threshold(15)` 时发 `HelpRequestEvent`。旧快照精力/饱食见底时，宠物一睁眼就喊饿喊困求助，与开机问候自相矛盾。三维托底 + 时间戳归一后，开机 `tick` 不再触发求助。运行期衰减/回血/求助逻辑不变。

## 4. C37 出场动画科技感增强

| 落点 | 做法 |
|------|------|
| `ui/tech_effects.py` 常量 | 新增 `SPAWN_DURATION_MS = 950`（`DURATION_MS = 700` 仍为退场时长）；`__init__` 存 `self._duration = SPAWN_DURATION_MS if mode=="spawn" else DURATION_MS`，`_advance`/`paintEvent` 统一用 `self._duration` |
| `_paint_spawn` 增强 | 先 `_draw_converge_rings(ease)` + `_draw_scan_ring(t)`；粒子 alpha `int(140+115*t)`、拖尾 `min(22.0, radius*0.45)`、笔宽 `size*(1.0+0.4*t)`；`t>0.7` 时 `_draw_glow(...)` + `_draw_core_flash(flash)` |
| `_draw_converge_rings` | 两圈 `phase∈(0.0,0.3)`，`radius=(CANVAS//2-12)*(1-p)` 由外向内收缩，alpha `int(50+150*p)` 越近越亮（HUD 感） |
| `_draw_scan_ring` | 4 段短弧 `drawArc`，`painter.rotate(120*t)`、`RoundCap`、青橘交替，纯装饰仪表扫描 |
| `_draw_core_flash` | 白核正弦脉冲 alpha `int(230*sin(flash*π))`、radius `int(6+18*flash)`，闪完即隐宠物淡入 |

## 5. 否决方案

| 方案 | 否决理由 |
|------|----------|
| 开机把 energy/hunger 直接重置为满值 | 掩盖真实养育进度、和「陪伴越久越珍贵」冲突；托底到基线 60 只兜住「见底」，保留中间态 |
| 靠调高 `danger_threshold` 压住开机求助 | 会同时削弱运行期真正的求助灵敏度；问题在开机快照状态而非阈值 |
| 通知 tile 用宠物名、标题用 WisePet | 用户实测指出应反过来：tile 是「应用归属」应为 WisePet，标题是「谁在说话」应为宠物名 |
| AUMID 在进程启动只设一次 | 宠物名可在设置里改；每次发通知前设，改名后下一条即生效，成本可忽略 |
| 出场特效引入 GIF/Lottie 素材 | 违背零素材程序化管线与包体约束；QPainter 增量绘制足够 |

## 6. 验证

- 新增/改单测：`test_main_assembly`（`TestStartupGreeting` 5 项：兜底含名 / AI 口号 / AI 抛错回落 / `greet_on_start` 投递标题「开启新的一天」/ `start` 触发；`should_lift_low_mood_to_positive_on_restore` 断言三维均 60；`should_not_ask_for_help_on_fresh_start` 存见底快照后 `start()+tick()` 断言无 `HelpRequestEvent`；`should_clamp_greeting_ai_timeout` 断言问候传给 `complete` 的 `ai.timeout_s` 被钳到 `GREETING_TIMEOUT_S`）、`test_repos_and_platform`（`set_notification_app_id` 5 项：非 Win / 空白 / 成功 / HRESULT 错误 / 抛异常）、`test_tech_effects`（spawn 各阶段绘制不崩、驱动到超时发 `finished`、出场时长 > 退场时长）。
- 全量 **1169 passed**，覆盖率 **94.79%**（≥90 门槛，ui 层按 pyproject 口径排除）。
- 真机待目检：开机不再冒「求助·好困呀」且推送昂扬问候；开系统通知后 tile 显示 WisePet、标题为宠物名；出场可见能量环收缩、扫描弧旋转、收尾白核爆闪后宠物淡入。
