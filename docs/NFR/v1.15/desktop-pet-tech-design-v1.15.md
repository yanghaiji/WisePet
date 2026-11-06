# 桌面宠物 WisePet 技术设计 v1.15

- **对应需求**：[FR v1.14](../../FR/v1.14/desktop-pet-PRD-v1.14.md)
- **上一版本**：[v1.14](../v1.14/desktop-pet-tech-design-v1.14.md)
- **状态**：待评审

## 0. 定位

`QAbstractSpinBox` 与 `QComboBox` 的 `wheelEvent()` 默认会步进当前值 / 切换选项。设置窗有 `QScrollArea`，于是「想滚页面」变成「偷偷改配置」。改动只在 `ui/settings_dialog.py`，业务与 QSS 一行未动。

## 1. 落地

| 落点 | 做法 |
|------|------|
| `_forward_wheel(self, event)` | 模块级小函数：`QApplication.sendEvent(self.wheel_target, event)` 把滚轮转交滚动区视口，再 `event.accept()` 阻止 Qt 继续向父级冒泡（避免滚两格） |
| 三个子类 | `_NoWheelSpinBox` / `_NoWheelDoubleSpinBox` / `_NoWheelComboBox` 各自 `wheelEvent = _forward_wheel`、类属性 `wheel_target: QWidget \| None = None`；不引入多继承 mixin，避免 PySide6 元类冲突 |
| 替换构造 | `_int_spin()` 与 AI / 宠物两组里的 `QDoubleSpinBox`、`_build_pet_group()` 里的 `QComboBox` 全部换成上述子类；返回类型仍是 `QSpinBox` 等基类，既有 `.value()` / `.setValue()` / `.minimum()` 契约不变 |
| 注入目标 | `__init__` 末尾给 9 个控件赋 `widget.wheel_target = self._scroll.viewport()`；控件自己不持有 `SettingsDialog`，分层不变 |

`QDoubleSpinBox` 继承 `QAbstractSpinBox` 而非 `QSpinBox`，所以子类、QSS 选择器都必须单列（与 v1.13 的箭头样式同一坑）。

## 2. 实测的 Qt 事实

**只 `event.ignore()` 不够**：先按「忽略滚轮让 Qt 冒泡给 `QScrollArea`」实现，值确实不再改，但 `should_scroll_page_instead_of_changing_value` 仍红——滚动条 value 保持 0，事件没有进到 `QAbstractScrollArea` 的 viewport 处理链。改成显式 `sendEvent(viewport, event)` 后立即转绿。这与 v1.14 里「裸 layout 不承载事件」是同一类教训：不能假设 Qt 会替你把事件送到目标组件。

## 3. 否决方案

| 方案 | 否决理由 |
|------|----------|
| 子类里只 `event.ignore()` | 实测页面不滚（见 §2） |
| `eventFilter` 返回 True 直接吞掉滚轮 | 鼠标停在输入框上滚轮完全失效，比改值更难受 |
| 手动 `verticalScrollBar().setValue(...)` | 要自己定步长 / 方向 / 平台手感，重复实现 `QAbstractScrollArea` 已有逻辑 |
| 给所有控件装过滤器统一拦截 | 数量多易漏，且会误伤 `QPlainTextEdit` 的文本滚动 |
| 连 `QPlainTextEdit` 一起禁滚轮 | 它滚轮只移动视图、不改变内容，且长 System Prompt 需要能滚 |
| 改 `QApplication` 全局滚轮策略 | 影响聊天窗 / 历史窗，超出本次范围 |

## 4. 验证

- 全量 `974 passed`（新增 2 项），覆盖率 **99.22%**（2050 stmts / 16 miss），改动文件 lints 0。
- `tests/ui/test_settings_dialog.py::TestWheelGuard` 用真实 `QWheelEvent`（`QApplication.sendEvent`）扫过 8 个数值框 + 皮肤下拉，断言值 / 选中项前后一致；另一项断言滚轮后 `verticalScrollBar().value() > 0`。
- 既有 25 项设置窗测试与全仓组装根冒烟全部照常通过。
- 真机待目检：滚轮扫过输入框不改值、页面能滚、键盘 / 微调按钮仍可改值，见 `AGENT.md` §4。
