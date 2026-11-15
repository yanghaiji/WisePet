# 桌面宠物 WisePet 技术设计 v1.29

- 版本：v1.29
- 状态：已实现（待评审）
- 日期：2026-11
- 对应需求：[FR v1.29](../../FR/v1.29/desktop-pet-PRD-v1.29.md)（轴② A 类五项）
- 性质：增量落地登记；守四层 DDD，零新增三方依赖

## 1. 分层与落点

| 层 | 文件 | 职责 |
|---|---|---|
| domain | `task_protocol.py`（新） | `TaskSpec` / `parse_task_command` / `backoff_seconds` / `is_done_signal` / `is_safe_read_target` / `describe_task` |
| domain | `companion.py` | `pick_recalls` 多条 + `resolve_key` 指纹 + `_topic_words` 话题碎片定向消解 + `recall_prompt_lines` |
| domain | `product.py` | `PUSH_TITLES` 加 9 个托付/情境标题 |
| domain | `config_model.py` | `AssistantSettings`（8 字段）+ `CompanionSettings.max_recalls`；四处同步 |
| infra | `task_store.py` / `knowledge_store.py` / `event_store.py`（新） | `tasks.json` / `knowledge.json` / `events.json`，统一 `atomic_write` + 损坏备份回落 |
| infra | `product_store.py` | `ActionAuditStore.delete_at` / `MemoryStore.delete_fact` / `delete_history_at` |
| infra | `presence_store.py` | `recalled_keys` / `resolved_recalls` + `mark_recall_key` / `resolve_recall` / `excluded_keys` |
| app | `task_service.py`（新） | 队列 / 两类执行 / 退避重试 / 只读沙箱 / 完成信号消解 |
| app | `knowledge_base.py`（新） | 切块 + 倒排索引 + BM25 近似打分 + `prompt_context`；复用 `nlp.bigram_split` |
| app | `event_triggers.py`（新） | 纯函数 `evaluate` + 三条规则 + 按日去重封顶 |
| app | `memory_timeline.py`（新） | 8 类来源聚合 + `forget(ids)` 分派真删 + `export_text` |
| app | `companion_service.py` | 清单化：`_load_candidates` / `recall_candidates` / `resolve` / `reset_candidates` |
| app | `long_term_memory.py` | `reload()` 保证删除后内存态一致 |
| ui | `memory_dialog.py`（新） | 贴纸风时间线对话框：多选删除 / 导出 / 刷新 |
| ui | `settings_dialog.py` | 在既有 7 分区内加 12 项控件；`collect()` 透传 `assistant` 与 `max_recalls`；滚轮 guard 补 4 项 |
| 组装根 | `main.py` | 装配 5 个服务/仓储 + 对话框；`tick` 加两处驱动；快速通道加托付与完成信号；`_system_prompt` 注入资料片段；命令面板 4 条新命令；`apply_config` 索引热生效 |

## 2. 关键实现决策

### 2.1 托付为什么不用 AI 解析
沿用 v2.1 快速通道口径：确定性的本地正则，快、省 token、不受模型不守 JSON 协议影响。
解析不了就当普通聊天，不猜不硬凑。`watch_file` 是**只读**的：路径由用户显式给出，
执行层再挡符号链接、非普通文件、>512KB；不执行、不解析代码、不写回。

### 2.2 失败必须可见
`_fail()` 按 `backoff_seconds`（30s→300s 上限）重排；超过 `max_tries` 转 `failed`
并发一条「托付停下了」气泡说明原因。这条是「可托付」的信任前提——静默失败比不做更糟。

### 2.3 RAG 与无参 system_prompt_provider
`ChatService` 只在 AI 分支调 `self._system_prompt_provider()`（无参）。
组装根在 `send_message()` 里缓存 `self._pending_query = text`，
`_system_prompt()` 据此检索注入；AI 未配置时不调用提示词，零额外开销。
索引默认关 + 目录为空，用户不显式指定就绝不读任何文件。

### 2.4 惦记消解从「一竿子」到「定向」
原 `pick_recall` 里任意后续完成信号会放弃全部候选，清单化后不可接受。
新增 `_topic_words`（中文 2-gram + 英文词，过滤虚词），完成信号与候选取话题交集；
含糊的「搞定了」只影响最新一条。既保留旧单条用例语义，又让多件事并存。

### 2.5 时间线是真删不是隐藏
`forget()` 先按倒序处理索引型 id（审计/历史），再处理其它——否则删事实会连带清掉
同 key 历史导致索引错位。删完 `LongTermMemory.reload()` 同步内存态。

### 2.6 不新增设置分区
`tests/ui/test_settings_dialog.py` 硬断言 `== 7` 个 `QFrame#section`；
全部新控件塞进「体验与隐私」与「陪伴进阶」，并用 `replace` 透传新分节，
避免既有「collect 不丢分节」契约被破坏。

## 3. 否决方案

| 方案 | 否决理由 |
|---|---|
| 托付接 AI 做意图解析 / 支持任意命令执行 | 不确定性高、沙箱成本高；写盘仍走既有三级权限 |
| 向量库 / embedding | 引入新依赖；BM25 近似 + bigram 对中文笔记已够 |
| 完成信号一律消解全部惦记 | 清单化要求多件事并存，实测用户会同时惦记周报和笔记 |
| 事件接天气/日历云 API | `AGENT.md` 已否决：联网依赖 + 隐私口径，改用本机可观测事实 |
| 时间线用「隐藏」代替删除 | 与「可遗忘」承诺不符，用户要的是数据真的没了 |
| 新增第 8 个设置分区 | 破坏既有分区数量断言，且菜单/滚动区已够长 |

## 4. 验收

- 新增 `tests/unit/test_assistant_features.py`（35 项）、`tests/ui/test_assistant_ui.py`（10 项）；
- 全量 **1434 passed / 0 failures / 0 errors**，改动文件 lints 0；
- 关键回归：设置窗 7 分区、`collect()` 不丢 `ai/pet/system/companion`、
  `StubRng` 顺序、`TestStartupGreeting`、`PUSH_TITLES` 白名单全部保持通过。

## 5. 真机待目检

- 说「盯着某个日志文件，出现 ERROR 告诉我」后往文件里写一行 ERROR，是否只在命中时开口；
- 指定一个笔记文件夹后，问文档里的问题，AI 回答是否真的引用了片段；
- 离开半小时回来 / 深夜使用时，情境推送是否克制且不刷屏；
- 时间线对话框勾选删除后，重启程序确认对应数据确实不再出现；
- 同时惦记两件事、只完成其中一件，另一件是否还在清单里。
