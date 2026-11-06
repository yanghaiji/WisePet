# 桌面宠物 WisePet 技术设计 v1.14

- **对应需求**：[FR v1.13](../../FR/v1.13/desktop-pet-PRD-v1.13.md)
- **上一版本**：[v1.13](../v1.13/desktop-pet-tech-design-v1.13.md)
- **状态**：待评审

## 0. 定位

v1.13 把设置窗换成了贴纸卡片，但留了三处尾巴；尤其「不能移动」是**事件投递范围**问题，不是拖动算法问题（算法在 v1.13 已验证过）。本轮只动 `ui/settings_dialog.py` 的构造与样式表。

## 1. 落地

| 落点 | 做法 |
|------|------|
| 数值框 | `_STYLE` 增 `QSpinBox, QDoubleSpinBox { padding-right: 22px; }` 与 `::up-button` / `::down-button`（`subcontrol-origin: border`、`subcontrol-position: top\|bottom right`、`border: none`、`background: transparent`、`border-radius: 4px`、`margin`），hover 统一 `#F7E7CC`；`QDoubleSpinBox` 必须单独列选择器（它继承 `QAbstractSpinBox`，不吃 `QSpinBox` 的 QSS 规则） |
| 空状态 | `_status.hide()` 初始隐藏，`_show_status()` 里 setText + setStyleSheet + `show()`；删掉不再使用的 `_STATUS_IDLE` |
| 标题栏 | `head` 从裸 `QHBoxLayout` 改为 `QFrame#titleBar` 容器（`setMouseTracking(True)`），dot/title/close 的 parent 随之改；`root.addWidget(self._title_bar)` 取代 `root.addLayout(head)` |
| 过滤器 | 安装对象从 `(dot, title)` 扩为 `(card, self._title_bar, dot, title)`；`close` 按钮不装；`title_bar` 暴露为只读 property 供真实鼠标事件测试注入 |
| 初始位置 | 构造末尾按 `self._screen.geometry()` 算水平居中、`y1 + 40`，再经 `self._screen.clamp(w, h)` 落到合法区；`resize(470, 560)` |
| 可读性 | `QLabel` 默认色 `#9A7A55` → `#6B4F3A`（`QLabel#title` / `#sectionTitle` / `#dot` 等 ID 规则优先级更高，不受影响） |

## 2. 两条 Qt 事实

1. `QHBoxLayout` 不是 widget，标题栏空白处的鼠标事件落到父 `QFrame`（card）上；只给两个 `QLabel` 装 `eventFilter` 时，约九成可拖区域收不到 `MouseButtonPress`。事件过滤器必须装在**实际承载区域**的 widget 上。
2. 有 parent 的 `QDialog` 默认居中于 parent（本项目的宠物窗在右下角），初始位置可能已经超出 `bounds_for(w, h)`；此时任何拖动都会先被 `clamp` 拉回合法区，观感就是「拖不动 / 一拖就跳」。构造期显式 `move()` 会置 `Qt.WA_Moved`，之后 `exec()` 不再重新居中。

## 3. 否决方案

| 方案 | 否决理由 |
|------|----------|
| 重写 `QDialog.mousePressEvent/mouseMoveEvent` | 子控件会吃掉事件，标题栏空白与卡片边缘仍覆盖不到，治标不治本 |
| 给每个子控件都装过滤器 | 数量多易漏，且滚动区 / 输入框里也会误触发窗口拖动 |
| 直接隐藏 spinbox 上下按钮 | 失去鼠标微调能力，只能键盘输入；表单交互退化 |
| 用图片 / qrc 换箭头图形 | 新增素材依赖，与「零新增资源」和打包管线的既有约定相冲 |
| 去掉状态标签 | 保存 / 测试连接的结果仍需展示，按需 `show()` 才对 |
| 拖动时不做 clamp | 窗口会被拖出屏幕，违反规约 §2.6 |

## 4. 验证

- 全量 `972 passed`（新增 4 项），覆盖率 **99.22%**（2050 stmts / 16 miss），改动文件 lints 0。
- `tests/ui/test_settings_dialog.py::TestStickerStyle` 新增：`QApplication.sendEvent` 往 `title_bar` 发真实 `MouseButtonPress`/`MouseMove`/`MouseButtonRelease`（不再只调私有拖动方法）、空状态初始隐藏 / 出结果后显示、spinbox 按钮 QSS 断言、注入 `FakeScreenBounds` 后窗口完整落在工作区内。
- 既有 21 项设置窗测试与全仓组装根冒烟全部照常通过。
- 真机待目检：整条标题栏可拖且不出工作区、数值框箭头观感、空状态条消失，见 `AGENT.md` §4。
