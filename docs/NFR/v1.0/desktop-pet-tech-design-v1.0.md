# 桌面宠物 AI Bot 技术方案

- **版本**：v1.0
- **状态**：待评审
- **对应需求**：docs/FR/v1.0/desktop-pet-PRD-v1.0.md（已确认）
- **技术栈**：Python ≥ 3.10 / PySide6 / httpx / pytest（+ pytest-qt）

---

## 1. 项目上下文分析

- 全新项目，仓库内仅有 `docs/` 与 `AGENT.md`，无历史代码约束 → **快速原型模式**：先交付 MVP 骨架，结构上为后续打包（PyInstaller）与扩展（素材包/多后端）预留边界。
- 规约约束落进设计：
  - 临时文件 → 项目内 `tmp/`（加入 .gitignore，用后即清）
  - 需求/技术文档 → `docs/FR`、`docs/NFR` 版本化管理
  - 实现思路/验证方式 → `AGENT.md`
  - **敏感数据例外**：api_key 存用户目录（`~/.ai-bot/config.json`）而非项目内，避免误提交（安全优先于便利，已在 PRD US-6 固化）
- 桌面应用非 Web 服务：无 HTTP API/数据库，"API 设计"体现为**模块间内部接口**，"数据模型"体现为**本地 JSON 存储结构**。

## 2. 架构总览

```
┌─────────────────────────────────────────────────┐
│ ui/          接口层（PySide6，唯一允许 import Qt 的层）│
│   PetWindow / SpeechBubble / ChatInputBox /     │
│   SettingsDialog / FrameAnimator / TrayMenu     │
├─────────────────────────────────────────────────┤
│ app/          应用层（编排，不 import Qt 控件类）      │
│   PetController / ChatService / ReminderService │
├─────────────────────────────────────────────────┤
│ domain/       领域层（纯 Python，零三方依赖，可测）    │
│   Pet 聚合 / 状态值对象 / 行为状态机 / ChatSession /  │
│   领域事件 / 仓储与网关端口(Protocol)               │
├─────────────────────────────────────────────────┤
│ infra/        基础设施层（实现领域端口）               │
│   JsonConfigRepo / JsonPetStateRepo /           │
│   OpenAICompatClient / FrameAssetLoader /       │
│   PlatformUtil / SingleInstance                 │
└─────────────────────────────────────────────────┘
依赖方向：ui → app → domain ← infra（domain 不依赖任何外层）
```

**Qt 隔离策略**：domain/app 层完全不依赖 PySide6，用 pytest 直接单测；UI 通过 Qt Signal/Slot 接收 app 层回调（app 层暴露纯 Python 回调接口，ui 适配器桥接为 Signal）。AI 流式请求在 `QThread` worker 中跑 httpx 同步流，经 Signal 回投主线程。

## 3. DDD 领域建模（L2 标准级）

### 3.1 限界上下文

| 上下文 | 类型 | 职责 | 关系 |
|--------|------|------|------|
| 宠物核心（Pet Core） | 核心域 | 宠物生命周期、六维状态、行为状态机、心情模型 | 被其他上下文依赖 |
| 对话（Conversation） | 核心域 | 会话历史、人设组装、流式应答编排 | 依赖宠物核心（状态注入 prompt）、依赖 AI 网关端口 |
| 提醒（Reminder） | 支撑域 | 喝水/久坐计时、勿扰顺延、播报请求 | 依赖宠物核心（借身体说话） |
| 配置（Config） | 通用域 | 持久化用户设置与状态快照 | 被上游依赖端口 |

上下文协作全部通过**领域事件 + 端口接口**，禁止跨上下文直接持有对象。

### 3.2 实体与值对象

```
聚合根：Pet
唯一标识：pet_id: str（默认 "mochi"）
属性：
  - name: str
  - vitals: VitalsVO          # 饥饿/心情/精力三值
  - behavior: BehaviorState   # 当前行为状态（枚举）
  - position: PositionVO      # 屏幕坐标（x, y）
  - facing: Direction         # 朝向（左/右）
  - last_updated: float       # 上次结算时间戳（epoch 秒）
生命周期：创建 → 待机 ⇄ 走动/反应/进食/思考/说话/睡眠（状态机驱动）

值对象：VitalsVO
属性：
  - hunger: int   # 饱食度 0-100（越高越饱）
  - mood: int     # 心情 0-100
  - energy: int   # 精力 0-100
不变量：所有分量钳制在 [0,100]；构造/更新后自动 clamp

值对象：PositionVO
属性：x: int, y: int
不变量：由调用方提供工作区边界做 clamp 校验（分辨率变化时重钳制）
```

### 3.3 聚合与不变量

```
聚合根：Pet
包含值对象：VitalsVO, PositionVO
包含领域逻辑：BehaviorStateMachine（Pet 的状态即行为形态，状态机作为聚合内部组件）
不变量：
  I1 行为切换必须经状态机白名单（如：拖拽中只能来自 待机/走动；思考中只能来自 待机）
  I2 拖拽/思考/说话期间禁止自主行为切换（走动计时器挂起）
  I3 vitals 更新只能经 decay()/feed()/pet_() 等行为方法，不允许裸赋值
  I4 时间衰减：hunger 线性衰减 -100/24h（默认），energy 夜间时段衰减加倍；
     离线结算按持久化时间戳补算，各分量有下限 FLOOR=10（不会饿死）
     系统时间回拨（now < last_updated）→ 本次不衰减也不恢复
```

```
聚合根：ChatSession
唯一标识：session_id
属性：
  - turns: list[Turn]         # (role, content)
  - max_turns: int = 20       # PRD US-3 上限
不变量：
  C1 append 后 len(turns) > max_turns*2（含 user/assistant 双角色）时截断最早轮
  C2 空文本/纯空白消息拒绝
  C3 system prompt = 模板 + 人设 name + 当前 vitals 摘要 + 时间上下文（由 ChatService 组装）
```

### 3.4 状态机（BehaviorStateMachine）

状态枚举：`IDLE, WALKING, DRAGGING, HURT, EATING, THINKING, SPEAKING, SLEEPING, SAD`

迁移白名单（与 PRD 状态图一致）：
```
IDLE     → WALKING | SLEEPING | HURT | DRAGGING | EATING | THINKING | SAD
WALKING  → IDLE | DRAGGING
DRAGGING → IDLE
HURT/EATING/THINKING → 由驱动方显式结束（动画播完/应答到达）→ IDLE
SPEAKING → IDLE
SLEEPING → IDLE
任意     → SAD（mood < SAD_THRESHOLD 时由 PetController 触发）
```
状态机只做合法性校验与迁移记录，**计时/触发时机由 app 层驱动**（domain 不持时钟、不持线程）。

### 3.5 领域事件

```
事件名：VitalsChangedEvent      触发：任一 vital 跨越阈值      携带：pet_id, vitals, changed, threshold_crossed  消费者：PetController（切 SAD / 主动求助）、UI（刷新状态条）
事件名：BehaviorChangedEvent    触发：状态机迁移成功           携带：pet_id, old, new                          消费者：FrameAnimator（切序列帧）
事件名：ChatReplyChunkEvent     触发：AI 流式增量到达          携带：text_delta, done                          消费者：SpeechBubble（增量渲染）
事件名：ChatErrorEvent          触发：请求失败/超时            携带：kind(auth/network/rate/timeout/server)     消费者：PetController（沮丧动画+兜底话术）
事件名：ReminderDueEvent        触发：提醒计时到且非勿扰        携带：kind(water/sedentary), text               消费者：PetController（移动+气泡）
事件名：HelpRequestEvent        触发：低状态主动求助（冷却30min）携带：reason(hunger/mood/energy)                消费者：SpeechBubble
```

事件机制：domain/app 层使用进程内同步 EventBus（轻量 pub-sub，约 40 行），ui 订阅后转投 Qt Signal 保证主线程更新。

### 3.6 端口（仓储/网关接口，`typing.Protocol`）

```
ConfigRepository：load() -> AppConfig ; save(cfg) ; path
PetStateRepository：load() -> PetSnapshot | None ; save(snapshot)
AIChatGateway：stream_chat(messages, api_cfg) -> Iterator[str]   # 逐 chunk yield 文本；抛 AIGatewayError(kind)
FullscreenDetector：is_user_fullscreen() -> bool                 # Windows ctypes 实现；macOS 返回 False（PRD：检测不到则不启用勿扰）
Clock：now() -> float ; monotonic() -> float                     # 便于测试注入假时钟
```

## 4. 分层设计与文件清单

```
ai-bot/
├── main.py                      # 组装根：构造 infra → app → ui，启动 QApplication
├── requirements.txt             # PySide6, httpx；dev: pytest, pytest-qt, respx
├── run.ps1 / run.sh             # 一键运行脚本（建 venv → pip install → python main.py）
├── assets/pet/<action>/*.png    # 序列帧素材（7 组动作）；缺失时 PlaceholderRenderer 程序绘制
├── config/                      # 应用默认配置（非敏感），人设 prompt 模板
│   └── default.yaml             # 或使用内置常量，见 §6
├── src/ai_bot/
│   ├── domain/                  # 纯 Python，零依赖
│   │   ├── model/pet.py  vitals.py  position.py  chat_session.py
│   │   ├── state_machine.py
│   │   ├── events.py
│   │   └── ports/               # Protocol 定义
│   ├── app/
│   │   ├── pet_controller.py    # 驱动状态机、自主行为随机调度、投喂/抚摸、拖拽落位钳制、事件分发
│   │   ├── chat_service.py      # 组装 C3 prompt、调 gateway、截断 C1、错误映射、请求排队（串行）
│   │   ├── reminder_service.py  # 计时、勿扰判定、合并提醒
│   │   └── event_bus.py         # 进程内 EventBus + 领域事件 dataclass
│   ├── infra/
│   │   ├── config_repo.py       # ~/.ai-bot/config.json（UTF-8, 原子写, api_key 脱敏工具 mask_key）
│   │   ├── pet_state_repo.py    # ~/.ai-bot/pet_state.json
│   │   ├── openai_client.py     # httpx 流式 POST /chat/completions；30s 超时；401/429/5xx → AIGatewayError
│   │   ├── frame_loader.py      # assets 发现帧序列；缺失 → placeholder
│   │   ├── platform_util.py     # 工作区几何(Win/mac 统一经 QScreen 由 ui 层传入)、全屏检测、用户目录
│   │   └── single_instance.py   # QLocalServer/QLocalSocket 互斥
│   └── ui/
│       ├── pet_window.py        # 无边框/透明/置顶窗口；mouse 事件→app 命令；paint 帧
│       ├── frame_animator.py    # QTimer 10~15FPS 驱动当前动作帧序列
│       ├── speech_bubble.py     # 圆角气泡、逐段文本、自动消失、"知道啦"按钮
│       ├── chat_input.py        # 点击宠物弹出的输入条（单行，回车发送，ESC 关闭）
│       ├── settings_dialog.py   # AI 配置/宠物参数/人设 + 测试连接按钮
│       └── tray_menu.py         # 右键菜单：设置/投喂/抚摸/隐藏/退出
├── tests/
│   ├── unit/                    # domain + app（pytest，注入假时钟/假 gateway/内存仓储）
│   ├── integration/             # infra：config/state 读写、openai_client(respx mock)、frame_loader
│   └── ui/                      # pytest-qt（offscreen 平台）冒烟：窗口构建/点击发信号/动画器帧循环
└── tmp/                         # 规约§1：临时下载目录（.gitignore，用完即清）
```

## 5. 内部接口设计（模块间契约）

桌面应用无外部 HTTP API，关键内部接口签名如下（均为 domain/app 纯 Python 接口）：

```python
# PetController（app 层门面，ui 只依赖它）
class PetController:
    def on_press(x, y) / on_drag(dx, dy) / on_release()      # 拖拽命令
    def on_click()                                            # 戳一下 → HURT
    def feed() / pet_head()                                   # 投喂/抚摸
    def request_chat(text) -> None                            # 委托 ChatService，排队串行
    def speak(text, source) -> None                           # 气泡播报（提醒/求助/兜底共用）
    def tick(now) -> None                                     # 周期时钟回调：衰减结算+自主行为

# ChatService
class ChatService:
    def send(text: str) -> None      # 入队；worker 线程消费；结果经 EventBus 发布
    def cancel_inflight() -> None    # 超时/退出取消

# ReminderService
class ReminderService:
    def start() / stop() / set_enabled(kind, on) / set_interval(kind, minutes≥5)
    def notify_ack(kind) -> None     # "知道啦"

# AIChatGateway（infra 实现）
class AIChatGateway:
    def stream_chat(messages: list[dict], cfg: AIConfig) -> Iterator[str]
    # 异常：AIGatewayError(kind ∈ {auth, network, rate, timeout, server}, detail)
```

## 6. 数据模型（本地 JSON 存储）

`~/.ai-bot/config.json`（敏感，用户目录，非项目内）：
```json
{
  "ai": {"base_url": "https://api.deepseek.com/v1", "api_key": "sk-…", "model": "deepseek-chat", "temperature": 0.8, "timeout_s": 30},
  "persona": {"name": "小墨", "system_prompt": "你是一只名叫{name}的桌面宠物…当前状态：{vitals_summary}"},
  "pet": {"walk_speed": 60, "idle_min_s": 5, "idle_max_s": 15, "max_turns": 20},
  "vitals": {"hunger_decay_per_hour": 4.2, "energy_night_factor": 2.0, "night_hours": [23, 7], "floor": 10, "sad_threshold": 25, "help_cooldown_min": 30},
  "reminders": {"water": {"enabled": true, "interval_min": 45}, "sedentary": {"enabled": true, "interval_min": 60}, "snooze_min": 5}
}
```
损坏处理：解析失败 → 文件改名 `config.json.bak-{ts}` 并写默认值（PRD M5 异常项）。

`~/.ai-bot/pet_state.json`（状态快照，退出/周期落盘）：
```json
{"pet_id": "mochi", "vitals": {"hunger": 72, "mood": 85, "energy": 60}, "position": {"x": 1200, "y": 980}, "last_updated": 1730000000.0}
```

无数据库 → 不涉 DDL；两文件均原子写（写临时文件 + `os.replace`），临时文件位于同目录。

## 7. 关键技术决策与风险

| # | 决策/风险 | 结论 | 理由 |
|---|-----------|------|------|
| D1 | AI 调用线程模型 | QThread worker + httpx **同步**流式 | 比 asyncio 桥接 Qt 简单可靠；PRD 要求串行队列，单 worker 天然满足 |
| D2 | domain 层无 Qt | 强制：仅 ui/ 可 import PySide6 | 保证 70%+ 覆盖率可用纯 pytest 达成，CI 无需显示环境 |
| D3 | 序列帧驱动 | QTimer 12FPS + QPixmap 缓存字典 | 满足 CPU<3%；素材缺失 → 程序绘制圆脸占位（US-1 降级） |
| D4 | 跨屏坐标 | 一律用 `virtualGeometry` 并每 tick 重取屏幕几何 | 分辨率变化/插拔显示器自适应（PRD 边界项） |
| D5 | 全屏勿扰 | Windows: ctypes GetForegroundWindow+GetWindowRect vs 工作区；macOS: 恒 False | PRD 已接受降级 |
| D6 | api_key 存储 | 明文存用户目录 JSON + 日志/UI 脱敏 mask | 桌面应用无系统级密钥库可用；文件权限 macOS/Win 设 0600/ACL 尽力而为；风险已在 PRD 声明接受 |
| R1 | macOS 透明置顶窗口在某些版本被 Dock 遮挡 | ui 层 `Qt.WindowStaysOnTopHint|FramelessWindowHint`；验证清单列入手测项 | 中 |
| R2 | PySide6 在 Apple Silicon 的 wheel 可用性 | requirements 固定 `PySide6>=6.6`，该版本起提供 universal2/arm64 wheel | 低 |
| R3 | 流式气泡频繁 repaint | chunk 聚合节流 ≥80ms 再刷新 | 低 |

## 8. 测试策略

| 层 | 框架 | 范围 | Mock 依赖 |
|----|------|------|-----------|
| domain | pytest + hypothesis 可选 | 值对象钳制、状态机白名单全迁移矩阵、衰减公式（注入假时钟，含时间回拨）、会话截断 C1/C2 | Clock、内存仓储 |
| app | pytest | PetController 编排（拖拽冻结自主行为、事件发布）、ChatService 排队/错误映射、ReminderService 勿扰顺延/合并 | FakeGateway、EventBus 捕获 |
| infra | pytest + respx/tmp_path | config/state 读写与损坏恢复、openai_client 流式解析与 401/429/超时分类、frame_loader 缺素材降级 | respx mock HTTP、tmp_path 文件 |
| ui | pytest-qt（`QT_QPA_PLATFORM=offscreen`） | 冒烟：窗口属性(边框/置顶/透明)、mousePress→controller 调用、气泡增量文本、动画器循环 | controller 用 fake |

命名：`should_[行为]_when_[条件]`；覆盖率目标 domain ≥80%、app ≥70%、infra 关键路径 100%、ui 冒烟。
**验证方式**：`python -m pytest`（自动 `QT_QPA_PLATFORM=offscreen`）+ 双平台手工清单（动效/拖拽/聊天/提醒各 1 页 checklist，记录于 AGENT.md）。

## 9. 开发任务拆解（按依赖顺序）

| Step | 任务 | 产出 |
|------|------|------|
| 1 | 项目骨架：目录、requirements、run 脚本、pytest 配置、.gitignore（含 tmp/、assets 不忽略） | 可 `python main.py` 起空窗 |
| 2 | domain：值对象/Pet/状态机/ChatSession/事件/端口 + 单测 | RED→GREEN 全绿 |
| 3 | infra：config/state 仓储、openai_client、frame_loader、single_instance + 集成测试 | 端口契约验证通过 |
| 4 | app：PetController/ChatService/ReminderService/event_bus + 单测 | 编排逻辑验证通过 |
| 5 | ui：PetWindow/FrameAnimator/SpeechBubble/ChatInput/SettingsDialog/TrayMenu + 冒烟测试 | MVP 功能全通 |
| 6 | 集成联调：main.py 组装根、占位素材、端到端手测清单 | 验收 PRD US-1~US-6 |
| 7 | 收尾：Phase 4 审查清单、AGENT.md 记录实现思路与验证方式 | 交付报告 |
