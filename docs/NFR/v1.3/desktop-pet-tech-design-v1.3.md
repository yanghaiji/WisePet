# 桌面宠物 AI Bot 技术设计

- **版本**：v1.3
- **状态**：待评审
- **对应需求**：[FR v1.2](../../FR/v1.2/desktop-pet-PRD-v1.2.md)（C1 多皮肤、C2 画风写实化、C3 离线小脑统计 NLP 化）
- **上一版本**：[v1.2](../v1.2/desktop-pet-tech-design-v1.2.md)
- **不变部分**：四层架构（ui/app/domain/infra）、领域模型、事件契约、线程模型、存储方案、拟流式与 `ChatService` 分支点全部沿用，本文只写本版三项变更的落地。

---

## 1. C1 多皮肤：目录约定 + 回落 + 热切换

### 1.1 各层改动
| 层 | 位置 | 改动 |
|----|------|------|
| domain | `domain/config_model.py` | 新增 `PET_SKINS=("cat","dog","monkey")`、`DEFAULT_SKIN="cat"`、`normalize_skin()`（非法值归一为默认，且可被保存修正）；`PetParams` 增字段 `skin` |
| infra | `infra/frame_loader.py` | 构造签名 `FrameAssetLoader(assets_dir, skin=DEFAULT_SKIN)`，读 `assets/pet/{skin}/{action}/*.png`；该皮肤缺该动作 → 回落默认皮肤同名动作并记一条 warning；新增 `available_skins()`（8 动作全齐才算已安装） |
| ui | `ui/frame_animator.py` | 新增 `set_loader(loader)`：清 `_cache` 且把 `_action` 置空，下一 tick 强制重取帧 → **不重启即时换图**，且不会停在旧皮肤的半帧上 |
| ui | `ui/settings_dialog.py` | `SKIN_LABELS={"cat":"橘猫","dog":"柴犬","monkey":"小猴"}`；下拉只列 `available_skins()` 交来的项 |
| 组装根 | `main.py` | `apply_config()` 比较 skin 变化后换 loader；`_available_skins()` 供设置窗；`_local_context()` 把 `config.pet.skin` 注入本地小脑（自称/语气词随皮肤变） |

### 1.2 关键取舍
- **回落而不是报错**：用户手工删素材、或新皮肤只做了一半时，界面必须仍有宠物；只有默认皮肤也缺才落到 v1.0 的 `PlaceholderRenderer`。
- **皮肤可用性由素材反推，而不是写死常量**：`available_skins()` 扫目录，新增皮肤不需要改 UI 代码（PRD v1.2 §1.4 第 2 条）。
- **热切换放在 animator 而不是重建 widget**：重建窗口会丢拖拽位置与当前动画相位，代价远大于清一次缓存。
- 素材目录迁移：`assets/pet/{action}/` → `assets/pet/cat/{action}/`，`FrameAssetLoader` 是唯一读目录的地方，因此运行时代码零额外改动。

---

## 2. C2 生成器写实化：`tools/gen_pet_assets.py` 重写

### 2.1 结构：参数与绘制分离
| 构件 | 职责 |
|------|------|
| `Palette`（frozen dataclass） | 一只动物的全部色层：`base/light/shade/deep`（毛色四层）+ `belly/muzzle` + 眼睛四层（`iris/iris_light/limbal`）+ `nose/nose_light` + `ear_inner/whisker/stripe` |
| `Anatomy`（frozen dataclass） | 解剖：头宽高与 `head_cy`、躯干宽高与 `body_cy`、`ear`(tri/fold/round)、`ear_w/h/dx`、眼宽高与偏移、`outer_corner_up`、`slit_pupil`、口吻宽高与位置、`nose_w/h`、`whiskers`、`tail`(bushy/curl/thin)、`stripes`、`face_ring`、`fur_len`、`crest` |
| `SKINS: dict[str, (Palette, Anatomy)]` | 三套参数表；新增皮肤=加一行参数，不新增绘制代码 |
| `_frame_cfg(action, index)` | 沿用 v1.2 的姿态表（去掉 `blush` 等卡通项），只管相位，不管物种 |

写实化三件事全部落在绘制层：`_volume()`（径向渐变立体积 + 线性渐变压暗部 + 边缘同色系压深 + 受光弧反光，取消统一黑描边）、`_fur_texture()`（剪影内 55%~95% 环带的明暗纹理）、`_fur_along_path()`（沿真实剪影的法线毛流）。

### 2.2 三个非显然的实现点
1. **毛流必须沿真实剪影取点**：早期用外接椭圆的参数角 `cos/sin` 取点，而躯干是梨形 `QPainterPath`，结果"毛飘在身体外"，缩略图看就是一圈虚线。改为 `path.pointAtPercent(t)` + `path.angleAtPercent(t)`（切线角，法线=切线旋转 90°），毛才贴边；并且法线朝下（`ny > 0.75`）处不炸毛，否则下巴/腹部长出硬刺。
2. **尾巴锥形**：等宽圆头笔刷画不出真实尾巴。`_cubic_at()` 按参数取三次贝塞尔点，`_tapered_tail()` 把曲线切成 24 段逐段变宽（先铺深色底层当边缘，再叠主色），最后沿法线贴尾毛。
3. **眼睛是结构而不是圆点**：`_lens_path()` 生成真实眼裂（内眼角偏下、外眼角按物种上扬/下垂），`setClipPath` 后画巩膜 → 虹膜径向渐变（`iris_light → iris → limbal` 形成 limbal ring）→ 瞳孔（猫科竖瞳 `pw=0.30r`、犬猴科圆瞳）→ 双高光 → 上眼睑投影，最后 restore 再描眼裂线。情绪态 `closed/blink/happy/cross/sad/up` 各自改变形状而不是简单加符号（`cross` 用挤紧的弧形而非卡通叉号）。

### 2.3 可复现性
笔触随机数一律 `random.Random(zlib.crc32(f"{skin}|{action}|{index}"))`：同参数重跑逐字节一致，素材 diff 才有意义；否则每次 `--skin all` 会产生 96 处无意义改动。

### 2.4 本版踩到的 Qt/PySide6 事实（登记备查）
| 现象 | 结论 |
|------|------|
| `QPainterPath.positionAtPercent` 不存在 | 用 `pointAtPercent` / `angleAtPercent` |
| `QPainterPath(QPointF, QPointF, QPointF)` 无此构造 | 只能 `moveTo/lineTo` 逐点建（三参构造会抛 TypeError） |
| `QPen(color, w, Qt.SolidLine, Qt.RoundJoin)` 报错 | 第 4 个位置参数是 `PenCapStyle`，要给 join 必须先给 cap |
| `gradient.setColorAt(0, "#B9D4DC")` 报错 | 停靠点必须是 `QColor`，不接受字符串 |
| `drawArc(rect, 180*16, 150*16)` 画"撇嘴"方向不直观 | 弧向受 y 轴向下影响易写反，撇嘴改用 `QPainterPath.cubicTo` 显式控制 |
| `drawPolygon` 会闭合折线 | 矢量 `Z` 必须用三条 `drawLine`，否则画成蝴蝶结 |

### 2.5 用法与产物
```
python tools/gen_pet_assets.py                 # 只重生 cat（32 帧）
python tools/gen_pet_assets.py --skin all      # 三皮肤 96 帧
python tools/gen_pet_assets.py --skin cat --preview   # 另出 tmp/preview-cat.png 目检（临时产物）
```
`--skin` 传未知值时打印可选皮肤并返回码 2（不抛栈）。完整性仍由 `tests/integration/test_pet_assets.py` 按"已安装皮肤"动态取参校验（不 import PySide6）。

---

## 3. C3 离线小脑：四段路由的统计 NLP

### 3.1 模块划分（全部 app 层，仅标准库 + 可选 jieba）
```
app/nlp.py            分词与相似度原语：Tokenizer / bigram_split / normalize / STOPWORDS / overlap_score
app/corpus.py         语料即能力上限：INTENT_SAMPLES(20 类) / KNOWLEDGE(20 条) / 反问与引导模板 / 姓名与活动模式
app/dialog_engine.py  统计层：Bm25Index / IntentClassifier / UserMemory / DialogEngine
app/local_responder.py 编排器：规则表 → 统计层 → 多轮 → 兜底（保持 v1.1 对外契约不变）
```
`ChatService` 与 ui 层零改动：仍是 `LocalResponder.reply()` + `stream_pieces` 拟流式（v1.2 §2.4 全部沿用）。

### 3.2 路由顺序（`_route`，返回 `(路由名, 文案)`）
| 段 | 判据 | 路由名 | 为什么在这个位置 |
|----|------|--------|------------------|
| 0 | 姓名模式（`我叫X` 等） | `name` | 必须最先，否则会被 `greet` 的"我"类关键词抢走 |
| 1 | 关键词表（20 条规则，表序即优先级） | 规则名 | 字面命中零歧义、最快，保留为最高优先级（PRD v1.2 §3.5） |
| 2 | 朴素贝叶斯意图 → 复用同名 handler | 意图名 | 规则未命中时的同义泛化 |
| 3 | 回指短句（`嗯`/`然后呢`/`为什么`）且上一轮有话题 | `follow_up` | 多轮反问，避免把"嗯"丢进兜底 |
| 4 | BM25 + 整句精配检索 | `knowledge` | 检索式问答 |
| 5 | 反射式兜底（复述话题 + 引导） | `fallback` | 全不命中 |

`matched_rule(text)` 以 `observe=True` 复用同一条链路但不写记忆，因此诊断查询不会污染会话状态。

### 3.3 分词后端与性能约束（PRD v1.2 §3.2 的落实）
- `Tokenizer` **惰性**：`__init__` 不 import jieba，`backend == "pending"`；首次 `tokenize()` 才 `_resolve()`。索引同样惰性构建（`DialogEngine._index is None`），冷启动不付任何 NLP 成本。
- 只在 `ChatService` 的 worker 线程里被调用（沿用 v1.2 线程模型），UI 线程零接触。
- **两级降级**：`import jieba` 失败 → bigram；`jieba` 可用但 `add_word` 调优失败 → 保留 jieba 只丢掉领域词；`_resolve()` 外层再包一层 `except`，词典加载异常绝不冒到聊天链路。
- 本版实测（Windows，`.venv`，Python 3.11）：首次触发词典 647.6ms（conda py311_ai 冷启动 2.6s、之后 0.62~0.67s），稳态 `grams()` 0.033ms/句，RSS 增量约 +70MB（Qt 基线 38MB → 108MB）。因此词典**绝不进冷启动**是硬要求，而不是优化项。
- `grams()` = unigram + 相邻实词 bigram，**停用词既不单独成词也不参与拼接**；`tokenize()` 过滤掉无 `\w` 的 token（jieba 会把"，"当词切出来）。

### 3.4 两个算法标定坑（都曾导致"看起来接了 NLP 其实没生效"）
| 坑 | 现象 | 结论 |
|----|------|------|
| 朴素贝叶斯按句长归一 | 20 类后验被压平到 ~1/N，绝对置信门槛永远不过 → 分类器一次都不触发 | 未归一 log 后验直接 softmax，**不能先除句长**（那等价于给 softmax 加温度） |
| bigram 跨停用词拼接 | "今天被/了一/有点"这类虚词以高 idf 顶起 BM25，知识检索乱命中并把"隐私"答案答给"被组长说了一顿" | 停用词不桥接 + `KNOWLEDGE_MIN_TERMS=2` + `KNOWLEDGE_STRONG_SCORE=6.0` 双门 |

标定值全部来自探针实测而非拍脑袋：`INTENT_MIN_CONF=0.25`（含义是**比 20 类均匀分布 0.05 高 5 倍**，不是"25% 把握"）、`INTENT_MIN_MARGIN=0.18`、`INTENT_MIN_MATCH=2`、`BM25_K1=1.5`、`BM25_B=0.75`、`PHRASE_SCORE=99.0`（语料问法原样出现在用户句里时优先精配）。

### 3.5 话题与记忆
- `topic_of()`：先在语料里出现过的实词中挑**最稀有**者（df 最小），都没有则只从**含中文**的词里取最长，再没有返回 `""`。这条限制是为了拉丁噪声（`zzz qqq`）不被当成话题反问你。
- `UserMemory`：`user_name / activity / last_topic / last_intent / turns / seen_intents`，**只在会话内**，`reset()` 清空，不写盘不上传（PRD AC-BRAIN-4/8）。
- `_note()` 在**生成文案之后**调用，且优先记用户自己嘴里的实词（记「工作」而不是条目名「决策」），否则 advice/follow_up 引用的是当轮主题名而不是用户话题。
- 记忆抽取 `extract_memory(text, patterns)`：`ACTIVITY_PATTERNS` 按长模式优先排列且不含动词（"我在写"当模式会让宾语只剩"代码"），值截断 12 字、遇标点收尾。

### 3.6 决策与否决
| 决策 | 理由 | 被否决的备选 |
|------|------|--------------|
| 不引入任何生成式模型（含 0.5B 级） | 用户硬约束：无独显/仅集显要能跑，且不得影响办公；0.5B 级 CPU 推理首字秒级、常驻内存 GB 级，与桌面小宠的常驻形态冲突 | 本地小模型、云端免费额度（引入 Key 依赖与网络，回到 v1.0 的哑火问题） |
| 不引入判别式嵌入模型（如 bge-small） | 仍是 torch 级依赖（数百 MB）+ 首次加载秒级，收益只覆盖"语义相似度"一项，而该收益已由 BM25+精配满足 | 向量检索服务、FAISS |
| 统计 NLP 三层 + 规则前置 | 关键词零歧义最快；NB/BM25 补泛化；规则优先级最高保证已能答的绝不退化成猜 | 纯规则（v1.1 现状：换个说法即兜底）、把语料塞进 prompt 交给 AI |
| jieba 作为**可选**依赖 | 真分词显著提升意图泛化（同义用例只在 jieba 下成立），但缺失必须能跑；测试用注入 bigram 保证跨环境确定 | 强制依赖、或自研完整中文分词（成本不对等） |
| 语料集中在 `corpus.py` | 能力上限=语料覆盖，加一条知识只加一个 `Knowledge` 条目，不动算法 | 把文案散进各 handler |

---

## 4. 测试与基线（本版实测）
| 项 | v1.2 | v1.3 |
|----|------|------|
| 用例总数 | 330 | **453**（unit 227 / integration 150 / ui 76） |
| 覆盖率 | 98.31%（TOTAL 1186 stmts / 20 miss） | **99.56%**（TOTAL 1597 stmts / 7 miss），门槛 `fail_under=90` 不变 |
| 新增模块覆盖 | — | app 层除 `pet_controller.py`（99%，剩 240-241 非法迁移静默分支）外全部 **100%**：`nlp.py`（补齐 import 失败与词典调优失败两条降级路径）、`corpus.py`、`dialog_engine.py`（补齐语料坏问法与空输入两条防御分支）、`local_responder.py`（补齐心情/饱食/精力中间档与低落知识后缀分支） |
| 新增测试 | — | `tests/unit/test_nlp.py`（后端惰性/两级降级/停用词不桥接/标点过滤）、`tests/unit/test_dialog_engine.py`（惰性建索引、精配检索、虚词不顶分、NB 平票拒绝、话题挑选、记忆抽取与截断、reset）、`tests/unit/test_local_responder.py` 扩展（皮肤自称参数化、四段路由、多轮反问、姓名记忆与隐私）、`tests/integration/test_pet_assets.py` 改为按已安装皮肤动态取参（三皮肤自动扩到 96 帧校验）、皮肤 loader/animator/settings/assembly 用例、`test_local_responder.py` 再补 `TestVitalBands`（中间档话术逐档断言，防止写错的文案藏在未触达行里） |
| 跨环境确定性 | 同左 | 统计层用例注入 bigram 或自带小语料，**不依赖 jieba 是否安装**；需要真分词才能成立的泛化用例统一用 `pytest.importorskip("jieba")` 标注 |

## 5. 遗留与后续
- 六项手工清单（AGENT.md §4）仍未逐项签字；性能指标 CPU/内存/24h 稳定性仍待人工采集。
- 写实化仍是插画级：毛发为笔触模拟而非逐根渲染，跨皮肤品种细节（爪垫、胡须根部阴影）未做。
- 语料覆盖 20 条知识 + 20 类意图，超出即兜底；扩充方式是加语料而非改算法。
- 跨会话长期记忆、皮肤素材在线下载按 PRD v1.2 §4 列为范围外。
