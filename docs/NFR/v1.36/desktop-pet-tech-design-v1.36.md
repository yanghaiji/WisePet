# WisePet 技术设计 v1.36

- 版本：v1.36
- 状态：已实现（待评审）
- 对应需求：FR v1.36（情感陪伴路线 A 五件事）

## 1. 模块清单

| 模块 | 职责 |
|---|---|
| `domain/companion_depth.py`（新） | 记忆可见化 / 情绪语气 / 作息共鸣 / 小脾气 / 可发现性，全纯函数 |
| `domain/task_protocol.py` | 新增 `WATCH_PAGE` + `_WATCH_PAGE_RE` + `is_safe_watch_url` + `page_changed` |
| `domain/pet.py` | 新增只读 `vitals` 视图（不破坏 I3） |
| `app/task_service.py` | 注入 `page_fetcher` + `_run_watch_page`（基线存 meta，限长） |
| `app/companion_service.py` | 新增 `gap_hours()` 只读 getter（供 prompt 用） |
| `ui/onboarding_dialog.py` | 第一步文案改为引导说名字（信号/技能契约不变） |
| `main.py` | `_companion_depth_lines()` 组装五件事的 prompt 增量；`_fetch_page_text()` 抓页面；`_maybe_nudge` 接小脾气话术 |

## 2. 关键实现要点

### 2.1 记忆可见化与被问才答互补

`memory_visibility_line(facts)` 仅在有长期事实时返回，注入系统提示词鼓励「话题相关时
自然提起一次」；与 v1.31 `long_term_memory.prompt_line` 的「问了才依据事实答」是
两条并行引导，前者主动、后者被动。`name_first_line(has_name, first_message)` 让首次
对话自然叫名字——Aha moment 的语言层落点。

### 2.2 情绪/作息注入与只读边界

`Pet` 新增 `@property vitals` 返回内部 `VitalsVO`（frozen 值对象），只供 prompt/UI 读，
所有写仍走行为方法（不变量 I3 不变）。`mood_tone_line`/`schedule_empathy_line` 按数值
返回语气片段，拼进 `_system_prompt()` 的 `_companion_depth_lines()`，与既有 `anchor`
（人格锚）、`ability`（能力护栏）同在提示词尾部（弱模型对尾部敏感）。

### 2.3 页面盯梢的解析优先级与 SSRF 边界

`_WATCH_PAGE_RE` 只匹配 `https?://` 开头，放在 `_WATCH_RE`（文件盯梢）**之前**判定，
避免 URL 被误当文件路径。「有变化/更新/动静」解析时归一为 `watch=change`（非关键词），
修了最初把「变化告诉我」误判成关键词的坑。`is_safe_watch_url` 只放行 http(s)、拒
localhost/127/内网段/169.254 元数据地址——静态挡 SSRF，真正的抓取在组装根用
`_HttpClient`（合并信任链）执行并去脚本/样式、取纯文本前 20000 字。首抓只存基线
（`meta.baseline`）不出声，后续 `page_changed` 判定关键词或内容变化。

### 2.4 可发现性与否决策

`discoverability_line(user_text)` 按关键词映射最多一条「顺势帮忙」引导（累了→休息提醒、
怕忘→记忆、查信息→联网、附近→周边），注入当前问题的提示词。不做常驻推销——只在
用户话语命中且确实贴合时提示。

### 2.5 否决策

- 把记忆可见化做成独立定时推送（否决：会变成骚扰，应融进对话自然发生）；
- 给 Pet 加公开的数值 setter（否决：破坏 I3 写不变量，只读视图足够）；
- 用 `QWebEngineView` 渲染动态页再抓（否决：引入几十 MB 依赖，违背零新依赖底线，
  静态文本已覆盖物流/成绩等主要场景）；
- 让用户去命令面板发现能力（否决：20+ 命令没人翻，改成对话中顺势浮现）。

## 3. 测试与基线

- 新增 `test_companion_depth.py`（记忆可见化/情绪/作息/小脾气/可发现性/页面盯梢解析与
  SSRF），与相关回归合跑 63 passed；组装根+设置窗 UI 104 passed；单元全量 879 passed；
  改动文件 lints 0。

## 4. 验证方式

- 告诉它名字，下一句它应叫你的名字；聊到之前说过的事，它会自然提起一次；
- 心情低/熬夜时语气变软变轻；久不理且低落时它会闹小脾气（每天最多一次）；
- 说「盯着 https://… ，出现 已发货 告诉我」→ 页面出现关键词时用宠物口吻提示；
- 说「今天好累」→ 它顺势问要不要设休息提醒，而非硬塞功能菜单。
