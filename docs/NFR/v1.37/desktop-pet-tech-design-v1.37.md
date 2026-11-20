# WisePet 技术设计 v1.37

- 版本：v1.37
- 状态：已实现（待评审）
- 对应需求：FR v1.37（近期处境驱动的「真正自然的主动」）

## 1. 模块清单

| 模块 | 职责 |
|---|---|
| `domain/situation.py`（新） | 处境识别 / 时效判定 / 提示词引导 / 关怀话术，全纯函数 |
| `infra/situation_store.py`（新） | `~/.WisePet/situation.json` 原子写 + 损坏备份，最多 20 条 |
| `app/situation_service.py`（新） | 观察对话 → upsert 处境 → prompt_line + tick 主动关怀（当天去重、30s 节流） |
| `domain/config_model.py` | `CompanionSettings.situation_enabled`（默认开）+ from_dict 解析 |
| `main.py` | 构造服务；`_observe_summary` 扇出 observe；`_companion_depth_lines` 注入 prompt；tick 调 service.tick |

## 2. 关键实现要点

### 2.1 保守识别（宁可漏不可乱）

`_SITUATIONS` 是「类别 → 触发词 → 持续天数」表（加班 7 天 / 备考 14 天 / 生病 10 天
等）。`detect_situation` 只接受用户第一人称明确陈述，`_NEG_RE` 挡掉「吗？/你是不是/
别人/朋友/他她/假如/比如/段子/笑话」等转述与玩笑。**不接 AI 语义**——换说法可能漏，
但误判「你在加班」然后天天追问，体验比漏一次糟得多。守 AGENT「不猜情绪」：只记客观
事件，话术里也明确要求模型「不替他表达情绪」。

### 2.2 时效与去重

`Situation(kind, since, expires_at, last_cared_day)` 不可变值对象；`active(now)` 判时效。
`observe` 同类 upsert（先删旧同类再追加新的，避免叠加），`_save` 限 20 条。`tick`
每 30s 才读一次盘（低开销），过期的从存储清理；`last_cared_day` 按自然日保证主动关怀
每天最多一次，且**先标记再去重**（标记落盘后才 publish，避免同日重复弹）。

### 2.3 两个出口与打扰护栏

① `prompt_line(now)` 返回贴合引导，拼进 `_system_prompt()` 尾部（与人格锚/情绪语气/
可发现性并列）；② `tick` 在处境新鲜且当天未关心过时 publish `SpeakRequestedEvent(
source="system", title="我在呢")`——标题在既有 `PUSH_TITLES`，照旧走 `QuietBudget`
独立闸门，不新增推送通道。话术按新鲜度分两段（<3 天 / ≥3 天）。

### 2.4 观察扇出点复用

复用 ChatService 已有的 `summary_observer`（每轮对话带 role/content），在
`_observe_summary` 里追加 `self._situation.observe(role, content)`（hasattr 保护构造顺序：
ChatService 在 situation 服务之前构造，但 observe 只在运行时调用）。situation.observe
内部只处理 role=="user"，assistant 轮次自动跳过。

### 2.5 否决策

- 用 AI 语义理解处境（否决：联网+成本+可能误猜情绪，保守关键词零成本且可控）；
- 处境当永久事实记进 auto_facts（否决：处境有时效，混进长期记忆会过期不了）；
- 新增独立推送定时器（否决：复用既有 tick + QuietBudget，少一个通道少一类 bug）；
- 推测「你是不是压力大/难过」（否决：AGENT 明确不猜情绪，只认客观事件）。

## 3. 测试与基线

- 新增 `test_situation.py`（识别边界/时效/提示词/关怀话术/当天去重/配置开关），
  与相关回归合跑通过；组装根+设置窗 UI 104 passed；单元全量 885 passed；改动文件 lints 0。

## 4. 验证方式

- 聊「最近天天加班」，下一句回复应体贴、少娱乐；接下来几天首次启动会说一句加班关怀，
  每天最多一次；
- 说「我朋友在加班」「你是不是很累？」不应识别成自己的处境；
- 设置 → 陪伴进阶关闭「近期处境」后，不再注入 prompt 也不主动关怀。
