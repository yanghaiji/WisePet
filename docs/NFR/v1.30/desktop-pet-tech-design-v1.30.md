# 桌面宠物 WisePet 技术设计 v1.30

- 版本：v1.30
- 状态：已实现（待评审）
- 日期：2026-11
- 对应需求：[FR v1.30](../../FR/v1.30/desktop-pet-PRD-v1.30.md)（原生 function calling + agent 工具循环）
- 性质：增量落地登记；零新增三方依赖（仍是 httpx + 标准库）

## 1. 分层与落点

| 层 | 文件 | 改动 |
|---|---|---|
| infra | `openai_client.py` | 新增 `chat_with_tools(messages, cfg, tools) -> (content, tool_calls)`：非流式带 `tools`/`tool_choice`；解析 `choices[0].message.tool_calls[].function`（arguments JSON 字符串→dict，坏 JSON 抛 PROTOCOL）；空 tools 不下发字段；`complete()` 改为委托它（后台任务零回归） |
| app | `agent_tools.py`（新） | `TOOL_SCHEMAS`（`_fn` 工厂消除嵌套括号错误）+ `AgentToolbox.execute(name, args) -> (result, action|None)`：只读 6 工具 + 写盘 3 动作桥接（返回 Action 对象交主线程） |
| app | `agent_loop.py`（新） | `AgentRunner.run(user_text, history) -> AgentOutcome`：多步循环、`role=tool` 回喂、步数上限强制总结、PROTOCOL 降级记忆（`_no_tools` 集合按 base_url\|model）、`_plain_round` 回落 |
| app | `chat_service.py` | `tool_runner` 可选注入；`_try_agent()`：异常映射既有错误链、`stream_pieces` 拟流式外发、取消口径与本地模式一致（done 必达）、pending_action 走 done 事件 |
| domain | `events.py` | `ChatReplyChunkEvent.pending_action`（默认 None，向后兼容） |
| domain | `config_model.py` | `AssistantSettings.tool_calling_enabled`（默认 True）四处同步 |
| ui | `main.py` | 装配 `AgentToolbox`（remind 回调接 `_schedule_remind_action`）+ `AgentRunner`；`_run_agent` 入口（检查开关）；`_on_chunk` 优先处理 `pending_action` |
| tests | `tests/unit/test_agent_loop.py`（新，20 项） | 工具箱沙箱 / 多步循环 / 待确认动作 / 步数上限 / 降级记忆 / role=tool 回喂 / ChatService 集成三条路径 |

## 2. 关键实现决策

### 2.1 非流式多步 + 拟流式外发（而非流式 tool_calls）
流式 `delta.tool_calls` 增量拼接复杂且各网关实现不一；多步工具执行用非流式（每步一次
完整响应）更稳、更易测。最终回答以既有 `stream_pieces` 拟流式外发——工具步本身有
网络延迟，拟流式收尾在体感上没有损失。真流式 tool_calls 留待网关兼容性验证后升级。

### 2.2 写盘动作「中断」而非「线程内确认」
agent 循环跑在 AiWorker 线程，`ActionConfirmDialog.exec()` 必须在主线程。第一版方案：
写盘工具在循环内**不执行**，打包成 pending_action 经 done 事件交回主线程
`_handle_action`（三级权限 + 预览 + 审计 + 撤销原封不动）。这等于把 LangGraph 的
human-in-the-loop 中断点放在了「需要用户确认」处，安全边界零放宽。

### 2.3 只读工具限定资料文件夹信任域
agent 能读文件是隐私敏感能力：`read_file/list_folder` 只允许 `knowledge_dir`
（与 RAG 同一信任域，用户显式设置才存在），`resolve()` 后 `relative_to` 防目录穿越，
拒符号链接、限 512KB。要读别处就把那里设为资料文件夹——边界清晰可解释。

### 2.4 降级按网关记忆
`_no_tools: set[str]`（`base_url|model`）。PROTOCOL 错误视为「网关不支持 tools」，
降级纯文本流式 + JSON 协议；auth/timeout/network 照旧上抛走既有错误链。
同一网关本次进程内不重复试错。

## 3. 否决方案

| 方案 | 否决理由 |
|---|---|
| 引入 langchain / langgraph | 依赖全家桶与零新依赖、打包瘦身、冷启动、单人维护冲突；抽象税让 1350 测试重写。模式已自研（见 §1/§2），langgraph 留给「写盘型后台多步托付」真需求出现时再评估 |
| 流式 tool_calls 解析 | 各网关增量格式不一、拼接复杂；非流式多步 + 拟流式外发体验损失可忽略 |
| 写盘工具在 agent 线程内直接执行 | 绕过三级权限与预览确认，安全边界放宽不可接受 |
| 工具结果全文回喂 | 模型上下文可能被 512KB 文件撑爆；截 6000 字符并保留首个匹配段 |
| pending_action 放进 ChatTurnRecordedEvent | 改动面更大；ChatReplyChunkEvent 只在 done 时携带，向后兼容 |

## 4. 验收

- 新增 `tests/unit/test_agent_loop.py` 20 项；全量 **1434 → 1454 passed / 0 failures / 0 errors**；
- 改动文件 lints 0；`complete()` 改造后新闻/鼓励/周报/问候/提醒润色等后台任务零回归。

## 5. 真机待目检

- 智谱 glm-4-flash 是否接受 tools 协议（不支持时日志应出现降级 warning 且聊天照常）；
- 「看看我笔记里写了什么计划」是否出现 list_folder → read_file 两步工具并给出正确回答；
- agent 发起 create_file 时确认弹窗是否照常出现、「以后都允许」是否生效；
- 工具循环期间取消聊天，气泡是否正常收尾。
