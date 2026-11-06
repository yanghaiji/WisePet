# 桌面宠物 WisePet 技术设计 v1.17

- **对应需求**：[FR v1.16](../../FR/v1.16/desktop-pet-PRD-v1.16.md)
- **上一版本**：[v1.16](../v1.16/desktop-pet-tech-design-v1.16.md)
- **状态**：待评审

## 0. 定位

改动全部落在 `app/pet_controller.py`；`domain` 状态机白名单、事件契约、ui 一行未动。诊断靠真机日志 + `~/.WisePet/pet_state.json` 数值，而非猜测：`mood=energy=10`（`floor`）、`sad_threshold=25`，日志 `IDLE→SAD` 与 `WALKING→IDLE` 相隔约 1 秒，构成闭环。

## 1. 落地

| 落点 | 做法 |
|------|------|
| `SAD_REENTRY_S = 20.0`（新增常量） | 低落状态的再次触发间隔 |
| `self._last_sad_at`（新增字段） | 记录上次进 `SAD` 的时刻 |
| `_maybe_sad()` | 命中低心情时先看冷却：`now - _last_sad_at < SAD_REENTRY_S` 则直接 return；否则记时刻再 `_transition_to(SAD)`。用 `self._clock.now()`，不新增参数、不改 `tick()` 调用签名 |
| `_maybe_autonomous_action()` 的 `SAD` 分支 | 夜间且 `energy < sad_threshold` 时，先 `_transition_to(IDLE)`（`SAD→IDLE` 合法）再 `_transition_to(SLEEPING, autonomous=True)`（`IDLE→SLEEPING` 合法）；其余情况仍 `_begin_stroll(running=False)` |

关键约束：`SAD→SLEEPING` 不在 `ALLOWED_TRANSITIONS`，所以必须**先归一到 IDLE**，不改 domain 契约。睡醒沿用既有 `_maybe_wake_up()` → 下一拍伸懒腰。

## 2. 实测对比

复现条件：offscreen 探针读真实 `~/.WisePet/config.json`，把 vitals 固定成真机的 `mood=10 / energy=10`，模拟时间固定到今天 11:00（白天），1s 主 tick + 50ms 运动 tick 跑 5 分钟；气泡按 2 秒自动收起。

| | 修复前（真机日志） | 修复后（同一探针） |
|---|---|---|
| 出现的状态 | `IDLE` / `SAD` / `WALKING` / `DRAGGING` / `SPEAKING` | `IDLE` / `WALKING` / `SAD` / `RUNNING` / `SPEAKING` |
| `IDLE` | 约 1 秒就被拉走 | 56.5s，有实质停留 |
| `SAD` | 占满整个循环 | 60.0s（约 20%），仍周期性出现 |
| `RUNNING` | 0 | 7.2s |
| 切换次数 | 反复 3 状态循环 | 48 次 |

夜间场景另测：同一数值下 `SAD → IDLE → SLEEPING` 生效，睡着后按既有规则回精力，死锁被打破（探针基础时间戳曾误落夜间窗口，反而验证了该分支）。

## 3. 否决方案

| 方案 | 否决理由 |
|------|----------|
| 给 `ALLOWED_TRANSITIONS` 加 `SAD→SLEEPING` | 改 domain 契约要动不变量与一批测试；归一到 IDLE 是已有合法路径 |
| 去掉 `SAD` 对自主行为的全部限制 | 「难过还一溜小跑」不合语义；保留只慢慢走，只在夜间睡觉开口子 |
| 调低 `sad_threshold` 默认值 / 改 `floor` | 用户 `config.json` 已存 25/10，默认值被覆盖，改了不生效 |
| 让心情/精力随时间自动回升 | 心情应靠抚摸等互动，自动回升会架空 US-4 的求助语义 |
| 靠 UI 提示遮住问题 | 日志已经证明是状态机死锁，可观测性不能替代修复 |

## 4. 验证

- 全量 `981 passed`（新增 2 项死锁回归），覆盖率 **99.23%**（2075 stmts / 16 miss），改动文件 lints 0。
- `tests/unit/test_pet_controller.py::TestPollingVisibility` 新增：`should_not_reenter_sad_immediately_when_mood_stays_low`、`should_still_allow_sleep_from_sad_at_night_when_energy_bottomed`；既有 69 项控制器测试全部通过。
- 真机待目检：重启后日志里能看到 `RUNNING`，`IDLE` 不再秒切 `SAD`；`EATING` / `HURT` / `THINKING` 仍需投喂 / 单击 / 发消息，`SLEEPING` 需夜间窗口，见 `AGENT.md` §4。
