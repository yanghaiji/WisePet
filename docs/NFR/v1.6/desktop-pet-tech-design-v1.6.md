# 桌面宠物 技术设计 v1.6 —— AI 链路 protocol 错误类（对应 PRD v1.5 C7）

> 本版只覆盖 C7 的实现。四层架构见 v1.0，AI 网关初版（SSE 解析与五类错误映射）见 v1.0 §M4，本地小脑见 v1.3/v1.5。
> 按 `docs/规约.md`：修订不回改历史版本；v1.0 的「状态码 → 错误分类」映射**继续有效**，本版在其后补一道响应体检视。

## 1. 变更概览

| 位置 | 变更 | 对应需求 |
| --- | --- | --- |
| `domain/errors.py` | `AIGatewayError` 新增 `PROTOCOL = "protocol"` 并加入 `KINDS` | R2 |
| `infra/openai_client.py` | 新增 `_dict_error()` / `_body_error()`；`probe()` 状态码之后校验响应体；`stream_chat()` 校验 content-type 与「零内容流」；`_iter_sse()` 对 data 体内的错误字段直接抛错 | R1/R2/R4/R5 |
| `app/chat_service.py` | `FALLBACK_TEXTS` 增加 `PROTOCOL` 文案（指向 Base URL 该怎么填） | R3 |
| `tests/integration/test_openai_client.py` | 新增 8 条 protocol 用例；改写 2 条把「静默」当契约的旧用例 | R1 |

## 2. 三个判定点（顺序即优先级）

```
状态码分类（原有） →  ① content-type 非 SSE？读体 → _body_error → protocol
                   →  ② SSE data 体内含错误字段？ → protocol
                   →  ③ 流正常结束但 emitted == 0 → protocol
```

- **① 只在 content-type 非空时判定**：缺省该头时不拦，直接走 SSE 解析（仓内已有用例就是无该头的形态），避免把省略 content-type 的自建网关误判死。
- **③ 必须放在生成器自然耗尽之后**：消费者取消（`break`）时生成器不会被推进到这一行，因此不会在用户主动打断时误报；同时 `GeneratorExit` 属 `BaseException`，不被本函数的 `except httpx.*` 吞掉。
- `_iter_sse` 增加 `isinstance(obj, dict)` 保护：网关可能 yield `[1,2,3]` 这类合法 JSON 但非对象的分片。

## 3. 错误体的两种风格（都来自真实网关）

| 风格 | 样例 | 提取结果 |
| --- | --- | --- |
| OpenAI | `{"error":{"code":"invalid_api_key","message":"key is wrong"}}` | `error: key is wrong` |
| 智谱 | `{"code":500,"msg":"404 NOT_FOUND","success":false}` | `msg: 404 NOT_FOUND`；若无 msg 字段则 `网关返回错误码 500` |

字段顺序 `message/msg/error/detail`，dict 值再取一层 `message/msg`。`code` 缺省视为 `"0"`（OpenAI 成功体不带 code），只有显式的非 0/200 或 `success: false` 才判错——**宁可漏判成"无内容"兜底，也不把成功体误判为错误**。

## 4. 探活为何要看体

`probe()` 原先只 `_raise_status_code`，于是智谱 `/api/anthropic` 这种「200 + 体内 404」会让设置窗显示**连接成功 ✓**，而聊天框毫无反应——这正是用户报的现象。现在两步：`_body_error` 有内容 → protocol（设置窗会把 detail 括号显示出来）；否则要求体内出现 `choices`，不然判「Base URL 可能不是 OpenAI 兼容地址」。

## 5. 真机验证（同一把 Key，非 mock）

| 配置 | 结果 |
| --- | --- |
| `https://open.bigmodel.cn/api/anthropic` + `glm-4-flash` | probe 与 stream 均 `protocol \| msg: 404 NOT_FOUND`，日志含 `non-SSE chat response (content-type=application/json)` |
| `https://open.bigmodel.cn/api/paas/v4` + `glm-4-flash` | probe OK，流式拿到回复文本 |
| `…/api/paas/v4` + `GLM-4.7-Flash` | 429 `1305 该模型当前访问量过大`（rate 类，非本次改动引入） |

## 6. 测试与覆盖率基线

| 项 | v1.5 | 本版 |
| --- | --- | --- |
| 用例总数 | 589（unit 363 / integration 150 / ui 76） | **598**（unit 364 / integration 158 / ui 76） |
| 语句覆盖 | 1628 stmts / 9 miss / 99.45% | **1671 stmts / 9 miss / 99.46%** |
| `openai_client.py` | 100% | **100%**（新增分支全部有归属用例） |

未命中 9 行与 v1.5 完全同一批（平台专属 + 三条刻意不补的防御分支），见 NFR v1.5 §7。

被改写的两条旧用例登记：`should_send_auth_and_stream_flag_when_requesting`、`should_skip_malformed_and_empty_choice_payloads_when_streaming`——它们原本断言「零内容流返回空列表」，而零内容沉默恰是本次要修的缺陷，故按新契约改写，不是为通过而改。

## 7. 已知遗留

- 网关用 200 + 完全自定义体（既无错误字段又无 `choices`）时，聊天侧只能给出「响应不是流式」这种形态描述，具体原因需看日志里的 200 字预览。
- `reasoning_content`（思考模型）不进气泡：只取 `delta.content`，实测 `glm-4.5-flash` 在小 `max_tokens` 下 content 为空 → 会走 protocol。本项目不传 max_tokens，正常对话不受影响，但用户若换用极小上下文配额的模型可能撞上。
- 未在设置窗对 Base URL 做格式预校验（结尾是否 `/v1`、`/v4`）：合法的自建网关路径千奇百怪，写死正则反而会拦错，改为「试一次让服务端说话」。
