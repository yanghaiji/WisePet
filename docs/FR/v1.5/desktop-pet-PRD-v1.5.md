# 桌面宠物 PRD v1.5 —— AI 链路失败必须可见

> 本文件只记录**相对 v1.4 的需求变更**（四层架构、本地小脑、画风均不重复）。
> 按 `docs/规约.md`：修订不回改历史版本；本版**替换 v1.0 §M4 异常场景**中「错误分类只有五种」的隐含约定。

## 0. 变更清单

| 编号 | 变更 | 类型 | 来源 |
| --- | --- | --- | --- |
| C7 | 配置了 AI 却**没有任何回复**不可接受：凡是「连上了但读不出内容」一律归为新的第六类错误 `protocol` 并显示可读话术，`测试连接` 与 `聊天` 必须给出同一结论 | 补充 v1.0 §M4 | 用户真机反馈「为啥我配置了 但是没用回复呢」，日志显示 `POST .../api/anthropic/chat/completions 200 OK` |

## 1. 问题定位（真机实测）

用户填的是 `Base URL = https://open.bigmodel.cn/api/anthropic`、`Model = GLM-4.7-Flash`。实测同一把 Key：

| 端点 | HTTP 状态 | 响应体 | 旧客户端表现 |
| --- | --- | --- | --- |
| `/api/anthropic/chat/completions` | **200** | `{"code":500,"msg":"404 NOT_FOUND","success":false}` | 状态码合法 → 不报错；体内无 SSE 行 → 解析出 **0 段文本** → 气泡不出现、无提示，日志只有 200 OK |
| `/api/paas/v4/chat/completions` + `glm-4-flash` | 200 | 正常 `choices` | 正常出字（本次实测拿到回复） |
| `/api/paas/v4/chat/completions` + `GLM-4.7-Flash` | 429 | `{"error":{"code":"1305","message":"该模型当前访问量过大…"}}` | 归类 rate（文案尚可，但原因其实是该模型过载） |

根因：**只看 HTTP 状态码**。兼容网关普遍会把业务错误包在 200 里，而「零内容流」在旧实现里等价于成功。

## 2. 需求条目

- **R1 失败必须有反馈**。AI 链路任何一次调用，只要最终没有产出可见文本，用户必须看到一句解释，不得静默。
- **R2 「连上但读不懂」单独成类**。新增 `protocol` 错误类型，覆盖三种形态：① 2xx 但响应体不是流式；② 响应体（含 SSE data 内）带错误字段；③ 流正常结束却一段内容都没有。
- **R3 话术要能自救**。`protocol` 的用户文案必须指出**下一步做什么**（Base URL 要填 OpenAI 兼容地址，一般以 `/v1` 或 `/v4` 结尾），而不是笼统的「服务开了小差」。
- **R4 测试连接与聊天结论一致**。设置窗「测试连接」不得对同一个端点给出「连接成功 ✓」而聊天却没回复——探活必须校验响应体形状（能读出错误信息，或至少含 `choices`）。
- **R5 日志保留原始线索**。判定为 `protocol` 时，把 content-type 与响应体前 200 字写入 warning，便于二次定位（不写 Key、不落盘敏感内容）。

## 3. 验收标准

- **AC-AI-1**：Base URL 填 `https://open.bigmodel.cn/api/anthropic`，点「测试连接」→ 状态栏显示连接失败，且括号内可见网关原文（`404 NOT_FOUND`）。
- **AC-AI-2**：同配置下发起聊天 → 气泡出现「读不懂 + Base URL」提示，**不再是一片空白**。
- **AC-AI-3**：Base URL 填 `https://open.bigmodel.cn/api/paas/v4`、Model 填 `glm-4-flash` → 测试连接成功，聊天气泡逐段出字。
- **AC-AI-4**：网关返回 200 + `{"error":{"message":"..."}}` 的 SSE 流 → 归类 `protocol` 并把 message 带进 detail。
- **AC-AI-5**：流式正常但内容为空 → 归类 `protocol`，detail 说明缺少 `choices[0].delta.content`。
- **AC-AI-6**：错误类型扩展后，每种 `AIGatewayError.KINDS` 都有对应用户文案（既有参数化用例覆盖）。

## 4. 边界与不承诺

- 不承诺识别所有网关的错误格式：只覆盖 `message/msg/error/detail` 四类字段与 `code/success` 风格；识别不出时仍以「不是流式 / 无内容」报错，不会退回静默。
- 不内置任何厂商地址的自动纠正（用户填什么就打什么），只提供文案提示与 README 表格。
- 不支持 Anthropic 原生协议（`/v1/messages` 的请求体与鉴权头不同），需要该协议请换 OpenAI 兼容地址。
