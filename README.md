# 桌面宠物 WisePet

一只住在桌面角落的小宠物：**橘猫 / 柴犬 / 小猴**三套形象可切换，透明置顶、会走动会犯困、可以抚摸投喂、会在你久坐忘喝水时提醒你，并且**不配置 AI Key 也能聊天**。**点一下宠物**浮出单行输入条即可聊（顶部小标签显示当前是思考/回复/出错哪一步、由大模型还是本地小脑作答）；**右键 →「历史对话…」**打开按天回看的记录窗，底部可以直接接着聊，打开它会收起单独的输入条。两个聊天界面都贴在宠物左侧（放不下才转右侧）、宠物挪窝时跟着走；回答气泡与聊天卡片是同一套圆角贴纸风。聊天时宠物会原地待着不乱跑，重启后也记得之前聊过的话。

- 技术栈：Python ≥ 3.10、PySide6、httpx、pytest；本地小脑可选用 jieba 分词（未安装自动回退）
- 平台：Windows 10/11、macOS 12+（Linux 不在范围内）
- 架构：四层 DDD（`ui` / `app` / `domain` / `infra`）+ 组装根 `main.py`，依赖方向 ui → app → domain ← infra

---

## 快速开始

### Windows（推荐，一键脚本）
```powershell
.\run.ps1        # 首次会创建 .venv 并安装依赖，然后启动桌宠
```

### macOS / Linux
```bash
./run.sh
```

### 手动启动
```powershell
python -m venv .venv
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt
& ".venv\Scripts\python.exe" main.py
```

> **用 conda 环境跑？** Qt 绑定必须与解释器构建配套，请用 conda 渠道安装：
> `conda install -c conda-forge pyside6`
> 用 `pip install PySide6` 装进 conda 环境会在 `import QtCore` 时报 `DLL load failed`。
> jieba 是纯 Python 包，在 conda 环境里用 `pip install jieba` 即可，不受上述限制。

启动后日志里会打印 `cold start ...`，正常应在 1 秒上下。

## 怎么玩

| 操作 | 效果 |
|------|------|
| 左键点宠物 | 浮出单行输入条，回车后思考 → 贴纸风气泡逐段回复；历史窗开着时改为聚焦它的输入行 |
| 按住拖动 | 任意摆放，跨屏可用；松手落位自动钳制到工作区，聊天窗跟着贴到新位置 |
| 右键菜单 | 投喂、抚摸、**历史对话…**（按天回看 + 直接接着聊，打开时收起输入条）、设置、显示/隐藏、退出 |
| 右键 →「隐藏宠物」 | 从桌面收起；**通知区 / 菜单栏的 WisePet 托盘图标仍在**，单击或双击图标、或右键选「显示宠物」即可召回 |
| 待机一会儿 | 自主状态轮播：散步 / 小跑 / 伸懒腰 / 吃 / 被戳 / 思考 / 说话 / 打盹 / 低落都会轮到，**不交互也能看全**（这些演示只换动画，不改数值、不弹气泡）；聊天输入条或历史窗开着时会原地不动，按 Esc 关掉即恢复 |
| 右键 → 投喂 | `EATING` 低头吃东西；点一下宠物是 `HURT` 被戳反应；聊天时是 `THINKING` → `SPEAKING` |
| 夜间 23:00–07:00 且精力低 / 心情低于 25 | `SLEEPING`（睡着回精力）/ `SAD`（低落、偶尔慢慢踱步）；这些状态不会由自主轮询产生 |
| 心情 / 精力长期贴底 | 会周期性 `SAD` 慢慢走，但不会卡死；夜间低精力会去 `SLEEPING` 睡回精力。想立刻恢复就投喂或抚摸 |
| 看日志确认状态 | `~/.WisePet/logs/WisePet.log` 里每次状态切换都有 `behavior IDLE -> WALKING` 这样的行 |
| 设置 → 皮肤 | 贴纸风设置窗（圆角卡片、整条标题栏可拖、分区可滚动、滚轮只滚页面不改值）；在**橘猫 / 柴犬 / 小猴**间切换，保存后不重启立即换形象，说话自称与语气词同步 |
| 闲置 | 随机走动/发呆，夜间精力低会睡觉；心情低落会主动求助 |
| 提醒 | 默认喝水 45 min、久坐 60 min；全屏应用时静默顺延 |

详细配置参考[设置 → 详情](docs/设置手册.md)

## 两种聊天模式

- **本地小脑（默认，无需任何配置）**：未填 API Key 时，由 `app/local_responder.py` 作答，走拟流式逐段上屏，同样计入会话历史。理解链路是按优先级串起的多层路由：关键词规则（**求知型问句优先走知识层**，不让状态规则截胡）→ 朴素贝叶斯意图泛化（听得懂「我心态有点崩」这类词表里没写的说法）→ 多轮话题反问 → BM25 知识检索（**41 条常识问答 / 20 类意图、每类 ≥ 8 句样本**），全不命中才兜底。话术一律以**先接住用户**为准：倾诉、说自己在做什么、求建议时先回应用户的内容，心情/饱食/精力只在被问到宠物时播报。它会记住你本轮说过的**名字和正在做的事**（仅会话内，不写盘不上传）；听不懂时不复读你的原话，而是给出 3 句真能答的问法当路标（逐轮轮换）。分词优先用 jieba，缺失时自动退回内置 bigram，行为不变。
- **AI 大脑（可选增强）**：右键 → 设置，填 `Base URL` / `API Key` / `Model`（任何 OpenAI 兼容服务），点「测试连接」再保存，即切回真正的流式大模型。API Key 只存用户目录 `~/.WisePet/config.json`，界面与日志一律脱敏。

### 接入 AI 服务：地址怎么填

客户端说的是 **OpenAI 兼容协议**（`POST {Base URL}/chat/completions`，`Authorization: Bearer`），所以 Base URL 必须填厂商的 OpenAI 兼容地址，通常以 `/v1` 或 `/v4` 结尾：

| 厂商 | Base URL | Model 示例 |
|------|----------|----------------|
| 智谱 GLM（有免费档） | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |

> 地址按各家公开文档整理；其中**智谱 `glm-4-flash` 已实测（测试连接通过、流式拿到回复）**，其余行未实跑验证。Model 名建议从厂商控制台直接复制。

> **填错了会怎样**：以前会“没反应”。厂商网关常把错误包在 **HTTP 200** 里返回（如误填智谱的 `https://open.bigmodel.cn/api/anthropic`，实际返回 `{"code":500,"msg":"404 NOT_FOUND"}`），旧实现只看状态码，解出零段文本就静默。现在这种情况归入新的 `protocol` 错误类：聊天气泡会说“连上了，但它回的话我读不懂——Base URL 请填 OpenAI 兼容地址”，测试连接也会直接报出网关原文，日志里留响应体前 200 字供定位（PRD v1.5 C7）。

> 某模型过载时会返回 429（实测 `GLM-4.7-Flash` 就是“该模型当前访问量过大”，气泡提示“太热情啦”），换个模型或稍后再试。人设成熟度主要看设置里的 **System Prompt**，比换模型影响更大。

### 怎么看这条是谁答的

两种模式的气泡长得一样（都是逐段上屏），分辨靠日志文件 `~/.WisePet/logs/WisePet.log`（启动时也会打印具体路径）。每问一句至少留一行，带 `source=` 与同一序号。下面三行逐字来自离线脚本实跑输出（网关是假的，故 `first_chunk`/`elapsed` 接近 0；本地那行的 0.69s 是首轮分词词典加载的一次性成本）：

```log
chat #1 source=ai request model=glm-4-flash endpoint=https://api.test/v1/chat/completions msgs=2 prompt_chars=10
chat #1 source=ai done chunks=5 reply_chars=27 first_chunk=0.00s elapsed=0.00s preview=你好呀，我是通义千问，很高兴见到你！↘今天过得怎么样？
chat #2 source=local intent=stress reply_chars=40 elapsed=0.69s preview=我在。这件事不用现在就解决，你先骂两句也行，本地小脑连网都不上，真的没有第三人。
```

- `source=ai` 是远端模型答的，`source=local` 是本地小脑答的（后者不发任何网络请求）；`intent=` 告诉你是哪条路由答的（`greet`/`stress`/`knowledge`/`fallback` …）。
- `preview=` 是回复前 60 字（换行折成 `↘`），只用于辨认是哪一条；**聊天全文看右键「历史对话…」打开的记录窗**，日志里仍只有 60 字摘要。
- **聊天记录按天保存在本机**：`~/.WisePet/chat/YYYY-MM-DD.json`，你输入的原文与完整回复都会明文落盘（这是"聊天记录保存"的代价，FR v1.8 R5 已如实登记）；不想要就删对应日期的文件，程序会自动备份损坏文件、不会崩。右键「历史对话…」打开记录窗，顶部可切换有记录的历史日期，**底部可直接接着聊**（打开时收起单独输入条）；启动会自动读回当天（窗口本身不会自动弹出），并把最近的对话回灌模型上下文，所以**重启后宠物记得之前说过的话**（FR v1.10 C16/C19）。
- 启动与保存配置时各有一行锚点 `chat brain=ai|local …`，用来确认刚改的配置是否真的生效了。
- `first_chunk` 是从发起到第一段内容上屏的等待；没拿到任何内容时记 `n/a`（而不是 `0.00s`）。

配置与宠物状态持久化在 `~/.WisePet/`（`config.json`、`pet_state.json`），聊天记录在 `~/.WisePet/chat/YYYY-MM-DD.json`（按天、只追加），退出与周期落盘，下次启动按离线时长补算衰减（不会饿死）。从旧版 `ai-bot` 升级时，首次启动会把 `~/.ai-bot/` 下的配置、状态与日志复制到新目录（同名文件不覆盖），旧目录保留作备份。

## 目录结构

```
main.py                  组装根（唯一允许在运行时链接 Qt 的非 ui 入口）
src/WisePet/ui/           Qt 控件：宠物窗、贴纸风气泡、单行输入条（chat_input）、可继续聊的历史窗（chat_window）、动画器、设置窗
src/WisePet/app/          编排：ChatService、ChatHistory（会话记录门面）、LocalResponder（nlp/corpus/dialog_engine 三层统计小脑）、PetController、ReminderService、EventBus
src/WisePet/domain/       纯逻辑零依赖：Pet、Vitals、状态机、Position、事件（含会话状态/记录事件）、ChatRecord 与按天键、配置模型、端口
src/WisePet/infra/        仓储与网关：JSON 存储、按天聊天记录仓储、OpenAI 兼容客户端、按皮肤加载素材、单实例、平台工具
assets/pet/{skin}/{action}/  3 皮肤 × 8 动作 × 4 帧共 96 帧 256px 透明 PNG（idle/walk/sad/hurt/think/eat/speak/sleep）
tools/gen_pet_assets.py  离线素材生成器（QPainter chibi（Q 版）参数化绘制，不参与运行时）
tools/package.py         双平台打包程序（PyInstaller → zip / NSIS / dmg）
.github/workflows/build.yml  双 runner 出 Windows + macOS 安装包
tests/                   unit / integration / ui 三层
docs/FR · docs/NFR       需求与技术设计（版本化）
AGENT.md                 Agent 开发规约、关键决策记录、验证方式
```

## 开发与测试

```powershell
& ".venv\Scripts\python.exe" -m pip install -r dev-requirements.txt
& ".venv\Scripts\python.exe" -m pytest              # 当前基线 926 项全绿
& ".venv\Scripts\python.exe" -m pytest -q --cov     # 覆盖率门槛 fail_under = 90（实测 99.22%）
```

- 测试无需显示环境：`tests/conftest.py` 自动置 `QT_QPA_PLATFORM=offscreen`。
- 需要真分词才能成立的泛化用例用 `pytest.importorskip("jieba")` 标注，未装 jieba 的机器上其余用例行为一致。
- 重新生成桌宠素材：`python tools/gen_pet_assets.py --skin all`（三皮肤 96 帧；缺省只重生 cat 32 帧）。笔触随机数以皮肤+动作+帧号为种子，同参数重跑逐字节一致。`--preview` 输出 `tmp/preview-{skin}.png` 总览图供目检，属临时产物。
- 临时/中间产物一律放 `tmp/`（已 gitignore），用完立即清理。

## 打包与分发

一个命令在当前系统产出该平台的安装/分发包（**PyInstaller 不支持交叉编译**：Windows 包在 Windows 打，macOS 包在 macOS 打；一次拿双平台产物用下面的 CI）。

```powershell
& ".venv\Scripts\python.exe" -m pip install -r dev-requirements.txt
& ".venv\Scripts\python.exe" tools/package.py            # Windows
```
```bash
.venv/bin/python -m pip install -r dev-requirements.txt
.venv/bin/python tools/package.py                       # macOS
```

| 平台 | 产物（`dist/`） | 说明 |
|------|----------------|------|
| Windows | `WisePet-<版本>-windows-x64.zip` | 解压即用的便携包 |
| Windows | `WisePet-<版本>-windows-x64-setup.exe` | NSIS 安装程序：检测到 `makensis` 时生成（`choco install nsis`） |
| macOS | `WisePet-<版本>-macos-<arm64\|x64>.dmg` | 打开后拖进 Applications 即用 |

- 版本号取自 `pyproject.toml`；图标由 `assets/pet/cat/idle/01.png` 现场转成 ico / icns（纯标准库，不引入 Pillow）。运行期素材 `assets/` 随包放在 exe 同级（Windows）或 `.app/Contents/Resources/`（macOS），皮肤目录仍可增删。
- 常用参数：`--dry-run`（只打印命令）、`--no-installer`（只冻结，便于本地验证）、`--console`（保留控制台排查启动失败）、`--clean`（清理临时与旧产物）、`--target windows|macos`。
- 临时文件（spec、图标、NSIS 脚本、dmg 暂存）在 `tmp/packaging/`，成功即清理；最终产物留在已 gitignore 的 `dist/`。
- macOS 包默认**未签名未公证**：本机自用需 `codesign` 或右键→打开；分发时设置 `WISEPET_CODESIGN_IDENTITY` 让脚本签名，再自行走 Apple 公证流程。
- 双平台一键出包：`.github/workflows/build.yml` 在 `windows-latest` 与 `macos-latest` 两个 runner 上并行调用本脚本，产物作为 artifacts 上传（push `v*` 标签或手动触发）。

## 文档

- **设置手册**：[docs/设置手册.md](docs/设置手册.md) —— 每个设置项的作用、默认值与常见问题（多模型方案 / 提醒 / 新闻 / 鼓励推送 / 附件 / 一句话定时提醒）
- 需求（PRD）：[docs/FR/README.md](docs/FR/README.md) —— v1.0 首版、v1.1 形象正式化 + 离线可聊、v1.2 多皮肤 + 画风写实 + 统计 NLP 小脑、v1.3 画风改 Q 版可爱 + 兜底话术卫生、v1.4 本地小脑对话质量（先接住用户）、v1.5 AI 链路失败必须可见、v1.6 回复来源必须可观测、v1.8 会话窗与按天记录、v1.9 聊天入口拆分、v1.10 记忆恢复 + 浮窗跟随 + 历史窗继续聊 + 气泡风格统一、v1.11 隐藏后从系统托盘召回、v1.12 设置窗视觉统一、v1.13 设置窗收尾与拖动修复、v1.14 滚轮只滚页面不改值、v1.15 轮询节奏修复、v1.16 低落状态死锁修复、v1.17 全状态自主轮播
- 技术设计：[docs/NFR/README.md](docs/NFR/README.md) —— v1.0 方案、v1.1 实现偏差登记、v1.2 素材与本地聊天落地、v1.3 皮肤管线与统计小脑落地、v1.4 chibi 生成器与话题卫生落地、v1.5 问句优先路由与语料规模、v1.6 protocol 错误类与响应体检视、v1.7 来源日志字段口径、v1.9 会话状态机与按天记录、v1.10 入口拆分与贴纸风重绘、v1.11 跟随/继续聊/气泡卡片化落地、v1.12 系统托盘召回入口落地、v1.13 设置窗视觉统一落地、v1.14 设置窗收尾与拖动修复落地、v1.15 滚轮转发落地、v1.16 轮询节奏落地、v1.17 低落死锁落地、v1.18 全状态轮播落地
- 项目规约：[docs/规约.md](docs/规约.md)；Agent 约束与决策：[AGENT.md](AGENT.md)

## 已知限制

- macOS 全屏勿扰检测未实现（恒视为非全屏，提醒不静默），按 PRD 接受降级
- 未做开机自启、代码签名与公证；皮肤只能在**已安装的三套**里选，不在线下载
- 形象为 **Q 版可爱（chibi）插画**：大头大眼短肢、低饱和奶油配色，三皮肤靠剪影（耳形/尾形）+ 花纹 + 配色区分；不是写实品种还原（爪垫、胡须根部阴影等未做）
- 本地小脑是**统计方法 + 规则**，不是生成式模型：上限是“不智障”而不是“聪明”。能泛化同义说法、能答语料内知识，超出 41 条知识 / 20 类意图覆盖即兜底；真要对答质量满意请接 AI 链路（设置里填 Base URL / Key / Model）；时间类回答只给“时段 + 整点”的模糊表述，语料不含需联网核实的事实（天气、新闻、汇率）
- cat 的 10 个动作（60 帧）直接由 `docs/images/cat` 的 10 张 2×3 分镜图**等分切出**，保留原图全部画面（食盆、zzz、泪滴、张嘴、表情）；为满足素材契约只把白底抠成透明、擦掉 AI 水印、等比居中到 256×256 RGBA，6 帧共用一份包围盒保证不抖。dog / monkey 仍是旧素材
- 小脑的**话题跟踪**（`last_topic`）重启即忘（PRD v1.2 §4 列为范围外），但按天落盘的聊天记录会回灌模型上下文，所以宠物仍记得之前聊过的话（FR v1.10 C16）；加载 jieba 后常驻内存约 +70MB，没装也能完整使用
- 真机手工清单（12 项，含本地小脑八句口测、AI 链路三句与日志来源目检，见 AGENT.md §4）与 CPU/内存/24h 稳定性读数仍待人工采集
