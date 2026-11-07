# 桌面宠物 WisePet 技术设计 v1.22

- 版本：v1.22
- 状态：已实现（待评审）
- 日期：2026-11-07
- 对应需求：[FR v1.21](../../FR/v1.21/desktop-pet-PRD-v1.21.md)（C38）
- 性质：落地登记，不回改历史版本

## 1. 分层与落点

遵守既有依赖方向 `ui → app → domain ← infra`；domain 零三方依赖，app 不直接 import infra
（两个新仓储由组装根注入）。**新模块为主、既有文件最小接线**，避免再动已经被真机返工多次的
设置窗 / 聊天窗主体。

| 层 | 文件 | 职责 |
|---|---|---|
| domain | `src/WisePet/domain/product.py` | `SCENE_PRESETS` / `apply_scene()`（`dataclasses.replace` 组合）、`growth_decoration()`、`BehaviorContext` + `context_hint()`、`is_push_notification()` |
| domain | `src/WisePet/domain/config_model.py` | 新增 `QuietBudgetSettings` / `BehaviorSettings` / `MemorySettings` / `ActionSettings` 四分节；`from_dict` 用 `_bool` / `_int_clamp` / 新增 `_choice` 做旧配置兼容与白名单回落，`to_dict` 由 `asdict` 自动带上 |
| infra | `src/WisePet/infra/product_store.py` | `QuietStore`（按天计数）/ `MemoryStore`（三类事实），复用 `atomic_write` 与损坏备份 |
| app | `src/WisePet/app/quiet_budget.py` | `QuietBudget.allow(source, push=None)`：关预算 / 非推送放行、muted 全停、按自然日滚额度、超额记 INFO 日志 |
| app | `src/WisePet/app/long_term_memory.py` | 正则抽取名字 / 近况 / 项目名，`facts()` / `prompt_line()` / `hydrate()` / `clear()`；`_BAD_NAMES` 防「我是有点累」被误当名字 |
| app | `src/WisePet/app/chat_history.py` | `search(query, limit)`：跨所有日期、大小写不敏感、时间倒序、空查询返回空 |
| app | `src/WisePet/app/action_protocol.py` | `permission_required()` 三级、`action_kind()`、`action_preview()`（完整正文 / 逐页要点） |
| app | `src/WisePet/app/pet_controller.py` | 可选 `context_provider`；因果提示只作用于**非轮播**触发，排在 showcase 之后、概率分支之前，**不调 `_rng`** |
| ui | `src/WisePet/ui/onboarding_dialog.py` | 三步贴纸卡 + `try_chat` / `try_reminder` / `completed` 信号；只构造不自动显示 |
| ui | `src/WisePet/ui/action_confirm.py` | `ActionConfirmDialog`：完整预览只读框、风险说明、`set_remember_enabled()`、纯函数 `decide()` |
| ui | `src/WisePet/ui/pet_window.py` | 可选 `growth_provider`；`_draw_with_growth()` 以脚线为锚缩放 + 头顶徽章，无装饰时仍走原 `drawPixmap(0,0,frame)` |
| ui | `src/WisePet/ui/chat_window.py` | `search_provider` + `#search` 行；`search()` / `searching` 状态；搜索态下 `receive_record()` 不冲掉结果 |
| ui | `src/WisePet/ui/settings_dialog.py` | 第 6 个分区「体验与隐私」；`_load_from` 用 `getattr` 兼容旧配置，`collect()` 用 `replace` 并显式传 4 个新字段 |
| ui | `src/WisePet/ui/tray_menu.py` | 场景包子菜单、免打扰开关、撤销、重看引导；`set_muted()` 用 `blockSignals` 防回调自激 |
| 组装根 | `main.py` | 服务装配与 provider 注入、`_on_speak` 预算闸门、`_system_prompt` 拼记忆、写盘权限 / 快照 / 撤销、`main()` 首次运行判定 |

## 2. 三条关键数据流

1. **打扰预算**：各服务 `EventBus.publish(SpeakRequestedEvent)` → `_on_speak` 用
   `is_push_notification(source, title)` 判定 → `QuietBudget.allow(..., push=...)` → 放行才播报。
   `system` 不能整类算推送（工具执行反馈也用它），故用 `PUSH_SOURCES` + `PUSH_TITLES` 双判据。
   AI 流式回复走 `_on_chunk` 不经过此口，天然不受影响。
2. **长期记忆**：`send_message` → `LongTermMemory.learn_from()` → 落 `memory.json` +
   `hydrate()` 回填 `DialogEngine.memory`；`_system_prompt()` 每次渲染时追加 `prompt_line()`。
   快速提醒通道不学习（「1 分钟后提醒我」不是事实）。
3. **写盘权限**：`_handle_action` → `permission_required()` → `auto` 直接执行 / `deny` 拒绝 /
   `confirm` 弹 `ActionConfirmDialog`（可记住授权 → `apply_config` 改为 auto）→
   `_snapshot_before_write()` 存原始字节 → 执行 → 存 receipt 并启用撤销菜单 →
   `_undo_last_action()` 还原字节或删除新文件。

## 3. 否决方案（避免重复试错）

| 否决 | 理由 |
|---|---|
| 把预算闸门放进 `EventRouter._dispatch` | 会误伤聊天事件，出现「问了却没回」 |
| 把 `system` 整类算主动推送 | 成长播报、工具反馈、AI 兜底都用 system，会被静默吞掉 |
| 每次自主触发都按情境选动作 | 挤掉固定轮播，THINKING / SLEEPING 又要漏号（v1.17 已踩过一次） |
| 因果驱动里新增 `_rng.random()` | 打乱既有 StubRng 顺序断言 |
| 为成长外观新增素材 | 用户为 4 张图返工 4 轮，改绘制层零素材风险 |
| 场景包重建 `AppConfig` | 表单外的位移/概率参数会被静默打回默认值，且可能丢 api_key |
| 引导用 `exec()` 模态 / 在 `__init__` 里 `show()` | offscreen 测试挂死；改为 `QTimer.singleShot(900, ...)` + 真实入口判定 |
| 接天气 / 日程 API 做因果驱动 | 引入联网依赖与隐私口径，先用本机可观测事实兑现同等价值 |

## 4. 验证

- 新增 28 项回归：`tests/unit/test_product_features.py`（16）、`tests/unit/test_pet_controller.py`（因果驱动 3）、
  `tests/ui/test_product_features_ui.py`（9，含引导 / 检索 / 预览 / 场景包 / 免打扰 / 撤销）。
- 既有契约只碰一处：设置窗分区数 5 → 6（`test_settings_dialog.py` 已同步）。
- 全量 `1198 passed`、覆盖率 `94.77%`（`fail_under = 90`，TOTAL 3630 stmts / 190 miss）、改动文件 lints 0。
- **真机待目检**：首次引导三步与 1 分钟提醒、等级 ≥4 的徽章观感、免打扰开关即时生效、
  跨日搜索结果排序、写盘预览窗与撤销还原、夜间行为不被因果驱动锁死在 SLEEPING。
