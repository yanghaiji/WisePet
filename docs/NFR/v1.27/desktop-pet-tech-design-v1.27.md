# 桌面宠物 WisePet 技术设计 v1.27

- 版本：v1.27
- 状态：已实现（待评审）
- 日期：2026-11
- 对应需求：[FR v1.26](../../FR/v1.26/desktop-pet-PRD-v1.26.md)（C43 / C44 / C45）
- 性质：**补登 + 落地登记**，不回改历史版本；覆盖 v1.26 之后三轮已落代码未登记的改动

## 1. C43 macOS 签名公证链路

| 文件 | 改动 |
|---|---|
| `tools/package.py` | 新增 `sign_dmg(dmg, dry_run)`（对 hdiutil 产物加 `--options runtime` 签名，否则公证必被拒）与 `notarize_dmg(dmg, dry_run)`；macOS 分支改为 `sign_app → dmg_installer → sign_dmg → notarize_dmg` |
| `tools/package.py` | `notarize_dmg` 三重门控：`WISEPET_NOTARIZE == "1"` 才进、`APPLE_ID/APPLE_APP_PASSWORD/APPLE_TEAM_ID` 齐全才调 `xcrun`、`dry_run` 下只打印说明绝不回显明文密码；凭据全部走 `os.environ`，不落盘 |
| `.github/workflows/build.yml` | 打包步骤注入 6 个 secrets（`WISEPET_CODESIGN_IDENTITY`/`WISEPET_NOTARIZE`/`APPLE_ID`/`APPLE_APP_PASSWORD`/`APPLE_TEAM_ID`）；新增「导入 macOS 证书」step（base64 解码 → 临时 keychain → `security import` → `set-key-partition-list`） |

**CI 语法修复（用户真机推送时报错）**：证书 step 原写成
`if: matrix.target == 'macos' && secrets.MACOS_CERTIFICATE != ''`，GitHub Actions 的 `if:`
**不允许引用 `secrets` context**（只允许 `github/needs/strategy/matrix/job/runner/env/vars/steps/inputs`），
报 `Invalid workflow file ... Unrecognized named-value: 'secrets'`，导致整个 workflow 解析失败。
改为 `if: matrix.target == 'macos'`，把「有没有证书」挪进 shell 用 `-z "$MACOS_CERTIFICATE"`
判断并 `exit 0` 优雅跳过。同段另修一个 macOS 专属隐患：runner 自带 BSD `base64` 不支持 GNU 的
`--decode`，改用 `python -c "base64.b64decode(os.environ['MACOS_CERTIFICATE'])"` 解码。
新增 `tests/unit/test_workflow_expressions.py`（7 项纯标准库静态护栏）：`if:` 不得出现
`secrets.`、只允许合法 context 前缀、`${{ }}` 配对、step 缩进一致、证书 step 的 shell 兜底、
禁用 `base64 --decode`。本机无 actionlint/PyYAML/Ruby，只能做静态校验，真 YAML 解析仍以 GitHub 为准。
| `tests/unit/test_package_macos.py`（新，5 项） | 双重开关判定、凭据缺失降级、`sign_dmg` 身份门控；`run()` 用 monkeypatch 记录调用，不真正执行 `codesign/xcrun` |

**降级语义**：任一凭据缺失即跳过对应步骤，产出未签名 dmg；CI 双平台矩阵不中断。
本机 Windows 用 `python tools/package.py --target macos --dry-run` 验证命令拼接正确
（未配身份时 codesign/notarize 命令均不出现）。

## 2. C44 登场特效体系落点

| 文件 | 改动 |
|---|---|
| `ui/tech_effects.py` | 单一 `spawn` 扩成 7 种 `ENTER_KINDS`；`TRIGGER_ENTER_KIND` 映射 boot/chat/scene/celebrate；`random_enter_kind(exclude=())`；`TechEffect(center, mode=...)` 保留为 kind 别名兼容旧调用 |
| `domain/config_model.py` | `PetParams.enter_effect`（默认 auto）+ `_choice` 白名单 9 值（`auto/random` + 7 kind），非法回落 auto |
| `ui/settings_dialog.py` | 宠物参数加「进入特效」下拉 9 项，进 `value_widgets` 滚轮 guard；`_load_from` 回填、`collect()` 用 `replace` 透传 |
| `main.py` | `_enter_kind_for(trigger)`（固定 / random / auto 分派，auto 下 toggle 随机）、`_enter_pet(trigger, after)`、`_play_effect_only(trigger)`、`_play_or_enter(trigger)`；`_run_command` 的 chat/reminder/focus/scene 接线 |

**随触发的两个分支**（解决「只看到开机和恢复两个」）：

- 宠物**隐藏** → `_enter_pet()`：`setWindowOpacity(0)` → 播特效 → `finished` 后 `_fade_in_pet()`（+ after）。
- 宠物**已可见** → `_play_effect_only()`：只播独立透明叠加的特效层，**不动宠物显形**；
  播前 `prev.stop()` 清掉上一个反馈层，避免叠加闪烁。

## 3. C45 整体优化：从「7 段拼盘」回到统一骨架

### 3.1 根因（实证）

| 病灶 | 位置 | 修复 |
|---|---|---|
| **绘制期每帧随机** | `_paint_puzzle` 逐帧 `rng.uniform` 生成碎片落点；`_paint_terminal` 逐帧 `rng.choice` 字符；`_paint_ink` 逐帧 `rng.random` 定波纹配色；`_paint_magnifier` 逐帧 `rng.uniform` 定光束高度与拖尾 | 全部**上收到构造期缓存**：`_puzzle_pieces`（每片 tx/ty/sx/sy/rot/cyan）、`_terminal_grid`（二维字符表）、`_ink_colors`、`_magnifier_cy`/`_tail_factor`。paint 阶段只读，绝不调 rng |
| 骨架缺失 | 仅 `_paint_spawn` 有汇聚环/扫描环/白核爆闪，其余 6 种各画各的 | `_paint_enter()` 统一编排三段式；各 `_paint_*` 退化为纯主体绘制 |
| 节奏不齐 | `ENTER_DURATION_MS` 780~950ms 七种不一 | 统一 `{kind: 950}`，沿用 FR v1.20 C37；`EXIT_DURATION_MS = 700` |
| 硬起硬停 | 无整体透明度包络 | `_envelope(t)` 首尾各 6% 渐入渐出，`paintEvent` 里 `painter.setOpacity()`；退场用 `_exit_opacity(t)=1-t²` 渐隐 |
| 淡入与爆闪脱节 | `finished` 只在 100% 发 → 特效消失后才开始 220ms 淡入，中间有空白帧 | `REVEAL_AT=0.82` 处提前发 `finished`，宠物淡入与收尾爆闪重叠；`_revealed` 标志保证只发一次 |

### 3.2 统一骨架（`_paint_enter`）

```
main_t = min(1.0, t / MAIN_END)          # MAIN_END = 0.66
_draw_converge_rings(painter, ease_out(main_t))   # 铺垫：两圈能量环由外向内锁定
_draw_scan_ring(painter, t)                       # 铺垫：四段短弧旋转扫描
dispatch[kind](painter, main_t)                  # 主体：7 种风格各异，只在 0~66% 展开
_draw_finale(painter, t)                         # 收尾(66%~100%)：光晕 + 白核爆闪 + 十字准星
```

`_draw_finale` 与最初 spawn 的收尾代码**逐行同源**，因此 7 种看起来是同一个程序的出场。
主体只负责自己的形状，不再各自画收尾光晕（`_paint_puzzle`/`_paint_curtain` 里重复的
尾段 glow 已删），避免叠加过曝。

### 3.2b R15 三角碎片配色修正

用户真机目检反馈「三角配色不好看」。定位为重构引入的配色 bug：

| 旧 | 新 |
|---|---|
| `base = CYAN if cyan else CYAN_DARK`（非青碎片的「亮部」是暗青 `(20,80,100)`） | `tone = PUZZLE_TONES[0 if cyan else 1]`，`PUZZLE_TONES = (CYAN, ORANGE)` |
| `dark = CYAN_DARK if cyan else ORANGE_DARK`，两端都 `setAlpha(alpha)` → **不透明暗色实心块** | 描边 `edge.setAlpha(alpha)`；填充 `head.setAlpha(alpha*0.42)` → `tail.setAlpha(0)` 渐隐到全透明 |

删除仅此处使用的 `CYAN_DARK`/`ORANGE_DARK` 两个常量。回归测试
`should_puzzle_use_only_bright_tones()` 锁住两色亮度门槛、
`should_puzzle_fill_fade_to_transparent()` 锁住绘制确定性（`tests/ui/test_tech_effects.py` 共 75 项）。

### 3.3 时序

```
0%        6%                     66%          82%              94%      100%
渐入 ───── 汇聚环+扫描环+主体 ─── 收尾爆闪 ─── 提前 finished ── 渐出 ── hide
                                                │ (宠物 220ms 淡入与爆闪重叠)
                                                └ _fade_in_pet() → after()
```

退场（`_mode == "exit"`）不走 `REVEAL_AT`，仍在 100% 只发一次 `finished`
（`_exit_app` 依赖它触发 `app.quit`）。

### 3.4 兼容要点

- `TechEffect(center, mode="spawn")` 仍兼容：`mode` 作为 `kind` 别名。
- `exit` 之外的未知 kind 兜底为 `spawn`，永不崩。
- `rng` 可注入（`TechEffect(..., rng=Random(42))`）以支撑确定性测试。

## 4. 测试与质量账

| 文件 | 覆盖 |
|---|---|
| `tests/ui/test_tech_effects.py`（73 项） | 7 kind + exit × 5 阶段绘制不崩；**7 kind + exit × 3 进度逐帧像素确定性**；统一 950ms；提前 reveal 只发一次；退场不提前发；包络首尾为 0；随机池含全部 7 种；旧 `mode=` 兼容 |
| `tests/unit/test_enter_effect_config.py` | auto 默认、9 值合法、非法/非字符串回落 auto |
| `tests/ui/test_main_assembly.py::TestEnterEffectRandom` | auto-toggle 随机且排除非法值、random 模式、手动固定、非 toggle 场景仍固定 |
| `tests/ui/test_main_assembly.py::TestEnterEffectFollowsTrigger` | 宠物可见时播反馈层且不隐藏、隐藏时才登场 |
| `tests/unit/test_package_macos.py` | 公证/签名开关与凭据降级 |

逐帧确定性用「同一 `t` 连画两帧、稀疏采样像素摘要相等」实现，不依赖 Qt 绑定的
`constBits()` 内存缓冲比较语义（不同 `QImage` 对象的缓冲地址不可直接比）。

```
全量基线 1350 passed / 0 failures（上一轮执行结果；本次未重跑全量）
覆盖率 94.37%（3873 / 4104 stmts，门槛 90）
本次新增 7 项 workflow 护栏单独验证 7 passed
改动文件 lints 0
```

## 5. 否决方案与取舍

| 否决 | 理由 |
|---|---|
| 直接删掉 6 种、只留最初 spawn | 用户明确要过「不要一个风格」；统一**骨架**即可兼顾质感与多样性 |
| 在 paint 里用「按 t 派生的确定性伪随机」代替缓存 | 仍需每帧重算且跨特效口径不一；上收构造期最直白、可测 |
| 用 `QPropertyAnimation` / `QGraphicsEffect` 做特效 | 引入 Qt 动画栈与对象生命周期管理，30fps QTimer + QPainter 已够且零依赖 |
| 给每种特效各配一个时长以「显得有层次」 | 正是「差太多」的来源；统一 950ms 才像同一套出场 |
| `finished` 提前到 66% | 宠物会在主体还没演完时淡入，喧宾夺主；82% 与 220ms 淡入正好落在爆闪段 |
| 提前发 `finished` 时同时置 `_revealed` 后不再 hide | 会导致特效窗残留；结束分支继续 hide，仅靠标志抑制重复发信号 |
| CI 里硬编码 Apple 凭据 / 打印 notarytool 命令 | 泄露风险；凭据只在 secrets + 环境变量，dry-run 不回显密码 |
| macOS 分支在本机「假装验证过」 | Windows 无 codesign/xcrun/钥匙串，只验证命令拼接与开关逻辑，真机项如实登记 FR v1.26 §5 |

## 6. 真机目检清单（本机无法完成）

1. 开机 → 粒子汇聚：汇聚环、扫描环、白核爆闪三段是否连贯，宠物淡入是否不留空白帧。
2. 设置里逐个固定 7 种，观察主体是否**稳定不抖**（尤其 puzzle 碎片、terminal 字符、ink 波纹）。
3. 隐藏→恢复多次，确认随机且不出现闪烁/残留特效窗。
4. 宠物挂着时执行「跟我聊天 / 切场景 / 排提醒」，确认反馈层可见且宠物不被隐藏。
5. macOS：配好 secrets 后推 tag，验证 dmg 签名 + 公证票据装订、双击不再报「无法验证开发者」。
