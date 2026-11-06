# 桌面宠物 AI Bot 技术设计

- **版本**：v1.2
- **状态**：待评审
- **对应需求**：[FR v1.1](../../FR/v1.1/desktop-pet-PRD-v1.1.md)（C1 形象正式化、C2 离线可聊）
- **上一版本**：[v1.1](../v1.1/desktop-pet-tech-design-v1.1.md)（MVP 实现偏差登记）
- **不变部分**：四层架构（ui/app/domain/infra）、领域模型、事件契约、线程模型、存储方案全部沿用 v1.0/v1.1，本文只写本版两项变更的落地。

---

## 1. C1 宠物形象：离线生成素材 + 运行时管线零改动

### 1.1 既有管线（未修改）
| 环节 | 位置 | 职责 |
|------|------|------|
| 素材发现 | `infra/frame_loader.py` `FrameAssetLoader.frames(action)` | 读 `assets/pet/{action}/*.png` 按文件名排序；缺目录返回空并记 `missing_actions` |
| 播放 | `ui/frame_animator.py` | QTimer ~12FPS，`CANVAS_SIZE=128`，`_fit_canvas` 等比缩放居中 |
| 降级 | `ui/frame_animator.py` `PlaceholderRenderer` | 素材缺失时程序绘制占位圆脸，不崩溃（**保留**，PRD v1.1 §1.3 第 3 条） |

### 1.2 素材生成器 `tools/gen_pet_assets.py`
- 纯 QPainter 离屏绘制：`QImage(SIZE, SIZE, Format_ARGB32_Premultiplied)` + `fill(Qt.transparent)`，**不创建 `QGuiApplication`** 也能出图，因此可在 headless / CI 执行。
- 贴纸风格实现：粗描边实心形状 = 同一条 `QPainterPath` 先用 `OUTLINE` 宽 `26+LW` 描边、再用 `FUR` 宽 26 描边；配色为命名常量（`FUR #F5A65B`、`FUR_DARK #E0842F`、`CREAM`、`EAR_INNER`、`IRIS #35B37E`、`OUTLINE #3A2B21`）。
- 姿态由 `_frame_cfg(action, index)` 单点描述（head_tilt / ears / eyes / mouth / tail / squash / step / blush / extras / bob），8 动作 × 4 相位 → 32 张 `01..04.png`，`01` 为标准姿态。
- 字体零依赖：`Z` / `?` / `♪` 全部矢量化绘制（`_z_glyph` / `_q_glyph` / `_note_glyph`）。原因：`setFont`/`drawText` 需 `QGuiApplication`，headless 下报 `QFontDatabase: Must construct a QGuiApplication`；同时避免打包后字体缺失导致渲染漂移。
- Qt 角度为 1/16 度：下弧（微笑 ∪）= `drawArc(rect, 0, 180*16)`，上弧（∩ / 撇嘴）= `drawArc(rect, 180*16, 180*16)`。
- 用法：`python tools/gen_pet_assets.py`（重生 32 帧）；`--preview` 额外输出 `tmp/preview.png` 总览图供视觉校验，属临时产物、用后清理。

### 1.3 决策与否决
| 决策 | 理由 | 被否决的备选 |
|------|------|--------------|
| 生成期落盘 PNG，运行时只读文件 | 运行时素材管线（loader/animator/降级）零改动，回归面最小；美术参数留在工具里不污染产品代码 | **运行时用 QPainter 直接画猫**：需把姿态参数塞进 `ui/frame_animator.py`，每帧重绘增加 CPU，且绕开了既有 `FrameAssetLoader` 契约与占位降级路径 |
| 程序化绘制而非外部美术/AI 生图 | 可版本化、可复现、无版权与分发问题；改参数重跑即可 | 外部素材包（本版本额度受限，且引入二进制来源不可追溯） |
| 素材完整性用文件契约测试守护 | `tests/integration/test_pet_assets.py` 校验 8×4 帧存在、PNG 魔数、体积 >1000B、`missing_actions()` 为空；**刻意不 import PySide6**，Qt 不可用时该测试仍能给出信号 | 像素级图像断言（脆弱、跨平台不稳定） |

### 1.4 分层铁律例外登记（同步 AGENT.md §2.3）
`tools/` 目录允许 import PySide6，边界为：**仅供离线执行的资产生成脚本**，不得被 `src/ai_bot/**` 或 `main.py` 导入，不进入运行期依赖与打包清单。铁律本体（仅 `ui/`、`main.py`、`tests/ui/` 可在运行时链接 Qt）不变。

---

## 2. C2 离线可聊：app 层 LocalResponder + 拟流式

### 2.1 落点与依赖方向
```
main.py（组装根）
  └─ 构造 LocalResponder(context_provider=self._local_context)
        └─ 注入 ChatService(local_responder=...)
              └─ app/local_responder.py（纯标准库，只读 LocalContext）
```
- `app/local_responder.py` 属 **app 层**，仅标准库、零三方依赖，遵守 ui → app → domain 方向。
- **不进 domain**：话术文案与关键词表是产品表达而非领域不变量，塞进 domain 会让领域层背上文案变更频率。
- **不作为 `AIChatGateway` 的第三个实现**：该端口的语义是"外部网关 + 错误映射（AUTH/NETWORK/RATE/TIMEOUT/SERVER）"，本地引擎没有配置与网络语义；若伪装成网关，瞬时成功的回复会被迫走 `AIGatewayError` 路径，反而污染错误兜底。故以**独立协作者 + ChatService 显式分支**表达。
- 上下文注入：`main.py` 的 `_local_context()` 从 `pet.vitals`（hunger/mood/energy）与 `SystemClock`（`time.localtime(...).tm_hour`）构造 `LocalContext`，app 层不反向依赖 ui/main。

### 2.2 模式切换点
`ChatService.process_next()`：出队后取 `AIConfig`，`self._local is not None and not cfg.configured` → 走 `_reply_locally()`；否则原样走网关流式。
- 开关即 `AIConfig.configured`（`bool(api_key.strip())`），与 PRD v1.0/v1.1 的配置语义一致，无新增配置项。
- 未注入 `local_responder` 时（如既有单测与旧装配）保持 v1.0 行为：空 Key 请求由网关抛 `AUTH` → 兜底话术。该兼容性由 `test_chat_service.py` 显式锁定。
- ui 侧可发现性：`ui/settings_dialog.py` 的 `_NO_KEY_HINT` 改为“留空也能聊（走本地小脑），填上 Key 接 AI 大脑会更聪明”，保存后空 Key 的状态行同步提示“未配置，暂用本地小脑”（两条 `tests/ui/test_settings_dialog.py` 用例锁定）。“测试连接”失败仍走 v1.0 可读兜底——那是**故障**路径，不等同于**未配置**。

### 2.3 回复引擎（确定性）
- 16 条命名规则 + `fallback`，命中顺序即 `_rules` 表序，`_find()` 取首个命中；规则名可经 `matched_rule()` 查询，供日志与测试诊断。
- 状态感知分档阈值：`MOOD 70/40/25`、`HUNGER 70/40/20`、`ENERGY 70/40/20`；夜间判定 `hour >= 23 or hour < 7`；时段名按 `hour // 3` 共 8 档（`_PERIODS`，档位与索引必须等长，否则会出现"15 点显示上午"这类错位）。
- **不用 `random`**：`_pick()` 用自增 `_turn % len(pool)` 轮换，保证同输入序列可复现、可断言具体文案。
- 首次回复附带 `LOCAL_MODE_HINT`（如何接上 AI），`_hinted` 置位后不再重复；`reset()` 与会话清空一起回到初始态（PRD AC-LOCAL-8）。
- 关键词归一：输入 `strip().lower()`；中文直接子串匹配，英文短词带尾空格（`"hi "`），防止 `this` / `again` 误命中。

### 2.4 拟流式与事件契约
`stream_pieces(text)`：遇标点（`，。！？…；：、\n`）或攒满 `LOCAL_CHUNK_CHARS = 8` 字符即切段，逐段 `publish(ChatReplyChunkEvent(text_delta=piece))`，末尾 `publish(..., done=True, reply=full)`。
- 复用既有事件与 ui 侧 `CHUNK_THROTTLE_MS` 节流，**ui 层零改动**。
- 本地回复计入 `ChatSession`（`add_turn("assistant", reply)`），下一轮上下文可见。
- 本地分支**不检查 `_cancelled`**：回复瞬时完成且无网络可取消，若沿用中断分支会漏发 `done` 事件，使 ui 永久停在流式状态（该取舍写在 `_reply_locally` docstring 中）。

### 2.5 测试与基线（本版实测）
| 项 | v1.1 | v1.2 |
|----|------|------|
| 用例总数 | 266 | **330**（unit 146 / integration 110 / ui 74） |
| 覆盖率门槛 | `fail_under = 90` | 不变，实测 **98.31%**（TOTAL 1186 stmts / 20 miss） |
| 新增测试 | — | `tests/unit/test_local_responder.py`（规则路由 / 状态感知 / 回复行为）、`tests/integration/test_pet_assets.py`（素材契约）、`tests/unit/test_chat_service.py` 扩展（`TestStreamPieces`、`TestLocalMode`）、`tests/unit/fakes.py` 新增 `FakeResponder`、`tests/ui/test_main_assembly.py` 新增本地聊天端到端、`tests/ui/test_settings_dialog.py` 新增空 Key 占位提示与无 Key 保存两例 |
| `app/local_responder.py` 覆盖率 | — | 90%（13 行为未被命中的分档话术与冷门 handler，属文案分支而非逻辑分支） |
| 真机（Windows + conda py311_ai） | 冷启动 1.39s | 冷启动 **0.42s**，日志无 `asset packs missing`，桌面显示橘猫、未配 Key 可对话 |

### 2.6 遗留与后续
- 六项手工清单（AGENT.md §4）仍未逐项签字，性能指标 CPU/内存/24h 稳定性仍待人工采集。
- 多皮肤切换 UI、本地小脑的意图分类，按 PRD v1.1 §3 列为范围外。
