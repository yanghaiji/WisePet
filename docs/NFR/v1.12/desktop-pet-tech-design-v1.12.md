# 桌面宠物 WisePet 技术设计 v1.12

- **对应需求**：[FR v1.11](../../FR/v1.11/desktop-pet-PRD-v1.11.md)
- **上一版本**：[v1.11](../v1.11/desktop-pet-tech-design-v1.11.md)
- **状态**：待评审

## 0. 缺陷定位

`main.py::toggle_visible()` 把 `PetWindow.setVisible(False)` 之后，唯一能唤出 `PetMenu` 的 `contextMenuEvent` 也随之消失；菜单里的「显示宠物」虽然文案正确，却没有任何可点击路径。全仓无 `QSystemTrayIcon`，`NFR v1.1` 里「QMenu 承担同等入口职责」的登记由此被证伪。修复只补一个常驻入口，不动隐藏逻辑本身。

## 1. 落地

| 落点 | 做法 |
|------|------|
| `ui/tray_icon.py`（新增） | `PetTrayIcon(QSystemTrayIcon)`：构造时 `setContextMenu(menu)` + `setToolTip` + `activated` 连接 + `show()`；`set_hidden_state()` 切 tooltip；`is_available()` 静态探测平台能力；三个公开常量 `TOOLTIP_VISIBLE` / `TOOLTIP_HIDDEN` / `HIDE_DISABLED_HINT` |
| 图标来源 | 组装根传 `self._animator.current_frame()`（当前皮肤 idle 帧 / 缺帧时的占位圆脸），`QIcon(QPixmap)` 直接可用，**零新增素材文件** |
| `main.py::__init__` | `setQuitOnLastWindowClosed(False)`；菜单构造完立刻建托盘并复用 `self._menu`；`tray_available` 可注入（默认 `PetTrayIcon.is_available()`），不可用时 `act_toggle.setEnabled(False)` + tooltip 说明 + warning 日志 |
| `main.py::toggle_visible()` | 在原有 `self._menu.set_hidden_state(hidden=visible)` 后补一行 `self._tray.set_hidden_state(hidden=visible)`；停帧、收起两个聊天 UI、`hide_bubble()`、恢复帧轮询全部照旧 |
| `main.py::shutdown()` | `self._tray.hide()`，退出时不留残影；`act_quit` 仍走 `app.quit()` |
| 激活原因 | 只响应 `Trigger` / `DoubleClick`；`Context`（右键）留给菜单，否则一次右键会既弹菜单又翻转显隐 |

## 2. 两条实测的 Qt 事实

1. offscreen 平台下 `QSystemTrayIcon.isSystemTrayAvailable()` 返回 **False**，但构造、`setContextMenu()`、`show()` 都不抛异常，`isVisible()` 甚至返回 **True**。因此**不能**用 `isVisible()` 判断平台是否真有托盘，降级判定只能用 `isSystemTrayAvailable()`。
2. `QApplication.quitOnLastWindowClosed` 默认是 **True**。宠物窗是唯一可见窗口，隐藏后必须显式关掉，否则事件循环可能在隐藏那一刻收场（衰减 / 提醒 / 落盘一起停）。

## 3. 否决方案

| 方案 | 否决理由 |
|------|----------|
| 隐藏时保留一个 1×1 透明窗口当右键靶子 | 仍是「看得见/可命中」的窗口，会挡住该处点击，且隐藏语义不干净 |
| 全局热键（如 Ctrl+Alt+W）召回 | Qt 没有跨平台全局热键，要引 Windows/macOS 平台 API，与规约 §2.6「平台差异收敛」相冲 |
| 隐藏后定时自动显示 | 不是「召回」，破坏隐藏语义，用户无法决定何时回来 |
| 给托盘再造一份独立菜单 | 两份菜单的文案 / 启用状态要互相同步，等于引入第二个状态真相；复用同一 `PetMenu` 实例最省 |
| 托盘不可用也照常允许隐藏 | 会把用户彻底锁死（正是本次报的缺陷）；宁可禁用隐藏并说明原因 |

## 4. 修订旧登记

`NFR v1.1` §「未实现清单：系统托盘图标 **未做**，`ui/tray_menu.py::PetMenu` 承担同等入口职责，可接受」——本版本实现托盘并证明该假设不成立，该条由本文件取代；按规约不回改 v1.1 原文。模块名 `tray_menu.py` 沿用不改（它确实仍是那份 QMenu）。

## 5. 验证

- 全量 `963 passed`（新增 11 项：托盘组件 6、组装根召回/降级 5），覆盖率 **99.22%**（2050 stmts / 16 miss），改动文件 lints 0。
- `tests/ui/test_tray_icon.py`：菜单复用、tooltip 双向切换、Trigger/DoubleClick 回调、Context 忽略、平台探测。
- `tests/ui/test_main_assembly.py::TestTrayRestore`：隐藏后托盘仍是恢复入口、从菜单「显示宠物」恢复（含帧轮询与文案复位）、双击图标恢复、托盘不可用时禁用隐藏、隐藏时 `quitOnLastWindowClosed` 为 False。
- 真机待目检：Windows 通知区 / macOS 菜单栏图标出现、隐藏后右键能看到「显示宠物」、双击图标可召回，见 `AGENT.md` §4。
