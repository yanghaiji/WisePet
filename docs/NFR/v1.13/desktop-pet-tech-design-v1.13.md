# 桌面宠物 WisePet 技术设计 v1.13

- **对应需求**：[FR v1.12](../../FR/v1.12/desktop-pet-PRD-v1.12.md)
- **上一版本**：[v1.12](../v1.12/desktop-pet-tech-design-v1.12.md)
- **状态**：待评审

## 0. 定位

`ui/settings_dialog.py` 此前是 `QDialog` + 四个 `QGroupBox` + `QFormLayout` + 原生按钮，样式表为空；`_show_status()` 直接内联 `color:#2E7D32|#C62828`。视觉割裂但业务契约完整，因此本轮**只换外观层**，把 `collect()` / `_load_from()` / `_save()` / `_test_connection()` 原样保留。

## 1. 落地

| 落点 | 做法 |
|------|------|
| 窗体 | `Qt.FramelessWindowHint \| Qt.Dialog` + `WA_TranslucentBackground`；`QFrame#settingsCard` 圆角 16px；`setMinimumSize(420, 460)` / `resize(470, 600)` |
| 标题栏 | `QLabel#dot` + `QLabel#title` + `QPushButton#close`（`×` → `reject`）；标题区两个 label 装 `eventFilter`，`globalPosition` 计算拖动，`ScreenBounds.clamp()` 收进工作区（组装根 `open_settings()` 注入 `self._screen`） |
| 内容 | `QScrollArea`（`widgetResizable=True`、`NoFrame`）+ `QWidget#settingsContent`；四个 `QFrame#section` 由 `_section(parent, title)` 统一构造，返回 `(frame, QFormLayout)` |
| 样式 | 模块级 `_STYLE` 统一 QLineEdit / QPlainTextEdit / SpinBox / ComboBox / CheckBox（含 indicator）/ 滚动条 / 按钮；`QDialogButtonBox` 保留，Save 按钮 `setObjectName("saveBtn")` 并中文化文案 |
| 状态 | `_STATUS_IDLE` / `_STATUS_OK` / `_STATUS_ERROR` 三套内联胶囊样式（失败淡粉 `#F7E2E2` 与 `_BUBBLE_BG["fallback"]` 同源），`_show_status()` 只换样式串 + 文本 |

## 2. 兼容要点

1. **保留 `QDialogButtonBox`**：既有测试 `_click()` 用 `findChild(QDialogButtonBox)` + `box.button(Save|Cancel)`，集合与回调契约不能动；只改 objectName / 文案 / QSS。
2. **状态样式用内联串而非 Qt 属性选择器**：`_show_status()` 本来就调 `setStyleSheet()`，会盖掉控件所属窗口的 QSS；用 `[tone="ok"]` 属性选择器还得 `unpolish/polish`，行为不稳。三套常量最可控。
3. **`installEventFilter` 放在构造末尾**：v1.11 里踩过构造期 Qt 立刻派发 polish 事件、过滤器访问尚未创建的字段导致 116 个 fixture 全挂；这里拖动字段在 `__init__` 开头就初始化，且过滤器最后才装。关闭按钮**不装**过滤器，否则点击会被当成拖动起点。
4. **滚动区透明**：`QScrollArea` 与 `QWidget#settingsContent` 都设 `background:transparent`，否则卡片里会出现一块系统白底。
5. **Esc 仍可用**：`QDialog` 默认 Esc → `reject()`，frameless 不改变该行为；`accept()` 后 `result()` 仍是 `QDialog.Accepted`。

## 3. 否决方案

| 方案 | 否决理由 |
|------|----------|
| 只调色不改形态 | 系统标题栏、GroupBox 方框、方形原生按钮还在，割裂感来自形态而非单个颜色 |
| 完全自绘表单控件 | ui 门槛与冒烟成本高，QSS 已覆盖全部控件且可测 |
| 用属性选择器切状态色 | 需 `unpolish/polish`，与既有内联 `setStyleSheet` 冲突 |
| 去掉 `QDialogButtonBox` 换三个裸按钮 | 破坏 Save/Cancel 标准角色契约与既有测试，也丢失平台键盘默认键 |
| 拖动时自己取 `QGuiApplication.primaryScreen()` | 违反规约 §2.6「工作区几何由 `ui/screen_bounds.py` 提供」 |
| 去掉滚动区、窗口自适应内容高度 | 四组表单加卡片内边距后高度会超过小屏工作区 |

## 4. 验证

- 全量 `968 passed`（新增 5 项设置窗样式冒烟），覆盖率 **99.22%**（2050 stmts / 16 miss），改动文件 lints 0。
- `tests/ui/test_settings_dialog.py::TestStickerStyle`：无边框透明卡片与同源配色、自定义标题栏关闭、四个可滚动分区、按钮 objectName 与状态胶囊配色、标题栏拖动。
- 既有 16 项设置窗测试（api_key 不回显 / 留空不修改 / 区间校验 / 保存 / 取消 / 测试连接）全部照常通过；`tests/ui/test_main_assembly.py` 的组装根冒烟也未受影响。
- 真机待目检：圆角与配色、滚动区在小屏可用性、标题栏拖动不越界、模态关闭后宠物仍可交互，见 `AGENT.md` §4。
