# 桌面宠物 WisePet 技术设计 v1.19

- **对应需求**：[FR v1.18](../../FR/v1.18/desktop-pet-PRD-v1.18.md)
- **上一版本**：[v1.18](../v1.18/desktop-pet-tech-design-v1.18.md)
- **状态**：已实现（补登，待评审）

## 0. 定位

两项都遵循既有分层：**配置进 domain、交互进 ui、装配进 `main.py` 组装根**，ui 不直接读仓储，配置经 provider 回调注入。零改动状态机、事件契约、数值语义。

## 1. C28 聊天输入条空闲自动隐藏

| 落点 | 做法 |
|------|------|
| `domain/config_model.py::ChatSettings` | 新增 `idle_hide_s: float = 30.0`；`from_dict` 用 `max(0.0, _num(...))` 解析——**0 表示不隐藏**，非法值回默认 |
| `ui/chat_input.py` | 单个 `QTimer`（`setSingleShot(True)`）+ 注入的 `idle_hide_provider` 回调；`_restart_idle_timer()` 读 `provider()` 秒数：`provider is None` / 不可见 / `seconds<=0` / 取值异常都 `stop()`，否则 `start(ms)` |
| 计时重置点 | `show_near()`（打开）、`_edit.textChanged`（输入）、`_submit()`（发送）、`set_state()`（阶段变化）四处各调一次 `_restart_idle_timer()`，对齐 R2 |
| `hideEvent()` | 收起即 `stop()` 定时器并 `_emit_active(False)`——自动隐藏与手动关闭**同一条路径**，因此天然复用 v1.10 的会话冻结解冻（R4） |
| `main.py` | 构造 `ChatInput(..., idle_hide_provider=lambda: self._config.chat.idle_hide_s)`，保存配置后无需重启即生效（provider 每次实时读） |
| `ui/settings_dialog.py` | 「宠物参数」分区 `_chat_idle_hide = _int_spin(0, 600, 30, " s")`，纳入 `value_widgets`（滚轮只滚页面、可键入）与 `_load_from`/`collect` 往返 |

## 2. C29 截图问小墨（区域框选）

| 落点 | 做法 |
|------|------|
| `ui/region_selector.py::RegionSelector` | 全屏 `QWidget`：`FramelessWindowHint \| WindowStaysOnTopHint \| Tool` + `WA_TranslucentBackground`，盖 `primaryScreen().geometry()`；`paintEvent` 先 `drawPixmap` 铺快照（"冻住"），再用**四块矩形 fillRect** 拼出选区外压暗（不做位图 alpha 运算，代价最低），选区边框 `QColor(232,164,92)` 主题橘 |
| 交互 | `mousePress/Move/Release` 维护 `_origin`/`_current` 橡皮筋；`mouseRelease` 归一化后 **宽高任一 < `_MIN_SELECTION=8` 视为误点**（`_finish(None)`）；`keyPress` Esc → `_finish(None)` |
| 结果信号 | `selected(QRect)` / `cancelled()`；`_finish` 先 `close()` 再回调 `on_done`（恢复宠物窗），最后发信号 |
| `main._screenshot_ask` | 隐藏宠物窗与气泡 + `processEvents()`（避免把猫截进快照）→ `screen.grabWindow(0)` 抓主屏快照 → 用 `QEventLoop` 阻塞等框选 → `snapshot.copy(rect)` 裁剪 → `attachment_from_clipboard_image(cropped.toImage())` 转附件 → 打开/复用历史窗 `add_attachment` + `focus_input` |
| 快照失败降级 | `snapshot is None/isNull()` 或 `att is None` → `controller.speak("截屏失败了……再试一次？")`，不抛异常 |
| `ui/tray_menu.py` | 新增 `on_screenshot` 回调与「截图问小墨」`act_screenshot`，`main` 接 `self._screenshot_ask` |
| 复用附件通道 | 走 `domain/attachment.py` 既有 `attachment_from_clipboard_image`（5MB 上限）与 `ChatWindow.add_attachment`，**不新增消息结构**（R4）；只覆盖主屏（R5，见 `_screenshot_ask` docstring） |

## 3. 否决方案

| 方案 | 否决理由 |
|------|----------|
| 空闲隐藏用全局轮询 `tick` 判断时间戳 | 输入条是独立顶层窗，`QTimer` singleShot 更贴合「有交互就重开」语义，且关闭即停表、无常驻开销 |
| 框选用系统截图工具 / 全屏截后裁剪 | 破坏「冻结画面内直接圈选」的一体化体验，且引入外部进程依赖 |
| 选区压暗用 QGraphicsOpacityMask / 位图运算 | 代价高；四矩形 fillRect 拼洞视觉等价、实现更简单 |
| 截图独立成一套消息类型 | 与 Ctrl+V 贴图重复；复用附件通道即可，数据结构不变 |
| 抓全部显示器 | 多屏坐标合成复杂，先按主屏落地，副屏列入范围外 |

## 4. 验证

- 单测覆盖：`ChatSettings.idle_hide_s` 解析（默认/0/非法回退）、`chat_input` 空闲计时重置与 `0=不隐藏`、`region_selector` 选区归一与 <8px 丢弃、`tray_menu` 截图入口、设置窗往返。
- 全量套件通过，覆盖率维持门槛（≥90%）；改动文件 lints 0。
- 真机待目检：输入条晾 30s 自动收起且宠物恢复活动；托盘「截图问小墨」能框选出图并落到聊天输入，见 `AGENT.md`。
