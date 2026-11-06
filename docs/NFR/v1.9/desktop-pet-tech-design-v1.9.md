# 桌面宠物 WisePet 技术设计 v1.9

- **对应需求**：[FR v1.8](../../FR/v1.8/desktop-pet-PRD-v1.8.md)
- **上一版本**：[v1.8](../v1.8/desktop-pet-tech-design-v1.8.md)
- **状态**：待评审
- **变更性质**：增量修订（仅描述相对 NFR v1.8 的变化）

## 1. 分层与落点

沿用 `ui → app → domain ← infra` 单向依赖，新增组件按层落位：

| 层 | 新增/改动 | 职责 |
|---|---|---|
| domain | `chat_record.py`（`ChatRecord`、`day_key`）、`events.py`（`ChatStateChangedEvent`、`ChatTurnRecordedEvent`、`PHASE_*`） | 记录值对象、按天归档键、会话事实；零三方依赖 |
| app | `chat_history.py`（`ChatHistory`）、`chat_service.py`（发布新事件）、`pet_controller.py`（对话冻结与归一化） | 历史门面、会话状态机、位移冻结 |
| infra | `chat_log_repo.py`（`ChatLogRepository`） | `~/.WisePet/chat/YYYY-MM-DD.json` 原子写、损坏备份 |
| ui | `chat_window.py`（`ChatWindow`） | 历史区、状态栏、日期选择、输入；只发信号与接收回调 |
| 组装根 | `main.py` | 注入仓储/门面、订阅事件、连接 `clicked`/`active_changed` |

关键约束：`ChatWindow` 通过构造注入 `days_provider` / `records_provider` 两个 callable，**不 import 任何 infra 仓储**（规约 3）。`ChatHistory` 只依赖一个 duck-typed `store`，测试用 `StubStore` 即可，不需 Qt 也不需磁盘。

## 2. 会话状态机

`ChatService` 是唯一发布方，事件经 `EventBus → QtEventBridge → EventRouter` 回主线程后由组装根消费，因此 `ChatWindow` 的控件访问都在主线程，`ChatLogRepository.append` 也不会被 worker 线程并发写。

```
send(text)                → ChatTurnRecordedEvent(user) + PHASE_THINKING(brain)
process_next()            → PHASE_THINKING(brain 按实际配置校正)
首段内容到达              → PHASE_REPLYING(brain)
done 且 reply 非空        → ChatTurnRecordedEvent(assistant) + ChatReplyChunkEvent(done)
done                      → PHASE_DONE(brain)
AIGatewayError / 未知异常 → ChatErrorEvent + PHASE_ERROR(detail=kind)
                            + ChatTurnRecordedEvent(assistant, fallback_text, "fallback")
```

- `brain = "local" if (local_responder is not None and not ai.configured) else "ai"`，与 v1.6 `source=` 日志判据同源，抽成 `ChatService._brain_source()` 与组装根 `_brain_source()` 两处一致实现。
- `send()` 发布一次 THINKING 让界面立刻响应；`process_next()` 再校正一次，避免"排队期间用户改了 Key"导致来源显示失真。
- 错误有终态：无论错误事件来自 `ChatService._on_failure` 还是外部直接发布 `ChatErrorEvent`，组装根 `_on_chat_error` 都会兜一次 `set_state(PHASE_ERROR, detail=kind)`，界面不会永远停在"思考中"。
- 事件带 `session_id = day_key(ts)`，与按天文件同键。

### 否决过的方案
1. **复用 `ChatSession` 当历史**：它是发给模型的上下文窗口，`_trim()` 按 `max_turns*2` 删最早，语义相反（一个要忘、一个要留）。否决。
2. **用 `ChatReplyChunkEvent` 拼历史**：增量在 worker 线程逐字发，落盘会写半截句子且重复 IO；且 `done=True` 只带全文不带来源。否决。
3. **UI 直接 `ChatLogRepository.load()`**：违反分层铁律。否决。
4. **单文件 `chat.json` 无限追加**：用户明确要求"先按天保存"，且单文件会随天数无限膨胀、损坏时全丢。否决。

## 3. 按天存储格式

`~/.WisePet/chat/YYYY-MM-DD.json`，与 `config.json` / `pet_state.json` 同数据目录（测试注入 `config_repo.path.parent` 时自动跟随 `tmp_path`，不污染真实目录）。

```json
{
  "date": "2026-11-05",
  "records": [
    {"role": "user", "content": "你好", "source": "user",
     "ts": 1762329600.0, "session_id": "2026-11-05"},
    {"role": "assistant", "content": "...", "source": "local",
     "ts": 1762329600.2, "session_id": "2026-11-05"}
  ]
}
```

- 顶层必须是 dict，符合 `json_store.atomic_write` 的载荷契约；`load_json_or_backup_reset` 复用现成的损坏备份。
- `append` = 读当天 → 追加一条 → 原子替换，一天几十条记录，简单且不留半截 JSON。
- 日期由 `day_key(record.ts)` 从**记录自身时间戳**推导，不是写入时刻，避免排队消息跨午夜归错文件。
- `ChatRecord.from_dict` 容忍缺字段（老文件向前兼容）；`days()` 列已有日期供窗口下拉。
- 只追加不截断：25 条/天的用例显式覆盖，确认不继承 `ChatSession._trim()` 的行为。

## 4. 对话期间位移冻结

`PetController` 新增 `_conversation_active` 与只读属性 `_frozen`：

```python
_frozen = _conversation_active or pet.state in {THINKING, SPEAKING}
```

统一收口到四处：`is_moving()` 返回 False（关掉 50ms 运动计时器）、`_apply_gravity`、`_advance_locomotion`、`move_to`。`tick()` 在 `_conversation_active` 时跳过 `_maybe_autonomous_action` / `_maybe_wake_up` / `_maybe_request_help`，但**仍执行数值衰减与一次性动作收场**——否则 `EATING` 之类会永远卡住。

`ChatWindow.active_changed(bool)` 由 `show_near` / `hideEvent` 发信号，组装根直连 `controller.set_conversation_active`；打开时立即 `stop_walk()` + `finish_action()` 收住当前行为。

状态归一化（不变量 I1 不放宽白名单）：`start_thinking()` / `start_speaking()` 先经 `_settle_to_idle_for_conversation()` 把可中断状态收回 `IDLE` 再迁移，并补发 `BehaviorChangedEvent`；`DRAGGING` 例外。`start_speaking()` 对 `THINKING→SPEAKING` 走直接迁移，避免把正在思考的状态打回待机。

`test_pet_controller.py` 里 `should_reject_feed_when_walking` 之类 domain 层断言保持原样——严格性留在 domain，编排在 app。

## 5. 并发、性能与兼容

- `ChatService.process_next()` 在 worker 线程发布事件；Qt 队列连接保证组装根回调在主线程，仓储写与控件更新都单线程串行。
- 每条完整回复只落盘一次（非逐 chunk）；一天一个文件，重启只读当天，不做全量扫描。
- `AiWorker` 依旧 100ms 空转轮询；新增事件不进 `ChatReplyChunkEvent` 的 `done` 判定路径。`test_ai_worker` 原先"桥上所有事件都有 `.done`"的脆弱断言改为按 `isinstance` 过滤。
- `ChatInput` 与 `SpeechBubble` 保留（气泡仍是拟人播报通道），只是组装根不再接 `ChatInput.submitted`；`ChatInput` 的既有单测不受影响。
- 隐藏宠物时一并 `hide()` 聊天窗（触发 `active_changed(False)`，不会把宠物永久冻住）。
- `apply_config()` 与 `start()` 都刷新状态栏大脑来源，配置热生效后显示不失真。

## 6. 测试与基线

| 文件 | 覆盖 |
|---|---|
| `tests/unit/test_chat_record.py` | 按天键、值对象往返、老文件缺字段 |
| `tests/integration/test_chat_log_repo.py` | 同日追加、跨日分文件、只追加不截断、损坏备份、Unicode |
| `tests/unit/test_chat_history.py` | 事件→记录、空白过滤、当天回看 |
| `tests/unit/test_chat_session_state.py` | 阶段序列、大脑来源、错误终态、session_id、空流 |
| `tests/ui/test_chat_window.py` | 发送/空白/历史/状态/来源/HTML 转义/开关信号/Esc/边界钳制 |
| `tests/ui/test_main_assembly.py` | 按天落盘、重启恢复、错误状态、点击切换、对话中不动 |
| `tests/unit/test_pet_controller.py` | 归一化、拖拽例外、重力与自主行为冻结、关窗恢复 |

基线：**906 项全绿**，覆盖率 **99.22%**（TOTAL 2045 stmts / 16 miss，阈值 90%），lints/ruff/mypy 0。`ui/` 依既有约定在覆盖率中 omit。

## 7. 范围外 / 后续

- 记录删除、导出、全文检索、多会话 tab：接口已留 `ChatHistory.records_for(day)` / `days()`，本期不实现 UI。
- 聊天记录加密（当前是本机明文 JSON，与 config 同级权限）。
- 会话窗的云端同步与跨设备合并。
