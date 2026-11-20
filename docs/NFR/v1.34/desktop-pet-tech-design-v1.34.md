# WisePet 技术设计 v1.34

- 版本：v1.34
- 状态：已实现（待评审）
- 对应需求：FR v1.34（markdown 渲染补登 / 度量口径校准 / 模型能力护栏 / 主动关怀闭环）

## 1. 模块清单

| 模块 | 职责 |
|---|---|
| `ui/markdown_render.py`（补登） | 基于 mistune 的完整 markdown → 贴纸风富文本 HTML；`escape=True` 防注入；图片降级链接；缺 mistune 降级纯文本 |
| `ui/resize_grip.py`（补登） | `QSizeGrip` 子类，松发 `user_resized` 信号 |
| `domain/metrics.py`（新） | 有效互动事件集合、`engaged_days` 口径、四护栏阈值与 `guardrail_status` 纯函数、看板标签常量 |
| `domain/model_capability.py`（新） | 模型三档推断 / 归一 / 工具准入 / 能力边界提示，全纯函数 |
| `domain/care.py`（新） | 节气表与 `solar_term`/`care_term`、`context_score`、`suggested_interval` 纯函数 |
| `app/telemetry_service.py` | `compute_metrics` 新增 `engaged_days`、`ai_fallback_rate`、`guardrails` 字段，兼容既有字段 |
| `app/event_triggers.py` | 新增 `RULE_SOLAR_TERM` 候选与 `solar_term_line`；构造器加两个可选 provider |
| `ui/stats_dialog.py` | 新增北极星标签与四个护栏灯 + `set_product_metrics()` |
| `main.py` | 六处埋点接线、模型分级工具门禁与提示注入、节气 provider 注入、看板刷新 |

## 2. 关键实现要点

### 2.1 有效互动口径与兼容策略

`ENGAGEMENT_EVENTS = {"msg:sent", "tool:used", "reminder:confirmed"}`；`engaged_days(days)` 只数
「当天 bucket 里出现过这些事件之一」的天数。`compute_metrics` **保留** `active_days = len(days)`
原语义，另起 `engaged_days` 字段——不回改既有字段，避免 `test_telemetry.py` 的活跃天数断言与
命令面板旧口径失效。

### 2.2 护栏纯函数与「n/a」语义

`guardrail_status(metrics)` 返回 `dict[str, str]`（ok / warn / n/a）：
- 打扰平衡：`reminder_dismiss_rate > 0.5` → warn；
- 大脑可用：`ai_fallback_rate > 0.5` → warn（该值为 None 即「无模型请求」→ n/a）；
- 运行稳定：`crash_count >= 3` → warn；
- 七日留存：`d7_eligible` 为假 → n/a（未满七日不该判留存），否则按 `d7_retained` 判 ok/warn。

模型失败率口径：`model_fails / model_requests`；只把 `model:fail`（`_on_chat_error` 里 track）
计入失败，`feat:tier_lite_blocked` 是正常路由不进分子。中途曾把 `model:fallback` 也并入分子，
后主动修正——「lite 不走工具」是设计行为，算失败会污染护栏。

### 2.3 模型分级与工具门禁

`infer_tier(model)` 用关键字启发式（小写包含匹配）：lite 提示词优先于 pro（如 `gpt-4o-mini`
同时含 `4o` 与 `mini`，因 `mini` 在 lite 表而判 lite，符合「够便宜就该降级」的直觉）。
`normalize_tier(raw, fallback)` 让配置非法值安全回落（本版未接设置窗 UI，走推断档）。
`tools_allowed(tier, enabled)` 是工具循环唯一准入点：`_run_agent` 在 `AgentRunner.run` 前先判，
lite 直接返回 None（沿用 ChatService 的纯文本路径），并 track `feat:tier_lite_blocked`。
`capability_hint(model, tier)` 只在 lite 时返回追加文本，`_system_prompt()` 拼在末尾（弱模型
对尾部敏感），非 lite 返回空串零开销。

### 2.4 节气候选与情境评分接线

`domain/care.py` 用（月, 日）→ 名称的近似表，按 `time.localtime(ts).tm_mon/tm_mday` 匹配；
`CARE_TERMS` 只挑 10 个有情绪价值的节气。`context_score` 是纯函数：`focusing/quiet` 直接 0，
深夜减 0.6、`dismiss_rate > 0.5` 减 0.4，钳制到 [0,1]。

`EventTriggerService` 构造器新增两个**关键字可选** provider：`term_provider(ts) -> str|None`
（默认 `care_term`，按 tick 传入的 `now` 取节气，保证测试时钟一致）、`dismiss_rate_provider()`。
`tick()` 在既有规则之后追加节气候选：复用同一 `fired_today` 去重集合、同一 `event_daily_limit`
总量上限（`remaining = limit - len(fired)`）、先过 `context_score > 0` 才发。默认 provider 在
非节气日返回 None，既有 `test_assistant_features.py` 全部不触发新路径、零回归。发出的
`SpeakRequestedEvent(source="cheer")` 照旧经组装根 `_on_speak` 的 `QuietBudget` 闸门。

### 2.5 关键决策与否决

- **北极星另起字段而非改 `active_days`**（否决：改语义会破坏历史断言与命令面板旧口径，新增字段零风险）；
- **工具门禁放在 `_run_agent` 而非 `AgentRunner` 内部**（否决：改 agent 循环核心路径波及 v1.30 的 20 项回归，组装根拦截最隔离）；
- **节气走本地近似表不接联网 API**（否决：与零依赖/断网可用底线冲突，±1 天对「说句话」足够）；
- **节气作为事件触发的候选而非独立服务**（否决：独立定时器会绕过当日去重与总量上限，重复造闸门）;
- **`suggested_interval` 只提供纯函数不接提醒服务**（否决：改提醒节奏牵动 DND/勿扰/场景包多条链路，本版先交付可测能力避免回归）；
- **模型分级不进设置窗 UI**（否决：新增控件牵动 7 分区滚动/滚轮 guard/校验多处，本版按模型名推断即可切换档位）。

### 2.6 测试与基线

- 新增 `tests/unit/test_quality_metrics.py`（有效互动口径 / 护栏灯 / 模型分级 / 节气候选 / 情境评分 / 降频，含事件触发集成），与 `test_telemetry.py`、`test_assistant_features.py`、`test_product_features.py` 合跑 74 passed；
- 排除依赖缺失文件后单元 + 集成 1086 passed；剩余 19 项失败集中在 `test_pet_assets.py` / `test_import_pet_images.py` 的 PNG 字节级复现（工具链差异，非本次改动）；
- UI 433 passed；3 项 markdown 渲染断言失败系本机未装 `mistune>=3.0`（已在 `requirements.txt`，走 `_fallback_html` 降级路径），装上即恢复，非代码缺陷；
- 本次改动文件 lints 0。

## 3. 验证方式

- 命令面板「陪伴指标」：`有效互动 N 天 | 活跃 N 天 | 提醒关闭率 | 崩溃 | 事件` + 第二行护栏灯；
- 右键「陪伴统计」：顶部北极星行 + 四个灯（●绿/▲橙/○灰）；
- 模型分级：把模型改成 `glm-4-flash` 并开「工具调用」，问附近美食应不触发 `nearby_pois`，日志见 `feat:tier_lite_blocked`；换 `gpt-4o` 正常走工具；
- 节气关怀：把系统日期临时设到立冬（11-07）等关怀节气，当天首次 tick 应说一句节气问候；专注/勿扰时不发。

## 4. 环境说明

本机验证时先后补齐了 `jieba / respx / python-pptx / PySide6 / pytest-qt`（均为 `requirements.txt` / `dev-requirements.txt` 中既有依赖）；`mistune>=3.0` 属可选依赖，缺失时 markdown 渲染降级为转义纯文本、不崩，与 NFR 设计一致。PNG 字节级复现测试受工具链版本影响，非功能回归。
