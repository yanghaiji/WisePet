# 桌面宠物 WisePet 技术设计 v1.31

- 版本：v1.31
- 状态：已实现（待评审）
- 日期：2026-11
- 对应需求：[FR v1.31](../../FR/v1.31/desktop-pet-PRD-v1.31.md)（记忆体系升级 + 本地多用户）
- 性质：增量落地登记；零新增三方依赖（httpx + 标准库）

## 1. 分层与落点

| 层 | 文件 | 改动 |
|---|---|---|
| app | `summary_memory.py`（新） | `SummaryMemory`（observe/异步压缩/reconcile/prefix_line）+ `SummaryStore`（summary.json） |
| app | `long_term_memory.py` | `remember/entries/delete_entry/_decay_entries`（TTL 90 天、提示词 8 条上限）；reload/clear 覆盖自由事实 |
| app | `knowledge_base.py` | `_cosine`、`_embed_chunks`（批量 ≤500/批 32）、`_query_vector`、BM25+1.2×余弦混合、三级降级 |
| app | `agent_tools.py` | `remember` 工具 schema + 执行分支 |
| app | `memory_timeline.py` | 「记住的事」条目聚合 + `memory_fact:` 分派删除 |
| app | `chat_service.py` | `summary_observer` / `summary_prefix_provider` 注入；user/assistant 各路径喂缓冲；摘要作为 messages[1] |
| infra | `openai_client.py` | `embeddings()` 批量调用（index 排序复原、语义就绪校验、错误分类一致）；`complete()` 委托链不变 |
| infra | `product_store.py` | `load/save_fact_entries` / `delete_fact_entry`；`save()` 保留磁盘上已有 fact_entries（防覆写丢失） |
| infra | `profile_store.py`（新） | `safe_name`（防穿越）/ `guard_port_for`（crc 派生端口）/ `active/set_active/ensure/data_dir_for` |
| domain | `ports.py` | `AIConfig.embedding_model` + `embedding_endpoint` + `semantic_ready` |
| domain | `config_model.py` | `AIProfile.embedding_model`、`MemorySettings.summary_enabled`、`AssistantSettings.semantic_search` |
| ui | `settings_dialog.py` | AI 区「Embedding 模型」；体验与隐私加「会话摘要」「语义检索」；collect/_load_from 同步 |
| 组装根 | `main.py` | 入口接 profile（argv/active/守卫端口/迁移/日志目录）；装配摘要与 embedding_fn；`_restore_chat_session` 后 reconcile；命令面板「切换身份」「资料索引状态」带语义信息 |
| tests | `tests/unit/test_memory_upgrade.py`（新，25 项） | 四大块 + embeddings httpx.MockTransport 解析/错误分类 + 配置 roundtrip |

## 2. 关键实现决策

### 2.1 摘要异步、永不阻塞回复
摘要压缩走 `threading.Thread`（daemon），`_inflight` 防并发；worker 线程只 observe 不等待。
失败（网关超时等）静默吞掉等下次——摘要只增强不添堵。前缀注入点在 ChatService
`build_messages` 之后 `insert(1, ...)`，不侵入 domain 层 `ChatSession`。

### 2.2 自由事实与三键同文件但独立列表
`memory.json` 结构扩展 `"fact_entries": [{key,value,day}]`。**踩过的坑**：
`MemoryStore.save()` 整体覆写会把 fact_entries 抹掉——save() 现在读磁盘保留该字段
（回归测试覆盖）。时间衰减在读取侧做（TTL 90 天 + 8 条上限），落盘不删数据，
「可遗忘」仍由时间线显式执行。

### 2.3 语义检索三级降级
1. `semantic_search` 关 / `embedding_model` 空 → 纯 BM25；
2. 建索引时 embedding 批量失败 → 索引无向量（纯关键词）；
3. 查询时 embedding 失败 → 该次查询纯 BM25。
任何一级都不影响功能可用性——语义只是增强。向量体积控制：MAX_VECTOR_CHUNKS=500、
float 保留 5 位小数（knowledge.json ≈4MB 上限）。

### 2.4 本地身份：active 是「下次启动」而非热切换
数据目录在构造期定死（JsonConfigRepository/data_dir），热切换会撕裂写盘目标。
故 active_profile.txt 只影响下次启动，UI 明确提示「重启生效」；守卫端口按
`crc32(名字) % 999` 派生，同身份互斥、不同身份可并行（家人可同时各开各的宠物）。

## 3. 否决方案

| 方案 | 否决理由 |
|---|---|
| langchain ConversationSummaryMemory | 依赖全家桶；自研 60 行覆盖同一模式且可测（见 v1.30 同类决策） |
| 每轮都调 AI 摘要 | token 成本与延迟不可接受；攒 8 条批压缩 |
| 事实抽取交给正则扩展 | 长尾事实（偏好/约定）正则不可枚举；function calling 的 remember 工具更可靠，正则三键保留兜底 |
| 向量库（sqlite-vec/faiss） | 新依赖；500 chunk 规模线性余弦足够快（<10ms） |
| 云账号 + 登录墙 | 与「本地可信」核心轴正面冲突（产品规划 §11 完整决策记录） |
| profile 热切换 | 数据目录构造期定死，热切换撕裂写盘目标；重启生效 + UI 提示 |

## 4. 验收

- 新增 `tests/unit/test_memory_upgrade.py` 25 项；全量 **1454 → 1479 passed / 0 failures / 0 errors**；
- 改动文件 lints 0；旧功能零回归（含 v1.29 时间线、v1.30 agent 循环）。

## 5. 真机待目检

- 长会话（>20 轮）后宠物是否仍记得开头约定；`summary.json` 是否跨重启生效；
- 对话中自然说出偏好，模型是否调用 remember 工具；时间线「记住的事」可否删；
- 智谱 embedding 接口（`embedding-3`）是否兼容；语义开关开/关的检索质量差异；
- `--profile=家人` 与默认身份并行运行是否互不干扰、数据是否完全隔离。
