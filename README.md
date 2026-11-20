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
| 看日志确认状态 | `~/.WisePet/logs/WisePet.log` 里每次状态切换都有 `behavior IDLE -> WALKING` 这样的行；告警与错误另存 `WisePet-error.log`，排障直接看它 |
| 设置 → 皮肤 | 贴纸风设置窗（圆角卡片、整条标题栏可拖、分区可滚动、滚轮只滚页面不改值）；在**橘猫 / 柴犬 / 小猴**间切换，保存后不重启立即换形象，说话自称与语气词同步 |
| 设置 →「系统配置」 | 与宠物本身无关的运行参数集中在这里：日志级别、单文件分割上限、日志保留天数、错误档开关。**保存后立即热重建日志，无需重启**；以后新增的系统级开关也放这个分区 |
| 弹窗突然都不见了 | 先右键 →「一键场景包」→「健康作息」一键恢复；也可到设置 →「体验与隐私」看打扰预算。「深度工作」会关掉新闻/鼓励/周报并把额度压到 6 条/天（同类 2 条），额度用尽当天会安静下来 |
| 右键 →「命令面板…」 | 一处搜到并执行 16 类能力：聊天、历史、搜记录、排提醒、番茄钟、切场景、导出聊天记录、本机数据占用、陪伴指标、我记得你什么、最近执行的操作、设置、统计等；↑↓ 选择、回车执行、Esc 关闭 |
| 托盘菜单第一行状态 | 常驻显示当前场景 / 新闻鼓励周报开关 / 今日推送用量 / 免打扰，不用等出问题才知道「为什么安静」 |
| 命令面板 →「导出聊天记录」 | 把全部聊天写到 `~/.WisePet/chat-export.md`；同面板还能按天删除、看本机占用、一键清空埋点 |
| 设置 → 宠物参数 →「活泼度」 | 安静 / 正常 / 活泼，只缩放自主行为间隔，不改状态机与全状态轮播 |
| 设置 → 宠物参数 →「进入特效」 | 宠物登场动画，7 种主体风格共享同一套骨架（汇聚能量环 → 扫描环 → 主体 → 白核爆闪 → 淡入）：自动 / 随机 / 粒子汇聚 / 粒子爆开 / 碎片拼合 / HUD 扫描 / 能量揭幕 / 能量波纹 / 聚焦光束；统一 950ms、纯程序绘制、零素材依赖 |
| 宠物登场特效「随触发」且骨架统一 | 选「自动」时按场景：开机→粒子汇聚、唤聊→HUD 扫描、切场景→能量揭幕、提醒/番茄→粒子爆开；**「从托盘恢复」每次随机**；另有「随机」模式每次都随机。宠物已可见时触发也会播放对应特效作反馈（不再整段跳过）。7 种共享最初开机动画的三段式骨架，不再各自硬起硬停 |
| 设置 → 体验与隐私 →「超限策略」 | 额度用完可选「整天安静」或「降频」（每 N 次拦截放行 1 条），不再一律静默 |
| 闲置 | 随机走动/发呆，夜间精力低会睡觉；心情低落会主动求助 |
| 提醒 | 默认喝水 45 min、久坐 60 min；全屏应用时静默顺延 |
| 首次启动 | 三步引导卡：点一下真的能聊、试一条 1 分钟提醒真的会响；只看一次，右键「再看一次新手引导」可重开 |
| 右键 →「一键场景包」 | 深度工作 / 健康作息 / 每日复盘，一键把番茄钟、提醒间隔、新闻鼓励、打扰预算打包换好；**不碰你的 API Key 和人设** |
| 右键 →「免打扰：开/关」 | 一键暂停全部主动推送；你主动发起的聊天回复与工具反馈不受影响。设置里还能配每日条数与单来源上限 |
| 右键 →「历史对话…」→ 搜索框 | 跨所有日期全文检索聊天记录，按时间倒序，条数可在设置里调 |
| 等级 ≥ 4 | 宠物变大一点、头顶多一枚等级徽章；成长体系关掉则不叠加 |
| 让模型写文件 / 生成 PPT | 先弹完整预览（路径 + 将写入内容），可设「每次确认 / 始终允许 / 一律拒绝」；写完后右键「撤销上一次写文件」可还原 |
| 告诉它「我叫…」「我在写…」 | 名字与近况记进本机 `memory.json`，重启后仍认得；也会拼进系统提示词。设置里可一键关闭 |

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

两种模式的气泡长得一样（都是逐段上屏），分辨靠日志文件 `~/.WisePet/logs/WisePet.log`（启动时也会打印具体路径；WARNING 及以上另存 `WisePet-error.log`）。每问一句至少留一行，带 `source=` 与同一序号。下面三行逐字来自离线脚本实跑输出（网关是假的，故 `first_chunk`/`elapsed` 接近 0；本地那行的 0.69s 是首轮分词词典加载的一次性成本）：

```log
chat #1 source=ai request model=glm-4-flash endpoint=https://api.test/v1/chat/completions msgs=2 prompt_chars=10
chat #1 source=ai done chunks=5 reply_chars=27 first_chunk=0.00s elapsed=0.00s preview=你好呀，我是通义千问，很高兴见到你！↘今天过得怎么样？
chat #2 source=local intent=stress reply_chars=40 elapsed=0.69s preview=我在。这件事不用现在就解决，你先骂两句也行，本地小脑连网都不上，真的没有第三人。
```

- `source=ai` 是远端模型答的，`source=local` 是本地小脑答的（后者不发任何网络请求）；`intent=` 告诉你是哪条路由答的（`greet`/`stress`/`knowledge`/`fallback` …）。
- `preview=` 是回复前 60 字（换行折成 `↘`），只用于辨认是哪一条；**聊天全文看右键「历史对话…」打开的记录窗**，日志里仍只有 60 字摘要。
- **聊天记录存在本机数据库**：`~/.WisePet/wisepet.db`（SQLite 单文件，`sqlite3` 标准库，零新依赖）。你输入的原文与完整回复都会明文落盘（这是"聊天记录保存"的代价，FR v1.8 R5 已如实登记）。右键「历史对话…」打开记录窗，顶部可切换有记录的历史日期，搜索框可**跨所有日期全文检索**，**底部可直接接着聊**（打开时收起单独输入条）；启动会自动读回当天（窗口本身不会自动弹出），并把最近的对话回灌模型上下文，所以**重启后宠物记得之前说过的话**（FR v1.10 C16/C19）。旧的 `chat/YYYY-MM-DD.json` 在首次启动时一次性导入，成功后归档到 `chat/legacy/*.json.migrated`（**不删除**）；想重新导入，把这些文件移回 `chat/`、去掉 `.migrated` 后缀、删掉 `wisepet.db` 再启动即可。数据库损坏也会自动备份并重建，不会崩。
- 启动与保存配置时各有一行锚点 `chat brain=ai|local …`，用来确认刚改的配置是否真的生效了。
- `first_chunk` 是从发起到第一段内容上屏的等待；没拿到任何内容时记 `n/a`（而不是 `0.00s`）。
- **日志治理**：主档 `WisePet.log`（INFO+，完整时间线）与错误档 `WisePet-error.log`（WARNING+）各自按 **单文件上限** 分割，**跨天也会封存一次**；滚动备份按**保留天数**清理，超期自动删除，正在写的文件不动。两档都过密钥脱敏，API Key / Bearer 不会明文落盘。**级别 / 上限（默认 10 MiB）/ 保留天数（默认 3 天）/ 分档开关**都可以在设置 →「系统配置」里改，保存后立即生效。

配置与宠物状态持久化在 `~/.WisePet/`（`config.json`、`pet_state.json` 等**仍是 JSON，可读可手改，保持不变**），只有聊天记录迁到了 `~/.WisePet/wisepet.db`（SQLite 单文件，真追加、可按天/全文检索），退出与周期落盘，下次启动按离线时长补算衰减（不会饿死）。从旧版 `ai-bot` 升级时，首次启动会把 `~/.ai-bot/` 下的配置、状态与日志复制到新目录（同名文件不覆盖），旧目录保留作备份。

## 目录结构

```
main.py                  组装根（唯一允许在运行时链接 Qt 的非 ui 入口）
src/WisePet/ui/           Qt 控件：宠物窗、贴纸风气泡、单行输入条（chat_input）、可继续聊的历史窗（chat_window）、动画器、设置窗
src/WisePet/app/          编排：ChatService、ChatHistory（会话记录门面）、LocalResponder（nlp/corpus/dialog_engine 三层统计小脑）、PetController、ReminderService、EventBus
src/WisePet/domain/       纯逻辑零依赖：Pet、Vitals、状态机、Position、事件（含会话状态/记录事件）、ChatRecord 与按天键、配置模型、端口
src/WisePet/infra/        仓储与网关：JSON 存储、OpenAI 兼容客户端、按皮肤加载素材、单实例、平台工具、产品层本地仓储（product_store）
src/WisePet/infra/sqlite_chat_repo.py  聊天记录仓储：~/.WisePet/wisepet.db（SQLite，含旧 JSON 一次性导入与损坏自愈）
src/WisePet/infra/chat_log_repo.py     旧按天 JSON 仓储：仅用于迁移与回退，运行时不再写入
src/WisePet/domain/product.py  产品层纯逻辑：场景包预设 / 成长外观 / 行为因果 / 主动推送判定
src/WisePet/app/{quiet_budget,long_term_memory}.py  打扰预算与长期事实记忆
src/WisePet/ui/{onboarding_dialog,action_confirm}.py  首次引导卡与写盘预览确认框
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
& ".venv\Scripts\python.exe" -m pytest              # 当前基线 1350 项全绿
& ".venv\Scripts\python.exe" -m pytest -q --cov     # 覆盖率门槛 fail_under = 90（实测 94.37%）
& ".venv\Scripts\python.exe" tools/verify_assets.py  # 校验 3 皮肤 × 10 动作 × 6 帧素材规格
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
- 常用参数：`--dry-run`（只打印命令）、`--no-installer`（只冻结，便于本地验证）、`--console`（保留控制台排查启动失败）、`--clean`（清理临时与历史解压目录）、`--verify`（只校验已有产物：exe / 素材 / 文件数 / 体积）、`--target windows|macos`。
- 打包时会裁剪未使用的 Qt 子模块、PNG 免二次压缩，并在结束前强制自检；CI 只上传当次生成的 zip / setup.exe / dmg，不再把历史解压目录当 artifact。
- 临时文件（spec、图标、NSIS 脚本、dmg 暂存）在 `tmp/packaging/`，成功即清理；最终产物留在已 gitignore 的 `dist/`。
- macOS 包默认**未签名未公证**：本机自用需 `codesign` 或右键→打开；分发时设置 `WISEPET_CODESIGN_IDENTITY` 让脚本签名，再自行走 Apple 公证流程。
- 双平台一键出包：`.github/workflows/build.yml` 在 `windows-latest` 与 `macos-latest` 两个 runner 上并行调用本脚本，产物作为 artifacts 上传（push `v*` 标签或手动触发）。

### 在 GitHub Actions 上打 Mac 包（签名 / 公证能力暂缓启用）

> **当前状态（用户要求「先不启用密钥」）**：CI **不注入任何 secrets、不导入证书**，
> 双平台都走纯打包路径，产出**未签名** `WisePet-*-macos-*.dmg`（右键 → 打开可用）。
> 签名/公证代码仍保留在 `tools/package.py`，以后有 Apple Developer 账号再按下表接回即可。

`macOS` 包必须在 macOS 上打（PyInstaller 不支持交叉编译），这正是 GitHub Actions 的
`macos-latest` runner 能替你做的事：推送 `v*` 标签触发 CI，Windows 与 macOS 各出一个包。
下表是**将来重新启用签名/公证时**需要的凭据，当前 CI 未使用：

| Secret | 用途 | 不填时 |
|---|---|---|
| `WISEPET_CODESIGN_IDENTITY` | `codesign` 身份，如 `Developer ID Application: 你的名 (TEAMID)` | 不签名 |
| `MACOS_CERTIFICATE` | 含私钥的 p12 证书经 base64 后的字符串 | 跳过证书导入 |
| `MACOS_CERTIFICATE_PWD` | 上述 p12 的密码 | — |
| `WISEPET_NOTARIZE` | 必须填字面量 `1` 才触发公证（避免误提交空凭据） | 不公证 |
| `APPLE_ID` | 你的 Apple ID 邮箱 | — |
| `APPLE_APP_PASSWORD` | Apple 专用 app 密码（非登录密码） | — |
| `APPLE_TEAM_ID` | 开发者团队 ID | — |

> 前提：你需有 **Apple Developer 账号**并完成 `Developer ID Application` 证书 + 公证权限
> （这是 Apple 的硬性要求，CI 无法代替你申请）。拿到账号后，凭据只存在你仓库的 secrets 里，
> 不会进入源码或日志；`package.py` 的公证步骤只在 `WISEPET_NOTARIZE=1` 且三项 Apple 凭据齐全时才调用 `xcrun`，否则静默跳过。
>
> **历史坑（重新启用时务必注意）**：GitHub Actions 的 `if:` **不能引用 `secrets`**
> （报 `Unrecognized named-value: 'secrets'`，整个 workflow 失效）；证书存在性判断要放 shell，
> `if:` 只用 `matrix.target`。另外 macOS runner 是 BSD `base64`，不支持 `--decode`，
> 证书解码得走 Python。`tests/unit/test_workflow_expressions.py` 有静态护栏锁住这些约束，
> 以及「当前 CI 不含任何 secrets 注入」这条临时决策。
>
> **macOS 产物自检阈值**：`.app` 是 Qt framework 目录树，文件数/体积天然数倍于 Windows
> onedir，因此 `verify_bundle()` 按平台取阈值（macOS 4000 文件 / 600MB，Windows 800 / 160MB），
> 并排除 framework 符号链接避免重复计数。打包失败时 CI 会打印 `dist/` 清单与自检结果。

## 自动更新配置（可选）

程序内置了自动更新检查，但**默认关闭**——要用需两步：

1. **发布 Release**：把代码推到 GitHub，打一个 `v*` 标签（如 `v3.4.0`）。`.github/workflows/build.yml` 会自动出 Windows zip/setup.exe 与 macOS dmg 并发布到该 tag 的 Release（详见上文「打包」章节）。
2. **在客户端配置**：设置 → 系统配置 → 勾选「启动时检查新版本」，「更新源」填你的仓库 `owner/repo`（如 `yourname/WisePet`），保存。也可点「立即检查更新」或命令面板搜「检查更新」。

发现新版本时会提示；命令面板「安装更新」会下载对应平台的安装包并**打开它**——由你/系统完成安装（程序不会静默替换正在运行的文件），装完重启即可。未配置更新源或网络不通时安静跳过，不影响断网使用。

## 文档

- **设置手册**：[docs/设置手册.md](docs/设置手册.md) —— 每个设置项的作用、默认值与常见问题（多模型方案 / 提醒 / 新闻 / 鼓励推送 / 附件 / 一句话定时提醒）
- **产品规划**：[docs/产品规划.md](docs/产品规划.md) —— v2.x 路线图、P0~P3 结清记录、商业化与风险护栏
- **意图协议**：[docs/意图协议.md](docs/意图协议.md) —— 工具执行层的 JSON 动作协议、三级权限 / 确认 / 撤销 / 审计、扩展步骤
- 需求（PRD）：[docs/FR/README.md](docs/FR/README.md) —— v1.0 首版、v1.1 形象正式化 + 离线可聊、v1.2 多皮肤 + 画风写实 + 统计 NLP 小脑、v1.3 画风改 Q 版可爱 + 兜底话术卫生、v1.4 本地小脑对话质量（先接住用户）、v1.5 AI 链路失败必须可见、v1.6 回复来源必须可观测、v1.8 会话窗与按天记录、v1.9 聊天入口拆分、v1.10 记忆恢复 + 浮窗跟随 + 历史窗继续聊 + 气泡风格统一、v1.11 隐藏后从系统托盘召回、v1.12 设置窗视觉统一、v1.13 设置窗收尾与拖动修复、v1.14 滚轮只滚页面不改值、v1.15 轮询节奏修复、v1.16 低落状态死锁修复、v1.17 全状态自主轮播、v1.18 输入条空闲隐藏+截图问小墨、v1.19 TTS/系统通知/周报/成长、v1.20 开机问候与出场特效、v1.21 P1/P2 产品能力、v1.22 聊天记录迁 SQLite、v1.23 日志分级与保留期、v1.24 系统配置分区、v1.25 P0/P1/P2 路线图落地、v1.26 macOS 签名公证接线 + 登场特效体系 + 出场动画整体优化、v1.28 陪伴轴①（主动引用 / 情境化 / 可干预 / 久别重逢）、v1.29 轴② A 类（托付任务 / 本地资料索引 / 事件主动 / 可遗忘时间线 / 惦记清单）、v1.30 原生 function calling + agent 工具循环、v1.31 记忆体系升级（会话摘要 / 实体记忆 / 语义检索 / 本地身份）、v1.32 身份热切换 / 家庭共享 AI + 配额 / 删除身份 / 检索策略修正
- 技术设计：[docs/NFR/README.md](docs/NFR/README.md) —— v1.0 方案、v1.1 实现偏差登记、v1.2 素材与本地聊天落地、v1.3 皮肤管线与统计小脑落地、v1.4 chibi 生成器与话题卫生落地、v1.5 问句优先路由与语料规模、v1.6 protocol 错误类与响应体检视、v1.7 来源日志字段口径、v1.9 会话状态机与按天记录、v1.10 入口拆分与贴纸风重绘、v1.11 跟随/继续聊/气泡卡片化落地、v1.12 系统托盘召回入口落地、v1.13 设置窗视觉统一落地、v1.14 设置窗收尾与拖动修复落地、v1.15 滚轮转发落地、v1.16 轮询节奏落地、v1.17 低落死锁落地、v1.18 全状态轮播落地、v1.19 C28/C29 补登、v1.20 C30~C33、v1.21 C34~C37、v1.22 P1/P2 落地、v1.23 聊天记录迁库、v1.24 日志治理落地、v1.25 系统配置落地、v1.26 P0/P1/P2 落地、v1.27 签名公证 + 登场特效 + 出场动画统一骨架、v1.28 陪伴轴①落地（惦记 / 情境 / 可干预 / 久别）、v1.29 轴② A 类五项落地、v1.30 function calling + agent 工具循环、v1.31 记忆体系升级 + 本地多用户、v1.32 身份热切换 + 家庭 AI 治理
- 项目规约：[docs/规约.md](docs/规约.md)；Agent 约束与决策：[AGENT.md](AGENT.md)

## 已知限制

- macOS 全屏勿扰检测未实现（恒视为非全屏，提醒不静默），按 PRD 接受降级
- 未做开机自启、代码签名与公证；皮肤只能在**已安装的三套**里选，不在线下载
- 形象为 **Q 版可爱（chibi）插画**：大头大眼短肢、低饱和奶油配色，三皮肤靠剪影（耳形/尾形）+ 花纹 + 配色区分；不是写实品种还原（爪垫、胡须根部阴影等未做）
- 本地小脑是**统计方法 + 规则**，不是生成式模型：上限是“不智障”而不是“聪明”。能泛化同义说法、能答语料内知识，超出 41 条知识 / 20 类意图覆盖即兜底；真要对答质量满意请接 AI 链路（设置里填 Base URL / Key / Model）；时间类回答只给“时段 + 整点”的模糊表述，语料不含需联网核实的事实（天气、新闻、汇率）
- cat 的 10 个动作（60 帧）直接由 `docs/images/cat` 的 10 张 2×3 分镜图**等分切出**，保留原图全部画面（食盆、zzz、泪滴、张嘴、表情）；为满足素材契约只把白底抠成透明、擦掉 AI 水印、等比居中到 256×256 RGBA，6 帧共用一份包围盒保证不抖。dog / monkey 仍是旧素材
- 小脑的**话题跟踪**（`last_topic`）重启即忘（PRD v1.2 §4 列为范围外），但按天落盘的聊天记录会回灌模型上下文，所以宠物仍记得之前聊过的话（FR v1.10 C16）；加载 jieba 后常驻内存约 +70MB，没装也能完整使用
- 真机手工清单（12 项，含本地小脑八句口测、AI 链路三句与日志来源目检，见 AGENT.md §4）与 CPU/内存/24h 稳定性读数仍待人工采集
- 首次引导只弹一次（标记 `~/.WisePet/onboarding.done`）；行为因果驱动用的是本机可观测的「时段 / 专注 / 勿扰」；**天气是可选联网增强**（设置→体验与隐私开启，国内免 Key 源），日程 API 未接；场景包是一次性配置预设，不会随日程自动切换
- 写盘类操作只保留「最近一次」可撤销快照，进程重启后清空；覆盖写按原始字节还原，原文件读不出来时拒绝撤销
- 聊天记录已迁 SQLite，**其余 10 类数据仍是 JSON**；迁移后不再产生按天文件，「删某天文件即删记录」的旧做法不再适用（要清空请删 `wisepet.db`）。旧文件只归档不删除，可随时重新导入
- **本地埋点已落地**（只记事件名 + 按天计数，不上传、可清空、只保留近 30 天）；**自动更新已落地**（后台检查 GitHub Releases + 下载打开安装包，需配置 `owner/repo`，见下节）；天气能力已落地（聊天问答 + 可选定时播报）；macOS 代码签名与公证的证书/真机、AI 素材补图、日历 API、第三方 action 沙箱仍未做；皮肤工坊 / 插件 / STT / 手机伴侣属 P3，条件触发。见 `docs/产品规划.md`、`FR v1.25` §2 与 `FR v1.35`
- 三套皮肤的**规格**已通过 `tools/verify_assets.py`（10 动作 × 6 帧 / 256×256 RGBA）；但 dog / monkey 仍是旧素材，画风与 cat 同批 AI 图尚未统一
