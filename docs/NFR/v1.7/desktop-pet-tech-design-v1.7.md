# 桌面宠物 技术设计 v1.7 —— 回复来源可观测（对应 PRD v1.6 C8）

> 本版只覆盖 C8 的实现。四层架构见 v1.0，本地小脑四段路由见 v1.3/v1.5，AI 链路 `protocol` 错误类见 v1.6。
> 按 `docs/规约.md`：修订不回改历史版本；本版**只在既有链路上加日志与一个诊断字段**，不改任何对话行为。

## 1. 变更概览

| 位置 | 变更 | 对应需求 |
| --- | --- | --- |
| `app/chat_service.py` | 新增 `REPLY_PREVIEW_CHARS` / `_preview()`；请求行、结果行、取消行、失败行统一带 `source=` 与 `chat #N` 编号；流式路径统计首段耗时 | R1/R2/R3/R6 |
| `app/local_responder.py` | 新增公开字段 `last_intent`（`reply()` 写入、`reset()` 清空），与 `matched_rule()` 同口径 | R4 |
| `main.py` | 新增 `_log_chat_brain(when=)`，在 `start()` 与 `apply_config()` 各调一行 | R5 |
| `infra/openai_client.py` | `probe()` 成功时补一行 `ai probe ok model=… endpoint=…`（v1.6 的判定顺序不变） | R5 |
| `tests/unit/fakes.py` | `FakeResponder` 增加 `last_intent`（与真实现同形，否则 ChatService 日志取不到字段） | R4 |

## 2. 日志字段口径（实测样本）

下面这段是离线脚本 `tmp/log_demo.py` 的实跑输出节选（假流式网关，真跑 ChatService，不是断言拼的样板）：

```
INFO [ai_bot.app.chat_service] chat #1 source=ai request model=glm-4-flash endpoint=https://api.test/v1/chat/completions msgs=2 prompt_chars=10
INFO [ai_bot.app.chat_service] chat #1 source=ai done chunks=5 reply_chars=27 first_chunk=0.00s elapsed=0.00s preview=你好呀，我是通义千问，很高兴见到你！↘今天过得怎么样？
INFO [ai_bot.app.nlp] tokenizer backend = jieba 0.42.1
INFO [ai_bot.app.chat_service] chat #2 source=local intent=stress reply_chars=40 elapsed=0.69s preview=我在。这件事不用现在就解决，你先骂两句也行，本地小脑连网都不上，真的没有第三人。
```

| 字段 | 出现处 | 含义与取数 |
| --- | --- | --- |
| `chat #N` | 全部行 | 串行队列内的自增序号，用来把请求行与结果行串起来（无并发，不需要 trace id） |
| `source=` | 全部行 | `ai` 走网关，`local` 走本地小脑；判据仍是 `AIConfig.configured`，与路由决策同一处 |
| `endpoint=` | 请求行 | `cfg.chat_endpoint`（Base URL + `/chat/completions`），填错地址时这一行直接暴露问题 |
| `msgs` / `prompt_chars` | 请求行 | 交给模型的消息条数与总字符数，用于判断上下文带了多少 |
| `chunks` / `reply_chars` | 结果行 | 收到的段数与拼起来的回复长度 |
| `first_chunk` | AI 结果行 | 从发起到第一段内容到达的等待；无内容时为 `n/a` |
| `intent=` | 本地结果行 | 命中的路由名（规则名 / `knowledge` / `follow_up` / `fallback`） |
| `preview=` | 结果行 | 回复前 60 字，换行折成 `↘` |

## 3. 三个实现决定

- **`last_intent` 读字段，不重跑 `matched_rule()`**：上一轮 `reply()` 已经写过话题与会话记忆，重跑路由可能落到不同分支，给出的意图就不是「刚才那条真实答案」的意图。故把命中名在 `reply()` 里就地存下来。
- **本地行不记 `first_chunk`**：本地链路是「整句生成 → 切片投递」，首段耗时恒等于 0，写进日志是假数据。真正的一次性成本落在 `elapsed` 上——实测首轮 `0.69s`，即 jieba 词典惰性加载，之后同会话内为毫秒级。
- **`first_chunk=n/a` 而不是 `0.00s`**：网关一段内容都没给（v1.6 的 protocol 形态）时，报 `0.00s` 会被读成「秒回但没内容」，掩盖真实故障。

## 4. 为什么把摘要提到 INFO

`setup_logging()` 的默认级别就是 INFO，且没有运行时开关。若正文只写 DEBUG，用户看到的仍是「只有 httpx 那行 200 OK」，C8 的诉求（分清谁答的）落不了地。因此摘要进 INFO，同时把代价压到最小：只截 60 字、只落宠物说出去的话（用户输入原文仍不进日志）、日志文件本身是 1MB × 3 轮转的本机文件。

脱敏两道防线不变：新增字段只打印 `model` 与地址，不打印 Key；`logger_setup.py` 的 `SecretMaskFilter` 仍对 `sk-*` 与 `Bearer *` 兜底掩码。

## 5. 锚点行为

`_log_chat_brain()` 在启动与保存配置后各打一行，形如 `chat brain=ai when=boot model=… base_url=…` / `chat brain=local when=…（未配置 api_key，回复全由本地小脑作答）`。它回答的是「从现在起的回复由谁作答」，与逐条 `source=` 互为上下文：只看一条 `source=local` 而没看到锚点，用户仍可能怀疑是不是配置没生效。

## 6. 测试与覆盖率基线

| 项 | v1.6 | 本版 |
| --- | --- | --- |
| 用例总数 | 598（unit 364 / integration 158 / ui 76） | **611**（unit 374 / integration 159 / ui 78） |
| 语句覆盖 | 1671 stmts / 9 miss / 99.46% | **1692 stmts / 9 miss / 99.47%** |
| `chat_service.py` | 100% | **100%**（新增分支含 `first_chunk=n/a` 全部有归属用例） |

新增 13 条：`TestSourceLogging` 8 条（AI 请求行字段、`n/a`、本地 `intent` 与零网关调用、preview 截断长度、换行折行、序号递增、取消行、失败行）、`test_local_responder.py` 2 条（`last_intent` 与 `reset()` 清空）、`test_main_assembly.py` 2 条（启动/保存配置时的 brain 锚点）、`test_openai_client.py` 1 条（探活成功留痕）。

本轮内部改写与删除的用例登记（都在同一次任务里先写后改，不是推翻历史契约）：`should_keep_reply_body_out_of_info_log_when_local` 原断言「INFO 不含正文」，与 C8 R3 直接冲突，改为 `should_keep_reply_preview_short_in_info_log_when_local`（断言含 preview 且截断长度等于 `REPLY_PREVIEW_CHARS`）；`should_log_preview_when_debug_level_enabled` 随 DEBUG 行合并进 INFO 而删除，其折行断言并入新用例。

## 7. 已知遗留

- 级别仍是编译期默认 INFO，未提供环境变量开关；要更细只能改 `setup_logging(level=…)` 调用点。
- `httpx` 自身的 `HTTP Request: POST …` 行与我们的行格式不同，串联靠 `chat #N` 附近的时序，不做前缀对齐。
- `intent=` 与规则名同字段（`knowledge`/`follow_up` 这类非规则名也会出现），口径就是「路由名」，不再拆分层级——拆层需要 `_route` 返回段号，收益不大。
- 气泡与设置窗都不显示来源标记（PRD v1.6 §4 明确不承诺），可观测性只在日志侧。
