# 桌面宠物 WisePet PRD v1.30

- 版本：v1.30
- 状态：已实现（待评审）
- 日期：2026-11
- 性质：增量需求（轴② B 类首项：原生 function calling + agent 工具循环），不回改历史版本

## 0. 背景

意图协议此前是「提示词求模型输出 JSON」，历史上反复出现「模型不守协议」的坑；
且一轮对话只能执行一个动作。本版引入 **OpenAI tools 协议（原生 function calling）**
与 **agent 工具循环**（LangGraph 式多步编排的自研实现），零新增三方依赖（仍是 httpx）。

## 1. R1 原生 function calling

- 请求带 `tools` + `tool_choice=auto`（OpenAI tools 协议），模型返回结构化 `tool_calls`
  而不是自由文本里的 JSON——解析可靠性大幅提升
- 空工具列表不下发 `tools` 字段，兼容挑剔的网关
- **兼容兜底**：网关不支持 tools（400/422/协议错误）时按 `base_url|model` 记忆并
  自动降级为纯文本流式 + 既有 JSON 协议，老的兼容路径一条不丢；auth/网络等错误照旧上抛

## 2. R2 agent 工具循环（多步）

- 循环：模型请求工具 → 本地执行 → 结果以 `role=tool` 回喂 → 继续（上限 6 步）；
  超限后强制一次无工具总结，避免无限循环与 token 失控
- 工具清单（9 个）：只读 6 个（read_file / list_folder / search_knowledge /
  search_history / recall_memories / get_status）+ 写盘 3 个（remind / create_file / make_ppt）
- **安全边界不因 agent 放宽**：
  - 只读工具限定「资料文件夹」信任域（拒符号链接、防目录穿越、限大小）；
  - `remind` 无写盘风险，循环内直接排期；
  - `create_file` / `make_ppt` **不在线程内执行**，作为 pending_action 交回主线程
    走既有的三级权限 + 完整预览确认 + 审计 + 撤销链路（human-in-the-loop 中断点）
- 最终回复以拟流式外发（视觉与真流式一致）；取消时仍发完整 done（口径与本地模式一致）

## 3. R3 开关与降级可观测

- 设置 →「体验与隐私」→「工具调用」总开关（默认开）；关闭即整体走原纯文本流式
- 降级发生时落日志（网关不支持 tools），同一网关不重复试错

## 4. 验收项

| 组 | 验收 |
|---|---|
| AC-TOOLS-1 | 9 个工具 schema 结构合法；`complete()` 改造后后台任务（新闻/鼓励/周报/问候）零回归 |
| AC-LOOP-1 | 多步循环：list_folder → read_file → 最终回答；工具结果以 role=tool 回喂 |
| AC-LOOP-2 | 写盘工具产出 pending_action 交主线程确认；remind 循环内直接排期 |
| AC-LOOP-3 | 步数上限强制总结；PROTOCOL 失败自动降级（按网关记忆，不重复试错）；auth 照旧上抛 |
| AC-CHAT-1 | ChatService agent 路径拟流式外发 + pending_action 事件；纯文本路径零回归 |
| AC-SAFE-1 | read_file/list_folder 限定资料文件夹、防目录穿越、拒符号链接与超大文件 |
| AC-CFG-1 | 「工具调用」开关关闭即走原路径；配置 roundtrip 不丢 |

## 5. 范围外

- 不做并行工具调用、不做流式 tool_calls（多步内非流式更稳，拟流式外发兜住体验）；
- 不引入 langchain/langgraph 依赖（模式已自研，见产品规划 §10 的分层路线）；
- 第三方插件注册自定义工具仍属 P3。
