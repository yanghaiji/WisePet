# 桌面宠物 PRD v1.7 —— 睡觉必须真的解乏

> 本文件只记录**相对 v1.6 的需求变更**。四层架构、素材管线、本地小脑、AI 链路错误分类均不重复。
> 按 `docs/规约.md`：修订不回改历史版本文件，本版为**新增 + 补登**。

## 0. 变更清单

| 编号 | 变更 | 类型 | 来源 |
| --- | --- | --- | --- |
| C12 | **睡眠必须恢复精力**：现在「夜间 + 精力低 → 入睡」是个只进不出的坑——睡着期间精力照旧衰减（还带夜间 2 倍系数）一路掉到下限 10，天亮第一件事就是又喊累。要求把睡眠变成真正的自我修复 | 补充 v1.0 §M5 | 本轮动画核查发现 `VitalsVO.rested()` 全仓零调用（死代码即证据：语义定义了、没人实现），且 AGENT.md §4 手工清单「投喂/戳多次 → 心情变化」一项始终未签字 |

### 0.1 补登说明（本次一并归档，不新增内容）

代码注释长期引用 `PRD v2.0 C9/C10/C11`，但 `docs/FR/` 里**没有 v2.0 文档**——那三条是口头需求，只落在 AGENT.md 决策表里。为恢复可追溯性，在此登记其口径与实现位置，数值与验收细节仍以代码与 AGENT.md §3/§4 为准：

| 编号 | 需求口径 | 实现位置 |
| --- | --- | --- |
| C9 | 素材画风统一为**扁平矢量贴纸风**（粗描边、同色系压深、无写实毛流），三皮肤共用一套姿态体系（四足着地、身体水平） | `tools/gen_pet_assets.py`（`OUTLINE_W`/`Chibi`）、`tools/import_pet_images.py` |
| C10 | **贴地行走与重力回落**：非拖拽时宠物自然落回工作区下沿并站在「地面」上，落地微弹一次；可由 `stick_to_floor` 关闭回退到 v1.x 自由漂浮 | `domain/position.gravity_step`、`app/pet_controller._apply_gravity`、`domain/config_model.PetParams.stick_to_floor` |
| C11 | **动作与帧数扩容**：8 动作 × 4 帧 → **10 动作 × 6 帧**（新增 `run`/`stretch`），11 个行为状态全部有对应素材；节奏由逐动作帧率单独调 | `domain/actions.ACTION_NAMES`/`FRAMES_PER_ACTION`/`FPS_BY_ACTION`、`tests/integration/test_pet_assets.py` |

## 1. 改前现状（可核对）

| 现象 | 改前 | 为什么不够 |
| --- | --- | --- |
| 入睡条件 | `is_night and energy < sad_threshold(25)` | 判定本身合理 |
| 睡眠期间 | 精力按 `energy_per_hour(3.0) × energy_night_factor(2.0)` 继续掉，直到下限 `floor(10)` | **越睡越累**：睡 8 小时是从 25 掉到 10，而不是回血 |
| 天亮时 | 精力必然处于下限 → `_maybe_request_help` 立刻发 `reason=energy` 求助 | 用户看到的固定剧本是「猫睡了一夜，醒来第一件事喊困」，喂食成为唯一解 |
| 领域层 | `VitalsVO.rested()` 已定义且有单测，但运行期**零调用** | 语义与实现脱节：文档写了「睡觉休息」，代码没接上 |

## 2. 需求条目

- **R1 睡着时精力回升**。处于 `SLEEPING` 期间，精力按**每小时固定回血**往上走，不再衰减；回血量与睡眠时长成正比（睡 8 小时约 +64，从见底回到接近满）。
- **R2 只回精力**。饱食度与心情照旧衰减——睡觉不吃饭、也不会因为睡一觉就开心。否则投喂与抚摸这两个交互动作会被睡眠替代。
- **R3 醒着熬夜不享受回血**。夜间且清醒时，精力仍是双倍衰减；「躺着」不等于「睡着」。
- **R4 天亮后不必再为精力求助**。睡够一夜（约 6 小时以上）后，天亮时的精力必须高到不触发 `reason=energy` 求助；睡得不足则仍应求助（诚实反映「没睡饱」）。
- **R5 状态面板与日志不得把回血说成衰减**。`VitalsChangedEvent.changed` 要区分 `rested` 与 `decay`，AI 人设里的 `vitals_summary` 数值随之自洽。

## 3. 验收标准

- **AC-SLEEP-1**：`decayed(dt_hours=8, is_sleeping=True)` 精力上升、饱食与心情下降；`is_sleeping=False` 且夜间时精力仍双倍下降（单测 `TestVitalsVO::should_restore_energy_when_sleeping` / `should_still_drain_energy_when_awake_at_night`）。
- **AC-SLEEP-2**：精力回血受 100 上限钳制，睡再久不会「精力溢出」（`should_not_recover_beyond_the_cap_when_sleeping_forever`）。
- **AC-SLEEP-3**：夜里睡满 8 小时后天亮 → 状态回 `IDLE`、精力高于 `sad_threshold`、本轮不产生 `HelpRequestEvent`（`TestNightBehavior::should_wake_up_at_dawn_with_energy_restored`）。
- **AC-SLEEP-4**：睡眠期间产生的数值变化事件带 `changed=("rested",)`（`should_report_rested_not_decay_when_energy_came_up_while_sleeping`）。
- **AC-SLEEP-5**（真机目检）：把夜间窗口设成当前时刻且把精力压到 10（或等它自然入睡），天亮后猫精神、不主动喊困。完成项请补日期与读数，不由自动化结果代填。

## 4. 边界与不承诺

- **关机期间不算睡眠**。持久化快照不保存行为状态（开机恢复即待机），所以关闭程序那 8 小时按普通夜间衰减结算——不为此新增状态持久化字段。
- **不做睡眠时长/质量曲线**。不引入「深度睡眠阶段」「越久收益递减」这类模型，线性回血 + 100 钳位已够读感。
- **不新增行为状态**。`SLEEPING` 沿用既有状态机；也不给睡眠加计时器收场（它只在天亮时结束）。
- **不承诺「睡够就永不求助」**。饱食度在睡眠中照掉，睡一夜可能换来一句喊饿——这是预期行为，不是本条变更要解决的点。
- **回血速率可配置但不做界面控件**。`vitals.rules.energy_recover_per_hour` 支持手改 `config.json`，设置窗不新增该项（只有一个用户可调参数时不值得加控件）。
