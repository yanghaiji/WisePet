# 桌面宠物 WisePet 需求设计 v1.16

- **对应技术设计**：[NFR v1.17](../../NFR/v1.17/desktop-pet-tech-design-v1.17.md)
- **上一版本**：[v1.15](../v1.15/desktop-pet-PRD-v1.15.md)
- **状态**：待评审
- **变更性质**：行为死锁缺陷修订（不改状态机白名单、不改事件契约）

## 0. 用户反馈与实证

> 「我看日志 为啥只要这几个在切换 没啥没用别的 状态的切换呀」

真机日志 `09:52~11:00` 持续重复 `WALKING → IDLE → SAD → WALKING`，夹杂 `DRAGGING` 与少量 `SPEAKING`，从未出现 `RUNNING` / `STRETCHING` / `EATING` / `HURT` / `THINKING` / `SLEEPING`。

`~/.WisePet/pet_state.json` 实测：`mood=10.0`、`energy=10.0`，正好都是 `DecayRules.floor=10`；配置 `sad_threshold=25`、`danger_threshold=15`。

根因链：

1. `mood < 25` → 每次回到 `IDLE` 立刻被 `_maybe_sad()` 拉进 `SAD`（日志里两条相差约 1ms）。
2. `SAD` 下的自主行为被写成「只慢慢走、不跑、不伸懒腰、不睡」，所以 `RUNNING` / `STRETCHING` 永不触发。
3. 睡觉是唯一能回精力的途径，却被 1+2 挡住 → `energy` 永远卡在 `floor`；三维数值只降不升，除非用户主动投喂/抚摸，**死锁无法自解**。
4. `EATING`（投喂）/ `HURT`（单击）/ `THINKING`（发消息）本就需要交互；日志里是 `DRAGGING`（有位移的拖），不算点击，所以也没有 `HURT`。

## 1. C26 低落状态死锁

### R1 SAD 冷却
心情长期贴底时不许每一次 `IDLE` 都被立刻拉回 `SAD`；进入 `SAD` 后至少间隔 20 秒才可再次进入，让 `IDLE` 有实质停留并能触发完整自主行为池（跑 / 伸懒腰 / 散步）。宠物仍会周期性表现低落，语义不变。

### R2 睡觉不被低落锁死
夜间且精力低于阈值时，允许从 `SAD` 先归一到 `IDLE` 再进 `SLEEPING`；睡着按既有规则回精力，打破「低心情 → 只慢慢走 → 永远不睡 → 精力永远回不来」。**不改 `domain` 迁移白名单**（`SAD→SLEEPING` 本来非法）。

### R3 其余语义保持
「难过时只慢慢走、不跑」、跑累降速、一次性动作收场、轮询节奏（FR v1.15 C25）全部不变。

### R4 交互态说明
`EATING` / `HURT` / `THINKING` 由投喂 / 单击 / 发消息触发，`SLEEPING` 还需夜间窗口，这些不是自主轮询产物；在用户文档里写清触发路径。

### 验收项
- AC-SAD-1：`mood` 贴底时，`SAD → IDLE` 后不会立刻再进 `SAD`。
- AC-SAD-2：夜间且 `energy` 贴底时，即使处于 `SAD` 也能进入 `SLEEPING`。
- AC-SAD-3：白天 `mood=10/energy=10` 长跑中能出现 `RUNNING`，`IDLE` 有实质停留，`SAD` 不再占满全程。
- AC-SAD-4：既有控制器测试与全仓回归全部继续通过。

## 2. 不作废的旧约束

- 状态机白名单、`LOCKED_STATES`、拖拽优先、对话冻结、睡眠回血（v1.7 C9）不变。
- FR v1.15 C25 的轮询节奏、逐动作帧率、一次性动作整圈收场不变。
- FR v1.14 C24 及更早的交互 / 素材口径不变。

## 3. 范围外

- 自动恢复心情或精力（不合语义，心情本就该靠互动）。
- 把 `sad_threshold` / `floor` 做成可配置或调默认值（用户 `config.json` 已固化 25/10，改默认值不生效）。
- 新增自主动作种类、全局热键唤出指定动作。
