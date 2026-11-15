# 桌面宠物 WisePet 技术设计 v1.28

- 版本：v1.28
- 状态：已实现（待评审）
- 日期：2026-11
- 对应需求：[FR v1.28](../../FR/v1.28/desktop-pet-PRD-v1.28.md)（C46 陪伴轴①四项）
- 性质：增量落地登记；守四层 DDD（ui → app → domain ← infra），零新增三方依赖

## 1. 分层与落点

| 层 | 文件 | 职责 |
|---|---|---|
| domain | `companion.py`（新） | 纯函数：`pick_recall` / `recall_line` / `recall_prompt_line` / `reunion_greeting` / `reunion_hint` / `context_phrase`；`RecallCandidate` 值对象 |
| domain | `product.py` | `BehaviorContext` 加 `weekday`（默认 -1 = 未知）/`just_finished_focus`；`context_hint` 加 ENERGIZE / 刚结束专注 / 深夜分支；`PUSH_TITLES` 加三个新标题 |
| domain | `config_model.py` | `CompanionSettings`（8 字段）：四处同步 dataclass / `AppConfig` 字段 / `from_dict` 解析与钳制 / `return cls(...)` |
| infra | `presence_store.py`（新） | `presence.json`：`last_seen_ts`（精确到秒）、`last_recall_day`、`last_reunion_day`；走 `atomic_write` + 损坏备份回落 |
| app | `companion_service.py`（新） | `boot()` 读旧时间戳→算间隔→写本次；`reunion_text()` / `reunion_prompt_hint()` / `recall_candidate()` / `prompt_line()` / `tick()` 去重推送 |
| app | `focus_service.py` | `note_finished()` + `just_finished(window_s)`；`tick()` 到期自动登记，取消不登记 |
| app | `pet_controller.py` | `interact(kind)`：复用 `_settle_to_idle_for_conversation` + `_reschedule_idle`；`USER_CALL_LINES` 语料；消费 `CONTEXT_ENERGIZE` / 无条件 `CONTEXT_SLEEP` |
| ui | `settings_dialog.py` | 「陪伴进阶」加 7 项控件；`_load_from` 回填、`collect()` 用 `replace` 透传 `companion`；数值框补进滚轮 guard |
| 组装根 | `main.py` | 装配服务与仓储；`start()` 里 `boot()` 早于问候；`tick()` 驱动；`save_state()` 周期写存在感；`_behavior_context` / `_companion_phrase` / `_system_prompt` / `_greeting_text` / `_greet_on_start` / `_on_pet_head` / `_call_pet` / 命令面板两条 |

## 2. 关键实现决策

### 2.1 为什么新建 `presence.json`

`pet_state.last_updated` 在 `_restore_pet()` 里被启动时间覆盖（C36 元气启动不补算离线衰减），
`telemetry` / `growth` 是按天粒度且构造期就写今天。**小时级久别必须有一个不被启动流程冲掉的
精确时间戳**，故独立成文件；`save_state()`（周期 60s + shutdown 兜底）写入，最多丢一个周期。

### 2.2 惦记抽取为什么是纯正则

零依赖、可预测、可解释、不上传。规则保守：只看用户消息、排除命令句、距今天数
`[1, lookback_days]`、后续出现完成信号即整体放弃、多条取最近。坏输入（dict/generator/坏 ts）
都能兼容；`records` 先 `list()` 固化避免生成器被消费两次。

### 2.3 两个隐私开关

`recall_enabled`（它主动说）与 `memory_prompt_enabled`（它只是知道）分开。
前者走唯一 `SpeakRequestedEvent` 出口，按 `source="cheer"` 计入打扰预算；
后者只影响系统提示词。用户可只关前者，仍享受「聊得自然」。

### 2.4 情境兼容旧行为

`BehaviorContext.weekday` 默认 `-1`，旧调用与 1350 个存量用例不感知；
`just_finished_focus` 默认 False。只有 `companion.contextual=True` 时组装根才传真值。
`CONTEXT_SLEEP` 改为无条件生效（原来只在 `is_night` 参数为真时），深夜行为才真正落地。

### 2.5 打断而不破坏状态机

`interact()` 不加新 `BehaviorState`，只用既有 `IDLE/STRETCHING/SPEAKING` 与
`_settle_to_idle_for_conversation`；`DRAGGING/THINKING/SPEAKING` 直接 return False。
这样不用动 `ALLOWED_TRANSITIONS`、`STATE_TO_ACTION` 与动画帧目录。

### 2.6 久别问候不等 AI

`_greeting_text()` 久别时直接返回本地分档文案，避免「等 2 秒 AI 却完全没提这件事」。
AI 侧只把 `reunion_hint()` 注入系统提示词，让后续对话不自相矛盾。

## 3. 否决方案

| 方案 | 否决理由 |
|---|---|
| 用 `growth.last_day` / telemetry 判久别 | 按天且构造期即写今天，小时级读不到 |
| 惦记交给 AI 抽取或润色 | 引入联网依赖与不可预测性；确定事实本地文案更快更稳 |
| 新增「被叫醒」等行为状态 | 要改状态机白名单 + 动作映射 + 60 帧素材，收益不匹配 |
| 惦记每次开机都推 | 打扰预算与「有分寸的主动」冲突；按自然日去重 + 延迟 3 分钟 |
| 把新配置塞进 `memory` 分节 | 语义不同（一个是事实记忆，一个是关系行为），且 `collect()` 透传成本高 |

## 4. 验收

- 新增 `tests/unit/test_companion.py`（33 项）、`tests/ui/test_companion_ui.py`（6 项）；
- 全量 **1389 passed / 0 failures / 0 errors**，改动文件 lints 0；
- 关键回归：`context_hint` 六分支旧断言、`TestStartupGreeting`、`collect()` 不丢分节、
  `StubRng` 顺序、打扰预算白名单全部保持通过。

## 5. 真机待目检

- 隔一天/几天后开机是否出现「好久不见」气泡且未被打扰预算吞掉；
- 深夜、周一早晨、专注刚结束时动作与话术是否有可感知差异；
- 走动中摸头 / 命令面板「叫一声」是否真的打断当前动作；
- 有一条旧惦记时，启动 3 分钟后「我还记得」是否只出现一次。
