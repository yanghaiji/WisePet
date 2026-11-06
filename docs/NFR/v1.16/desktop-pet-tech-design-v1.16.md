# 桌面宠物 WisePet 技术设计 v1.16

- **对应需求**：[FR v1.15](../../FR/v1.15/desktop-pet-PRD-v1.15.md)
- **上一版本**：[v1.15](../v1.15/desktop-pet-tech-design-v1.15.md)
- **状态**：待评审

## 0. 定位

改动全部落在 `app/pet_controller.py`（编排层），domain 状态机、事件契约、ui 一行未动。先用探针证伪「轮询坏了」：`tmp/probe_states.py` 用真实 `~/.WisePet/config.json` 构造 `PetApplication`，以 `1s 主 tick + 50ms 运动 tick` 模拟 5 分钟，记录 `pet.state` 与 `animator.action`——两者每一步都一致，`conversation_active=False`，动画计时器在跑。问题在节奏分布，不在事件链路。

## 1. 落地

| 落点 | 做法 |
|------|------|
| `_advance_locomotion` | 每拍算 `elapsed = now - _walk_started_at`：先判 `RUNNING` 降速（`0.6×walk_max`，重开一趟计时），再判 `elapsed >= walk_max` 收趟并 `_reschedule_idle()`；到边分支只保留 `_should_end_trip()` 的随机提前收场（签名去掉不再用的 `cfg`） |
| `_reschedule_idle`（新增） | 收趟 / 一次性动作收场时 `self._next_action_at = now + _random_idle_gap(cfg)`；旧代码只在「触发自主行为」时排期，一趟二三十秒后排期早已过期 |
| `_begin_stroll` | 正好 `x <= bounds[0]` / `x >= bounds[2]` 时把方向强制指向内侧；离边还有几步仍用 `rng.choice`，保住到边折返的既有玩法 |
| `_expire_one_shot` | 收场后同样调 `_reschedule_idle()` |
| `ONE_SHOT_MIN_DURATION_S` | `HURT 1.0→1.2`、`STRETCHING 0.7→1.5`；经 `whole_cycle_duration_s` 向上补齐整圈后均为 1.5s，`EATING` 1.8s 不变 |
| `_try_transition` / `_publish_state_change` | 发布 `BehaviorChangedEvent` 前写 `logger.info("behavior %s -> %s", ...)` |

## 2. 实测对比（同一探针、同一份用户配置）

| | 修复前 | 修复后 |
|---|---|---|
| `IDLE` | 108.1s | **140.8s** |
| `WALKING` | 166.3s | **137.2s** |
| `RUNNING` | 17.6s | 18.1s |
| `STRETCHING` | 8.0s（每次约 1s） | 4.0s（每次约 2s，更易看清） |
| 状态切换 | 41 次（含 0.05s 闪断） | 36 次（最短一趟 2.5s，无闪断） |
| 节奏 | 走 20~40s、待机 0.6s 又出发 | 走满 12s 收趟 → 休息 3~10s → 再出发 |

## 3. 否决方案

| 方案 | 否决理由 |
|------|----------|
| 改 `idle_min_s` / `walk_max_s` 默认值 | 用户 `~/.WisePet/config.json` 已存了 3/10/12，默认值会被覆盖，改了不生效 |
| 贴边修正用「距边 8px 容差」 | 实测会吃掉既有 `should_end_trip_at_edge_when_random_wants_to_stop`（起点 x=5）的到边玩法，收窄成「正好在边界」才修正 |
| 把时长收场放在降速之前 | 会让 `RUNNING` 一到上限直接急停，破坏 v2.0 C11「跑累先降回走」；必须降速优先且重开计时 |
| 靠 UI 层加动画状态提示 | 只是遮住问题，日志才是事后可查的可观测口径（规约 §2.10） |
| 把 `SLEEPING` / `SAD` 塞进自主池 | 睡着会触发回血、低落需心情阈值，语义副作用大，超出本次缺陷修订范围 |

## 4. 验证

- 全量 `979 passed`（新增 5 项轮询回归 + 更新 1 项时长断言），覆盖率 **99.23%**（2065 stmts / 16 miss），改动文件 lints 0。
- `tests/unit/test_pet_controller.py::TestPollingVisibility`：收趟后休息、未撞边也走满收趟、贴边起步朝内、状态迁移 INFO 日志、伸懒腰时长下限。
- 既有 `TestAutonomousBehavior` / `TestRunAndStretch` / `TestMotionTick` 64 项全部照常通过。
- 真机待目检：待机—溜达—休息交替、伸懒腰能看清、日志里能看到 `behavior … -> …`，见 `AGENT.md` §4。
