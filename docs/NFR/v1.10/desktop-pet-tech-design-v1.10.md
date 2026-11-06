# 桌面宠物 WisePet 技术设计 v1.10

- **对应需求**：[FR v1.9](../../FR/v1.9/desktop-pet-PRD-v1.9.md)
- **上一版本**：[v1.9](../v1.9/desktop-pet-tech-design-v1.9.md)
- **状态**：待评审
- **变更性质**：增量修订（仅描述相对 NFR v1.9 的变化）

## 1. 入口拆分（组装根）

| 触发 | 组件 | 行为 |
|---|---|---|
| `PetWindow.clicked` | `ChatInput` | `show_near()` 单行输入条；`submitted → send_message` |
| `PetMenu.act_history` | `ChatWindow` | `toggle()` 只读历史窗 |

`main.py` 恢复 `ChatInput` 实例与 `ChatInput` 导入；`ChatWindow.submitted` / `line_edit` / `_submit` 一并删除——历史窗不再具备发消息能力，避免两个入口同时往 `ChatService` 塞包。

`PetMenu.__init__` 新增可选 `on_history`，`act_history` 在设置/投喂/抚摸之后单独成组（两侧加分隔线）；未注入回调时该动作 `setEnabled(False)`，与既有 `on_reload` 的兜底写法一致。菜单本身加圆角暖色 QSS，不改任何动作语义。

## 2. 状态只有一个真相

v1.9 的误差在于把状态塞进了"会被藏起来的大窗"。本版把它上收为组装根的一个方法：

```python
def _set_chat_state(self, phase, source="", detail=""):
    self._chat_input.set_state(phase, source, detail)   # 聊天时一定看得见
    self._chat_window.set_state(phase, source, detail)  # 打开时也能看
```

`_on_chat_state` / `_on_chat_error` / `start()` / `apply_config()` 四处统一改调它，不再各自 `set_state`。两个 UI 只是渲染器，不持有状态权威；`_PHASE_TEXT` / `_BRAIN_TEXT` 在两个模块各留一份纯展示映射（与 `ChatService` 的 `source=` 判据同源），不引入共享状态对象，避免为一行文案多一层依赖。

`ChatInput` 顶部新增 `QLabel#status` 显示 `● 阶段 · 大脑`，错误时附 `detail`。

## 3. 会话激活的合并口径

v1.9 用 `chat_window.active_changed → controller.set_conversation_active(bool)` 单线连接；拆成两个界面后，**任何一个关闭都会误报 False 解冻**。改为组装根记录两个布尔：

```python
self._chat_ui_active = {"input": False, "history": False}
def _on_chat_ui_active(self, which, active):
    self._chat_ui_active[which] = active
    self._controller.set_conversation_active(any(self._chat_ui_active.values()))
```

两个 `active_changed` 各带自己的 key 连进来。`ChatInput` 新增 `active_changed`（`show_near` 发 True、`hideEvent` 发 False，带去重），`ChatWindow` 保留既有信号。`toggle_visible()` 隐藏宠物时两个都 `hide()`，因此不会残留"永久冻结"。

`PetController` 的 `_frozen` / `CONVERSATION_STATES` 逻辑完全未动（C14 不变）。

## 4. 历史窗重绘

`ChatWindow` 从"系统标题栏 + QLineEdit + QPushButton + QTextBrowser 纵排"改为无边框贴纸风卡片：

- `Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool` + `WA_TranslucentBackground` + `setFixedSize(440, 500)`；`QFrame#historyCard` 用 QSS 画圆角与描边，父窗口透明让圆角可见。
- 自定义标题栏：`●` 装饰点 + 标题 + `QComboBox` 日期下拉 + `×` 关闭按钮；底部一行注明保存路径。
- 消息不再是一行纯文本，改为 `_bubble_html(record)` 生成「元信息行 + 带底色的 table 气泡」：用户 `#F8D79B` 右对齐、助手 `#FFFFFF` 左对齐、兜底 `#F7E2E2`；`html.escape` + `\n → <br>` 保证纯文本语义（Qt 富文本对 `border-radius` 支持有限，故用 `bgcolor` + `border` 的兼容写法，不引 QWebEngineView）。
- 空态用 `setHtml` 写引导文案；`append_record` 首次调用时清掉空态（`_empty_hint` 标志），否则提示会留在第一条消息上方。
- `receive_record(record)`：仅当 `session_day == day_key()` 才实时上屏，避免停在旧日期时把今天的新消息插进旧视图；切回当天由 `reload()` 从仓储读全量，不丢消息。构造时先把日期下拉初始化为今天，保证 `start()` 之前也能正确判定。
- 无边框窗口自己实现拖动：`card` / `dot` / `title` 装 `eventFilter`，用 **globalPosition** 记录按下点与窗口原点（子控件事件给的是局部坐标，用局部坐标会导致跳变），`_move_by` 经 `ScreenBounds.clamp` 限制在工作区。日期下拉与关闭按钮不装过滤器，点击不受影响。`QShortcut(Escape)` + `keyPressEvent` 双通道关闭。

### 否决过的方案
1. **保留大窗只改样式**：用户明确要求"不希望直接展示出来"，形态本身被否，调色救不了。否决。
2. **大窗既聊天又看历史**：等于没拆入口，点宠物仍弹大窗。否决。
3. **把会话状态只留在历史窗**：大窗默认隐藏 = 状态不可见，回到 v1.8 之前的坑。否决。
4. **QListWidget + 自定义 item widget 做气泡**：视觉上限更高，但 ui 层不入门槛、冒烟测试成本大，且富文本气泡已满足"纯文本 + 转义"约束。否决（本期）。
5. **用 `event.position()` 拖动**：事件过滤器收到的是子控件局部坐标，会造成窗口跳变；改用 `event.globalPosition()`。

## 5. 测试与基线

| 文件 | 覆盖 |
|---|---|
| `tests/ui/test_chat_input.py` | 状态标签、错误详情、回空闲、激活信号、圆角样式 |
| `tests/ui/test_chat_window.py` | 只读（无 `submitted`/`line_edit`）、无边框卡片、关闭按钮、气泡配色与 HTML 转义、空态清理、跨日期隔离、按天切换、开关信号、Esc、边界钳制 |
| `tests/ui/test_main_assembly.py` | 点宠物只开输入条、启动不自动弹历史、右键菜单入口、重复点击收起、两界面任一开着都冻结、输入条提交连通 ChatService、隐藏宠物收起两个界面；会话生命周期用例补输入条状态断言 |

两处口径修正：`Qt` 富文本把颜色规范化为小写、并省略默认的 `align="left"`，故颜色断言用 `.lower()`、对齐断言改打在 `_bubble_html` 源串上（渲染后只验 `align="right"`）。

基线：**926 项全绿**，覆盖率 **99.22%**（TOTAL 2045 stmts / 16 miss，阈值 90%），lints/ruff/mypy 0。`ui/` 依既有约定在覆盖率中 omit。

## 6. 范围外

- 历史窗搜索 / 删除 / 导出、深色主题、气泡真实圆角（需自绘 item widget 或 QWebEngineView，本期不做）。
- 按天存储格式与会话事件契约未改，无需数据迁移。
