# 桌面宠物 WisePet 技术设计 v1.32

- 版本：v1.32
- 状态：已实现（待评审）
- 日期：2026-11
- 对应需求：[FR v1.32](../../FR/v1.32/desktop-pet-PRD-v1.32.md)
- 性质：增量落地登记；零新增三方依赖（shiboken6 是 PySide6 自带）

## 1. 分层与落点

| 层 | 文件 | 改动 |
|---|---|---|
| ui | `input_dialog.py`（新） | `InputDialog`：贴纸风单行输入，密码模式、validator 卡片内报错、show_centered |
| ui | `profile_picker.py` | QInputDialog 全部换 InputDialog；新增「🗑 删除身份…」流程（名字 → PIN → 名字二次确认） |
| infra | `profile_store.py` | `remove(name, pin)`：PIN 验证 + `shutil.rmtree` + active 清理；默认身份不可删 |
| infra | `ai_stores.py`（新） | `SharedAIStore`（全局 shared_ai.json）/ `AIUsageStore`（每身份 ai_usage.json 按天计数） |
| main | `_relaunch_as` | `save_state → QProcess.startDetached(sys.executable, [--profile=X], ROOT) → 1.2s 后退场退出`；frozen/开发双形态 |
| main | `_effective_ai` | 「使用家庭共享 AI」→ 共享配置覆盖三件套 + embedding；未配置共享回落自己并告警 |
| main | `_ai_within_quota/_count_ai_request` | 每日配额判定与计数；超限当日提示一次 |
| main | `_sync_window` | `shiboken6.isValid(window)` 存活检查——C++ 对象删除后安全跳过并停运动计时器（生产健壮性 + 消除跨测试污染） |
| app | `chat_service.py` | `ai_allowed_provider`（AI 分支前检查，超限回落本地）+ `on_ai_request`（每轮计数） |
| domain | `config_model.py` | `AssistantSettings.use_family_ai / daily_ai_limit`；`MemorySettings.fact_ttl_days`；`search_max_results` 默认 200 |
| app | `long_term_memory.py` | TTL 注入化（`ttl_provider`，0=永不衰减）；提示词条数 8→12 |
| app | `knowledge_base.py` | top_k 默认 6、单片段 800 字符、总量 3200、注入提示带 agentic 深查引导 |
| ui | `settings_dialog.py` | 助理区加「家庭共享 AI」「每日 AI 上限」；collect/_load_from 同步 |

## 2. 关键实现决策

### 2.1 热切换 = 进程接力而非进程内重建
数据目录在装配期定死，进程内重建全部服务（托盘/热键/计时器/单例端口）风险不可控。
改用 `startDetached`：新进程绑新 profile 的守卫端口（与旧进程无冲突），旧进程保存状态
后优雅退场。用户看到的是宠物退场→新宠物进场——**体验上就是即时生效**。

### 2.2 共享 Key 不下发到孩子配置
`shared_ai.json` 存全局目录；`_effective_ai()` 在运行时合并——孩子身份的 config.json
里永远没有共享 Key，家长换 Key 一次生效，且共享配置的生命周期与任何身份解耦。

### 2.3 配额按「对话轮」计数而非 token
token 估算不可靠且用户看不懂；「每天 N 次 AI 对话」直观可控。超限回落**本地小脑**
（宠物照常说话），而不是冷冰冰地报错——体验与消费控制兼得。

### 2.4 shiboken 存活检查
跨测试污染暴露的真问题：C++ 对象（PetWindow）可能先于 Python 包装被销毁，
`sync_from_pet` 的 move 调用抛 RuntimeError。`isValid()` 检查 + 停运动计时器，
既修测试污染也是生产健壮性（系统销毁窗口同样不崩）。

## 3. 否决方案

| 方案 | 否决理由 |
|---|---|
| 进程内重建全部服务实现热切换 | 托盘/热键/计时器/守卫端口的重建竞态不可控；进程接力体验等价且简单可靠 |
| 把共享 AI Key 写进孩子 profile 配置 | Key 泄漏面扩大；运行时合并让孩子永远摸不到共享 Key |
| token 级计费配额 | 估算不可靠；次数配额直观且足够防过度消费 |
| 检索继续固定 top_k 小上限 | 用户反馈正确：预设上限等于替 AI 丢记忆；agentic 检索才是 AI 的做法 |
| AES 加密 profile 数据 | 标准库无 AES；引 cryptography 违背零依赖，做成可选增强另议 |

## 4. 验收

- 全量 **1503 passed / 0 failures / 0 errors**（v1.31 末 1502 + 新增 - 精简）；
  改动文件 lints 0；
- 修复一个测试基础设施级 bug：PetWindow C++ 对象销毁后 motion timer 触发的
  RuntimeError 曾污染 12 个无辜测试（shiboken 存活检查后消除）。

## 5. 真机待目检

- 「切换身份」后是否 1~2 秒内看到旧宠物退场、新宠物进场（无手动重启）；
- 家庭共享：家长改 Key 后孩子身份是否立刻生效（下次对话）；
- 孩子身份把每日上限设为 3 次，聊第 4 句是否回落本地小脑并只提示一次；
- 删除身份三重确认是否顺畅；默认身份是否不可删。
