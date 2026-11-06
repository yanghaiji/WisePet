# 桌面宠物 技术设计 v1.8 —— 睡眠回血的落地 + 动画节拍与脚线归位

> 对应 FR v1.7（C12）。同时登记本轮两项**缺陷修复**的实现口径：动画节拍（帧数/帧率/收场时长）与素材脚线。
> 架构分层、领域模型、事件桥、线程模型均沿用 v1.0~v1.1，不重复。

## 1. 睡眠回血：只加一个入参，不加第二套时间轴

| 位置 | 改动 | 理由 |
| --- | --- | --- |
| `domain/vitals.DecayRules` | 新增 `energy_recover_per_hour: float = 8.0`（放在 `energy_night_factor` 之后、`floor` 之前） | 8 × 8h = 64 点：入睡门槛是 `energy < 25`，一夜后回到 ~89，够「有精神」又不虚高。字段插在中间安全——全仓构造 `DecayRules` 一律用关键字参数 |
| `domain/vitals.VitalsVO.decayed` | 签名加 `is_sleeping: bool = False`（默认值保证既有 21 处调用零改动）；为真时能量走 `self.rested(rate * dt).energy`，`hunger`/`mood` 分支原样 | 复用**既有** `rested()` 而不是新写一个 `_recover_one`：它就是为这件事定义的，之前只是没人调。默认参数而非新增方法：睡眠与衰减共享同一段 `dt`，分成两个入口必然出现「谁先算」的双记/漏记 |
| `domain/pet.Pet.settle_decay` | 传 `is_sleeping=self.state is BehaviorState.SLEEPING` | 状态在这里，时钟也从这里进；不引入「睡眠开始时刻」字段，因此**不改快照格式**，旧 `pet_state.json` 直接可读 |
| `app/pet_controller._settle_vitals` | `changed` 由写死 `("decay",)` 改为按能量升降给 `("rested",)` / `("decay",)` | 该字段语义是「为什么变了」。下游（ui 只读三维数值、日志只记来源）不消费 `changed`，改动无兼容代价；不改就变成「越睡越累」的假数据 |
| `domain/config_model` | `from_dict` 增读 `vitals.rules.energy_recover_per_hour`，非法值经 `_num` 回落默认 | 与其他衰减参数同一口径，避免「只有这一个键改不动」 |

**为什么不做成「按整觉一次性回血」**（`_maybe_wake_up` 里调一次 `rested(64)`）：那样睡 20 分钟和睡 8 小时收益相同，且离线补算路径（`settle_decay`）拿不到「睡了多久」，会退化成固定值。当前实现让**回血与时长成正比**，且与衰减共用同一套时间结算，天然覆盖「中途被拖出来打断」。

## 2. 动画节拍：契约上收 domain，app 按整圈收场

| 缺陷 | 修法 | 位置 |
| --- | --- | --- |
| 帧率表在 ui、一次性动作收场秒数在 app，两处各写一份 → `EATING` 2.0s ÷ 一圈 0.6s = 3.33 圈，**必然在第 2 帧被剪断** | `FRAMES_PER_ACTION`/`FPS_FALLBACK`/`FPS_BY_ACTION`/`frame_interval_ms` 上收 `domain/actions.py`（依赖方向 ui → app → domain，app 不能反向读 ui）；新增 `cycle_seconds()` 与 `whole_cycle_duration_s(action, at_least_s)` 把收场时长**向上补齐到整数圈** | `domain/actions.py`、`app/pet_controller.ONE_SHOT_MIN_DURATION_S` → `ONE_SHOT_DURATION_S`（HURT 1.125s / EATING 1.8s / STRETCHING 0.75s） |
| 帧率数值与素材运动量不匹配（实测相邻帧不透明像素变化占比：`run`/`hurt` 36~39%、`walk` 12%、`idle` 5%、`sleep` 5.6%、`sad` 1.7%） | `run` 14→16、`hurt` 12→16（大跳变要快播，否则读成抽搐）；`sleep` 2→4（每帧 500ms 是换图不是呼吸）；`sad` 6→4（比待机还静，放慢才读得出低落）；`walk` 一圈必须 ≤1s | `domain/actions.FPS_BY_ACTION`，不变量由 `tests/unit/test_action_timing.py` 锁住 |
| 向左走每拍重做一次整幅镜像变换 | 镜像结果进 `_mirror_cache`，`set_loader()` 换皮肤时清空 | `ui/frame_animator._facing_frame` |
| 窗口隐藏后仍以 4~16FPS 空转取帧 + 重绘 | `toggle_visible()` 隐藏即 `animator.stop()`、显示即 `start()` + `_sync_window()`；主 tick 不停（衰减/提醒/落盘还在跑） | `main.py` |
| `QTimer` 换动作后相位不重置、会吞掉新动作第一帧？ | **实测否**（探针：`setInterval` 后 109ms 触发，Qt 会重启计时器），故不改代码，仅登记以免后人重复怀疑 | — |

## 3. 脚线归位：素材契约补两个常量

素材是 256×256，但三皮肤**联合内容框完全相同**：`(23, 30, 232, 215)`，即内容只有 210×186，脚线 y=216、脚下留 40px 透明余量。运行时等比缩到 128 画布并**几何居中**，那 40px 缩成 20px 仍在脚下；而窗口底边就是贴地模式的「地面」，于是猫常年飘在半空。

- 契约侧：`domain/actions` 新增 `FRAME_SOURCE_SIZE = 256`、`FRAME_FOOT_Y = 216`、`FRAME_FOOT_RATIO`（216/256，与分辨率无关）。`tools/gen_pet_assets.py` 与 `tools/import_pet_images.py` 的 `SIZE`/`CX`/`BASE_Y` 改为引用它们，不再各写一份。
- 渲染侧：`ui/frame_animator._fit_canvas` 纵向不再居中，改由 `_foot_y()` 把脚线放到画布底边（128 画布下移 20px，缩放比例不变，猫仍是 93px 高）。
- **闸门**：仅当源图是方形且边长 ≥ `FRAME_SOURCE_SIZE` 时才套脚线契约；更小的手工素材/测试图无从知道脚在哪，保持旧的居中（`should_keep_centering_when_asset_is_smaller_than_contract`）。
- 代价如实登记：内容整体下移 20px，`bubble_anchor` 仍挂在窗口顶 -4px，因此气泡离头顶由 ~11px 变 ~31px（更空但不遮挡）；真机若发现脚部被任务栏下沿盖住，属于「工作区下沿取 `virtualGeometry` 而非 `availableGeometry`」这一既有口径问题（规约 §2.6），本轮不一并改。

## 4. 否决的替代方案

- **在 ui 层做纵向偏移**（否决）：脚线是素材的落盘契约，写进 ui 会让「重新生成素材」与「运行时缩放」两处各猜一次。
- **重切全部 180 帧把脚挪到画布底边**（否决）：改动面是素材本体 + 生成器 + 导入脚本三处，且旧素材用户无法热更；运行时补偿一次即可，且对小尺寸手工素材仍可回退。
- **睡眠期间暂停全部衰减**（否决，违反 FR v1.7 R2）：饱食照掉才能保留「睡一夜醒来喊饿」的真实感。
- **把 `rested()` 改成 `VitalsVO.slept(hours)`**（否决）：`rested(amount)` 语义已是「只回精力」，加一层只是换个名字，且会破坏既有单测 `TestVitalsContract::should_raise_energy_when_rested`。
- **`sleep` 帧率留 2FPS 省 CPU**（否决）：6 帧 × 500ms 的幻灯片观感远差于 4FPS，且空闲 CPU 预算由「隐藏即停轮询」这条更划算地省回来了。

## 5. 测试与基线

- 新增 `tests/unit/test_action_timing.py`（帧率表覆盖/区间/相对快慢、`cycle_seconds`、`ONE_SHOT_DURATION_S` 必须正好是整数圈且减一圈就低于下限）。
- 新增 5 项睡眠单测（`TestVitalsVO` 3 项、`TestPetDecay` 1 项、`TestNightBehavior` 2 项，其中能量事件一项）与 1 项配置解析项。
- ui 冒烟新增：帧率表与 domain 同一对象、镜像只算一次且换皮清空、隐藏停轮询/显示恢复、脚线贴底与小素材保持居中。
- 基线：**862 项全绿**，覆盖率门槛 `fail_under=90` 实测 **99.16%**（TOTAL 1894 stmts / 16 miss，未触达行集合与 v1.7 登记的同一份防御分支清单一致）。
