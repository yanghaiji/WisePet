# 桌面宠物 WisePet 技术设计 v1.11

- **对应需求**：[FR v1.10](../../FR/v1.10/desktop-pet-PRD-v1.10.md)
- **上一版本**：[v1.10](../v1.10/desktop-pet-tech-design-v1.10.md)
- **状态**：待评审

## 0. 背景

C15 把「点宠物弹常驻大窗」拆成单行输入条 + 只读历史窗后，真机又暴露三个交互/观感问题；同时上一轮修复「重启不记得」「浮窗压宠物」只落了代码、漏了文档。本版本把 C16/C17 补登，并落地 C18（跟随）/ C19（历史窗继续聊）/ C20（气泡风格统一）。分层铁律、事件桥、按天仓储均无改动。

## 1. C16 / C17 补登：会话记忆恢复与侧向停靠

- `app/chat_history.py` 增 `recent_turns(max_days=7)`：读最近有记录日期的完整记录、按 `ts` 正序排序；只读，不发布事件、不重写盘。
- 组装根 `start()` 调 `_restore_chat_session()`：跳过非 `user`/`assistant` 与 `source=fallback`，逐条 `ChatSession.add_turn()`（自带 `max_turns` 截断），并把当天条数喂给输入条状态标签。
- `ui/screen_bounds.py` 增纯函数 `beside_position()`（左侧优先 / 右侧兜底 / 垂直居中 / clamp）与 `fit_beside_width()`（按较宽一侧收缩），`ScreenBounds.place_beside()` 复用前者；`PetWindow.window_rect()` 提供宠物全局矩形。
- `ChatInput.show_near()` / `ChatWindow.show_near()` 改收 `pet_rect` 而非 `QPoint` 锚点。

## 2. C18：位置变化驱动跟随，而不是轮询

| 落点 | 做法 |
|------|------|
| `PetWindow` | 新增 `moved = Signal()`；`sync_from_pet()` 用 `_last_synced` 比对 `(x, y)`，**只有真的变了**才 emit。拖拽（`mouseMoveEvent`）、1s tick、50ms 运动 tick 全都经过 `sync_from_pet()`，一个信号覆盖所有位移来源 |
| `PetWindow.window_rect()` | 改由 `controller.pet.position` + 窗口尺寸构造 `QRect`，不再取 `frameGeometry()`：domain 位置才是唯一事实，且不受窗口管理器/offscreen 延迟影响 |
| 组装根 | `_window.moved` → `_follow_chat_uis()`：取一次 `window_rect()`，分别调两个 UI 的 `follow_pet()` |
| `ChatInput.follow_pet()` | 把 `show_near()` 里的定位逻辑抽成 `_place()`；仅 `isVisible()` 时重算坐标，不 `show()` / 不 `raise_()` / 不改焦点 / 不重发 `active_changed` |
| `ChatWindow.follow_pet()` | 同样抽 `_place()`（含 `fit_beside_width` 收窄）；不 `reload()`，避免重读盘与丢滚动位置。`_pinned` 在 `_move_by()` 里置真，`show_near()` 重置——用户手动拖过的位置优先于自动跟随 |

### Qt 事实
`installEventFilter(self)` 之后 Qt 可能立刻派发 polish 等事件，此时 `__init__` 里后面的控件还没建好；`ChatWindow.eventFilter()` 用 `getattr(self, "_edit", None)` 取输入行，避免构造期 `AttributeError`（本次实测 116 个 fixture 全挂，已修）。

## 3. C19：历史窗输入行与入口互斥

- `ui/chat_window.py` 增 `submitted = Signal(str)`、`line_edit` / `send_button` 属性、`QLineEdit#chatEdit` + `QPushButton#send`（QSS 与 `ChatInput` 同款）；回车与点击都进 `_submit()`：空消息丢弃、发完清空并保持焦点。历史区 `_history` 仍 `setReadOnly(True)`。
- `eventFilter()` 对输入行单独处理 Esc → `hide()`，其余事件直接交回 `super`，**不进入标题栏拖动分支**（否则点输入框会把窗口拖走）。
- 组装根 `_wire_ui()` 把 `_chat_window.submitted` 与 `_chat_input.submitted` 一起接到 `send_message`；`_show_history()` 先 `_chat_input.hide()` 再 `toggle()`；`_show_chat_input()` 在历史窗可见时只调 `focus_input()`。
- `_chat_ui_active` 的字典合并判定保留：即使将来出现两个界面同时可见，也不会因关掉其中一个就误解冻。

## 4. C20：SpeechBubble 由手绘改 QSS 卡片

- 删除 `paintEvent()`、`QPainterPath`、`_tail_points()`、`_BUBBLE_RADIUS` / `_TAIL_HEIGHT`；新增模块级 `_STYLE` 与 `QFrame#bubbleCard`（`background:#FFFDF7` / `border:1px solid #DFC49A` / `border-radius:14px`），与 `inputCard` / `historyCard` 同源。
- 卡片内一行：橘点 `QLabel#bubbleDot` + 正文 `QLabel#bubbleText` + 可选 `QPushButton#ack`；正文仍 `setTextFormat(Qt.PlainText)`、`setMaximumWidth(320)`、wordWrap。
- `_ANCHOR_GAP = 12` 取代尾巴高度参与 `_place_and_show()` 的 Y 偏移；`adjustSize()` → `clamp()` → `move()` 链路不变，所以驻留时长、节流、`follow()`、`hide_bubble()` 一行未改行为。

## 5. 否决方案

| 方案 | 否决理由 |
|------|----------|
| 1s tick 里无条件重排浮窗 | 每秒一次无谓 `resize/move`，且会覆盖用户手动拖过的位置；改为「位置真变了」才发信号 |
| 让 `ChatWindow` 直接持有 `PetWindow` 并自己监听 | ui 组件互相耦合，坐标来源出现第二份真相；统一由组装根转发 |
| 跟随时顺手 `reload()` 历史 | 每次拖拽重读盘、清空滚动位置，且会把用户正在看的旧日期强行切回今天 |
| 把 `ChatInput` 整块塞进历史窗当输入行 | 两套布局/宽度/激活信号不同，会引入第二个 `active_changed` 源；输入行只是十几行 QSS + `_submit()` |
| 继续用 `QPainter` 手绘圆角 | 与另两个窗口的 QSS 配色各写一份，改一处仍会跑偏；统一 QSS 后颜色只有一处 |
| 用 `frameGeometry()` 取宠物矩形 | 窗口管理器 / offscreen 下可能滞后于 domain 位置；`pet.position` 才是真相 |

## 6. 验证

- 全量 `952 passed`（新增 26 项：跟随 9、历史窗继续聊 9、样式 1、PetWindow 信号 2、组装根 5），`fail_under = 90` 达成，**99.22%**（2050 stmts / 16 miss），lints 0。
- 新增/改写测试：`tests/ui/test_chat_window.py`（`submitted` / 发送按钮 / 空消息 / 输入行 Esc / 跟随 / `_pinned`）、`test_chat_input.py`（`follow_pet`）、`test_pet_window.py`（`moved` 只在位置变化时发、`window_rect` 取自 domain）、`test_speech_bubble.py`（`bubbleCard` 与统一配色）、`test_main_assembly.py`（入口互斥、历史窗提交、两界面跟随）。
- 未触达的 16 行仍是既有平台专属/防御分支，清单沿用 NFR v1.10 §5。真机观感（卡片圆角、气泡随宠物贴边）仍待人工目检，见 `AGENT.md` §4。
