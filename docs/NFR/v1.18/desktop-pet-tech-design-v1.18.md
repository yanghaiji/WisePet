# 桌面宠物 WisePet 技术设计 v1.18

- **对应需求**：[FR v1.17](../../FR/v1.17/desktop-pet-PRD-v1.17.md)
- **上一版本**：[v1.17](../v1.17/desktop-pet-tech-design-v1.17.md)
- **状态**：待评审

## 0. 定位

改动全部落在 `app/pet_controller.py`；domain 白名单、事件契约、ui、数值语义一行未动。用户要的是「不靠交互也能看到全部状态」，所以核心是**确定性轮播**而非加大概率。

## 1. 落地

| 落点 | 做法 |
|------|------|
| `SHOWCASE_EVERY = 2` / `SHOWCASE_STATES` / `showcase_state(index)` | 7 个一次性状态按固定顺序循环；纯函数便于单测覆盖。轮播**不调 `rng`**，排在既有 `stretch_p` / `run_p` 概率分支之前，StubRng 的调用顺序不变（否则 `should_end_trip_at_edge_when_random_wants_to_stop` 那类依赖第 3 个随机值的用例会红） |
| `self._autonomous_count` | 每次自主触发计数，`% SHOWCASE_EVERY == 0` 时轮播，否则走原有走/跑分支 |
| `ONE_SHOT_MIN_DURATION_S` | 补 `SPEAKING / THINKING / SAD`（各 1.5s），经 `whole_cycle_duration_s` 向上补齐整圈 |
| `_expire_one_shot()` | `THINKING` 用 `pet.transition(IDLE)`（`finish_action()` 的集合不含 THINKING，`THINKING→IDLE` 在白名单里），其余仍 `finish_action()`；统一发布一次 `BehaviorChangedEvent` 并重排空闲 |
| `_maybe_wake_up()` / `NAP_MIN_DURATION_S = 6.0` | 白天打盹睡满 6 秒再醒；用 `_tracked_state is SLEEPING` 排除「本拍刚入睡、`_state_since` 还没刷新」，`_nap_at_night` 区分夜间睡眠（天亮即醒，保留既有契约）与白天打盹 |
| `self._nap_at_night` | 在 `_track_state()` 检测进入 SLEEPING 时按当时是否夜间记录 |

从 `IDLE` 出发到全部轮播目标的迁移都在 `ALLOWED_TRANSITIONS[IDLE]` 里；`THINKING/SPEAKING` 虽是 `LOCKED_STATES`，但 `can_transition(autonomous=True)` 只拦**从锁定态出发**，从 IDLE 进入合法；进入后 `can_autonomous()` 变 False，`_advance_motion()` 仍照常执行 `_expire_one_shot()` 收场。

## 2. 实测（offscreen 探针）

条件：真实 `~/.WisePet/config.json`，vitals 固定成真机的 `mood=10 / energy=10`，模拟时间固定今天 11:00（白天），1s 主 tick + 50ms 运动 tick，**10 分钟不交互**；气泡按 2 秒自动收起。

```text
IDLE 311.2s  WALKING 142.8s  SAD 49.0s  RUNNING 35.1s
STRETCHING 18.0s  SLEEPING 18.0s  SPEAKING 8.0s
EATING 6.0s  HURT 6.0s  THINKING 6.0s
changes: 138
```

全部 10 个动作状态（`STATE_TO_ACTION` 全覆盖，仅 `DRAGGING` 需用户拖拽）都由自主轮询出现；`animator.action` 与 `pet.state` 每步一致。

## 3. 否决方案

| 方案 | 否决理由 |
|------|----------|
| 用概率抽样扩池 | 某个状态可能长时间不出现，用户要的是「都可以出现」；且多一次 `rng.random()` 会打乱既有概率测试的 StubRng 顺序 |
| 每次自主触发都轮播 | 位移类被挤没，桌宠变成状态轮播机，失去活物感 |
| 改 `AUTONOMOUS_TARGETS` / 白名单 | 轮播目标从 IDLE 出发本来就合法，改 domain 契约无收益 |
| 让 `finish_action()` 认 THINKING | 那是 domain 语义（THINKING 本应等应答），演示态不该污染它；app 层走 `THINKING→IDLE` 即可 |
| 让白天打盹沿用「天亮即醒」 | 实测下一拍就被叫醒成 STRETCHING，睡觉根本看不见；但夜间睡眠必须保持天亮即醒（既有 v2.0 C11 求助时序） |
| 自主 SPEAKING 也发气泡 / 请求 | 与真实聊天混淆、频繁打扰，且会污染按天记录 |

## 4. 验证

- 全量 `985 passed`（新增 4 项轮播/收场回归），覆盖率 **99.24%**（2094 stmts / 16 miss），改动文件 lints 0。
- `tests/unit/test_pet_controller.py::TestAutonomousShowcase`：轮播覆盖 7 状态、每两次触发命中一次、`SPEAKING/THINKING/SAD` 自动收场、白天打盹不被秒叫醒；既有 `TestNightBehavior` 天亮即醒与睡醒伸懒腰 2 项照常通过。
- 真机待目检：重启后不交互也能看到全部 10 个动作，自主演示不弹气泡/不改数值，见 `AGENT.md` §4。
