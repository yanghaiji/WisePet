# 桌面宠物 AI Bot 技术设计

- **版本**：v1.4
- **状态**：待评审
- **对应需求**：[FR v1.3](../../FR/v1.3/desktop-pet-PRD-v1.3.md)（C4 画风改 Q 版可爱、C5 兜底话术卫生）
- **上一版本**：[v1.3](../v1.3/desktop-pet-tech-design-v1.3.md)
- **不变部分**：四层架构、多皮肤管线（v1.3 §1）、离线小脑四段路由与标定值（v1.3 §3）**全部沿用**；本版只重写素材生成器（替换 v1.3 §2）并补一条话题卫生规则。

---

## 1. C4 生成器重写：从解剖参数到 chibi 参数

### 1.1 结构仍然是"参数与绘制分离"
| 构件 | 职责 |
|------|------|
| `Palette`（frozen dataclass） | 色层收敛到 12 项：`base/light/shade/outline/belly/blush/eye/iris/nose/ear_inner/whisker/stripe`。写实版的四层明暗（`deep/muzzle/iris_light/limbal/nose_light`）全部删除——小尺寸下它们只会变成脏色 |
| `Chibi`（frozen dataclass） | 比例与部件：`head_r/head_cy`、`body_w/body_h/body_cy`、`ear`(tri/round)、`ear_w/h/dx/dy`、`eye_r/dx/dy`、`nose_w`、`tail`(bushy/curl/thin)、`stripes`、`cheek_tufts`、`muzzle_patch`、`fluff`、`body_mark`(bib/none) |
| `SKINS: dict[str, (Palette, Chibi)]` | 三套参数表；新增皮肤=加一行参数，不新增绘制代码 |
| `_pose(action, index)` | 姿态表只管**相位**（`bob/tilt/squash/head_dy/ear_droop/eye/mouth/tail_sway/tail_lift/blush/tear/sweat/zzz/bowl/kibble/paw_shift/open_scale`），不管物种 |
| `render_frame()` | 组装顺序即图层顺序（见 §1.3） |

写实层的 `_fur_texture()`（剪影内明暗纹理）、`_fur_along_path()`（密排法线毛流）、`_lens_path()`（真实眼裂）**整段删除**，替代物是 `_fluff_behind()`（轮廓外软毛凸起）与 `_cheek_tufts()`（脸侧两簇）。

### 1.2 可爱度的四个可核对参数
| 手段 | 落点 | 数值 |
|------|------|------|
| 大头 | `head_r=54~56`（画布 256 的 22%），`body_w=86~94` | 头宽 > 身宽，视觉上必然幼态 |
| 大眼 | `eye_r=16~17`、`_eye_shape()` 大椭圆 + 虹膜暖芯 + **双高光**（主高光 0.34r、次高光 0.17r） | 眼占脸约 30%，不画眼裂与竖瞳 |
| 短肢 | 不画腿，`_draw_paws()` 只画两只 13×9.5 的前爪 + 两道趾线 | 四肢缺失是 Q 版的正解，写实腿反而显老 |
| 软配色 | 低饱和奶油系 + `blush` 腮红 + `belly` 浅色围裙 | 取消黑色描边，`outline` 用同色系深色 |

### 1.3 图层顺序（三个"画在前面/后面"的坑都在这）
```
contact_shadow → [rotate(tilt)] → tail → body fluff → body volume → body stroke
  → bib → paws → ears → head cheek_tufts → head volume → head stroke
  → 额头虎斑 → eyes → face → tear/sweat → restore → bowl → zzz
```
1. **尾巴在身体之前**：尾根被身体盖住，只让后半段钻出轮廓，读起来才是"从身后绕出来"。画在身体之后会在胸口出现一根插着的棍子。
2. **食盆必须在 `p.restore()` 之后**：画在身体之前会被躯干完全挡住，`eat` 四帧看起来像在对着空气张嘴。
3. **`cheek_tufts` 在头部 volume 之前**：毛簇根部被头体剪影盖住才像长在脸上；且**整圈软边禁止**——`_fluff_behind` 沿整条头轮廓铺开时，128px 下糊成一朵太阳花。

### 1.4 尾巴：三皮肤共用一条曲线，只换参数
`_tail_points()` 统一为"贴地从臀侧拖出体侧、尾尖上抽"，参数表 `(尾根偏移, 步长, 臀高比, 翘起基数, 翘起系数, 上抽强度)`；`_tapered_curve()` 用等宽圆头笔刷逐段变宽，**整段单色**。

| 翻车形态 | 原因 | 结论 |
|----------|------|------|
| 尾巴像分节的毛毛虫 | 逐段交替 `shade/base` 上色 + 环状横纹 | 整段单色，环纹删除；边缘用一条更宽的 `shade` 同色底垫在下面 |
| 尾巴等于没尾巴 | 尾根偏移与步长太小，整条落在身体剪影内 | 身体最宽处约 ±51px，尾尖偏移必须 > 55px 才可见 |
| 尾巴像插了根法棍 | 把尾巴改画在身体之后，且沿体侧直线上升 | 画回身体之前 + 尾根收进轮廓 + 明显收细（21→10） |
| 柴犬卷尾像马克杯把手 | 试图用螺旋参数在腰侧画圆环 | 放弃圆环，仍用同一条贴地曲线，只把上抽调得比猫更明显 |

### 1.5 情绪与可复现性
- 眼形 7 态 `open/sad/up/blink/closed/happy/hurt`、嘴形 6 态 `smile/open/gape/frown/flat/sleep/grimace`；`frown` 用 `cubicTo` 显式控制（`drawArc` 弧向在 y 轴向下时易写反），`hurt` 是 `><` 两条折线而不是卡通叉号。
- Z 字符仍用三条 `drawLine`（`drawPolygon` 会闭合成蝴蝶结）。
- 随机数一律 `random.Random(zlib.crc32(f"{skin}|{action}|{index}"))`，同参数重跑逐字节一致。
- v1.3 §2.4 登记的 Qt/PySide6 事实全部仍然成立，本版新增一条：`QImage` 离屏绘制无需 `QApplication`，因此 `tools/` 与临时对比脚本都可以不开窗口直接跑。

### 1.6 用法与产物
```
python tools/gen_pet_assets.py                 # 只重生 cat（32 帧）
python tools/gen_pet_assets.py --skin all      # 三皮肤 96 帧
python tools/gen_pet_assets.py --skin cat --preview   # 另出 tmp/preview-cat.png 目检（临时产物）
```
完整性仍由 `tests/integration/test_pet_assets.py` 按"已安装皮肤"动态取参校验（不 import PySide6）：8 动作 × 4 帧、PNG 魔数、单帧 > 1KB。

---

## 2. C5 兜底话术卫生（`app/local_responder.py`）

| 改动 | 说明 |
|------|------|
| 模块级 `PHATIC_INTENTS` | 10 个寒暄意图（`greet/thanks/bye/who/praise/insult/love/help/remind/ai`）的常量集合，`_note()` 命中时写 `note(intent, "")`，**不登记话题** |
| `_note()` 优先级 | 寒暄判定放在"记用户实词"之前，否则 `topic_of("你好")` 返回 `"你好"` 本身并被记下来 |
| `_fallback()` 回指判定 | 只有 `topic_of(text)` 为空**且** `is_anaphoric(text)` 为真时才取 `memory.last_topic`；否则只回显本轮原文 |
| 空输入降级 | 原文为空时回 `"这件事"`，不输出空引号（`ECHO_MAX_LEN` 截断保留） |

诊断入口 `matched_rule()` 仍以 `observe=True` 复用同一链路且不写记忆，因此这条改动不影响"查询不污染会话状态"的既有保证。

---

## 3. 测试与基线（本版实测）
| 项 | v1.3 | v1.4 |
|----|------|------|
| 用例总数 | 453 | **456**（unit 230 / integration 150 / ui 76） |
| 覆盖率 | 99.56%（1597 stmts / 7 miss） | **99.56%**（TOTAL 1603 stmts / 7 miss），门槛 `fail_under=90` 不变 |
| 未覆盖行 | `pet_controller.py:240-241`、`json_store.py:53-56`、`platform_util.py:57` | 同左，三处均为"环境/非法迁移静默分支"，不为其造伪用例 |
| 新增测试 | — | `tests/unit/test_local_responder.py::TestFallbackEcho`（寒暄不记话题 / 上一轮是问候时不得回显旧话题 / 回指仍接住）；`TestVitalBands` 与 `test_dialog_engine.py` 两条防御分支用例随本版一并保留 |
| 素材校验 | 三皮肤 96 帧 | 同左（画风替换不改帧数与目录，因此素材用例零改动即通过） |
| 跨环境确定性 | 同左 | 同左：统计层用例注入 bigram 或自带小语料，需要真分词的用例用 `pytest.importorskip("jieba")` 标注 |

---

## 4. 遗留与后续
- 可爱度是**主观验收**：脚本目检（`tmp/preview-{cat,dog,monkey}.png` + 三皮肤并排）只能保证结构不翻车，最终签字仍需在 128×128 真机上做。
- 三皮肤目前靠"耳形 + 尾形 + 花纹 + 配色"区分，柴犬与橘猫的**剪影相似度仍偏高**（都是立耳）；下一版若要更强区分，应改耳形（垂耳/半立）而不是继续调色。
- 表情仍受限于 8 动作 × 4 帧；`hurt` 与 `sad` 的差异只靠眼形与泪滴，跨皮肤观感需要真机确认。
- 语料覆盖 20 条知识 + 20 类意图，超出即兜底；扩充方式是加语料而非改算法。
- 跨会话长期记忆、皮肤素材在线下载按 PRD v1.2 §4 列为范围外。
