# WisePet 技术设计 v1.35

- 版本：v1.35
- 状态：已实现（待评审）
- 对应需求：FR v1.35（工具准入修正 / 天气完整化 / 定位精度 / 贴纸风弹窗）

## 1. 模块清单

| 模块 | 职责 |
|---|---|
| `domain/model_capability.py` | `tools_allowed` 改为只看总开关（撤回按档位禁用）；`capability_hint` 改为操作指引 |
| `domain/weather.py` | WMO code→中文、`format_report` 生成 markdown 卡片、`ensure_card_format` 格式兜底、`care_line`/`context_score` 同前 |
| `infra/weather_api.py` | 双源网关：国内源（`t.weather.itboy.net` + 40 城市代码表）优先，Open-Meteo 备源；`WeatherSnapshot` 扩湿度/风力/时段温度/时间字段 |
| `app/weather_service.py` | 后台抓天气 + 缓存 + 间隔调度（`interval_min`）+ 坏天气每日一次额外提醒 |
| `app/agent_tools.py` | 新增 `get_weather` 工具；`nearby_pois` 加区域名地理编码 |
| `app/agent_loop.py` | `_final_with_weather_card` 格式兜底（丢 markdown 时用工具卡片替换） |
| `app/event_triggers.py` | 天气播报独立化（不占 `event_daily_limit`、不塞当日 fired）+ `source="weather"` |
| `domain/config_model.py` | `WeatherSettings` 改为 `interval_min`/`bad_weather_alert`（兼容旧键） |
| `domain/product.py` | `PUSH_SOURCES` 加 `weather`（走 QuietBudget 白名单） |
| `ui/input_dialog.py` | 新增 `ChoiceDialog` 贴纸风三选一 |
| `ui/settings_dialog.py` | 天气播报设置改为间隔控件 + 坏天气开关 |

## 2. 关键实现要点

### 2.1 工具准入修正（教训）

v1.34 把 lite 档（含 `glm-4-flash`）的整个工具循环禁掉，真机直接退化成「我查不到」——而 v1.30/v1.33 的联网工具本就是它驱动的。根因是把「弱模型会编造」的对策选成了「剥夺工具」。修正：`tools_allowed` 只看 `tool_calling_enabled`；`capability_hint` 改为「先调工具拿真实结果、不许凭记忆编造」。教训登记：不靠猜模型能力决定给不给工具，靠工具结果注入 + 提示词约束。

### 2.2 双源天气与实测依据

本机实测（`python` 脚本逐个探测）：
```
open-meteo https: ConnectTimeout 握手超时 11.4s
open-meteo http:  ReadTimeout 17.3s
wttr.in:          CERTIFICATE_VERIFY_FAILED 自签证书
itboy(国内):      200 0.5s  {"status":200,"data":{"wendu":...,"forecast":[...]}}
```
网关 `fetch()` 依次试 `_from_itboy`（城市名按 `_CITY_CODES` 40 条表匹配代码）→ `_from_open_meteo`（geocoding→forecast），首个非空结果返回，全失败返回 None。国内源字段：`wendu`/`shidu`/`forecast[0].type|high|low|fx|fl|notice`/`cityInfo.updateTime`；中文天气文字经 `_TEXT_TO_CODE` 映射回 WMO code 复用 `domain.weather` 的描述与好天气判定。

### 2.3 天气卡片格式兜底（真机教训）

弱模型把 markdown 卡片揉成一段话（截图实证）。三层防御：
1. `format_report` 直接产出成品 markdown（`###`/`- **x**`/`> 引用`），降低模型组织成本；
2. 工具指令要求「原样输出，不改写为一整段」，系统提示词声明「天气卡片不受 100 字限制」；
3. `agent_loop._loop` 记住 `get_weather` 返回的卡片（`### ` 开头），最终回复若既无 `###` 又无 `**` 则 `ensure_card_format` 用卡片替换、保留模型口语附加话。因为 `end_stream(reply)` 用最终 reply 覆盖流式内容，兜底对气泡有效。

### 2.4 天气间隔调度与去重归属

`WeatherService.due_line(now, day_key_fn)` 纯读缓存：`_last_report_ts` 按 `interval_min` 计时，首次（0）立即报、`now - last >= interval` 再报；跨日（`day_key` 变化）重置为 0 允许早上第一句。`bad_weather_alert` 时坏天气按自然日额外一次。去重职责下沉到服务层——`EventTriggerService` 去掉 `RULE_WEATHER not in fired` 与 `remaining>0` 判定（否则一天只报一次、或被情境额度挤掉），改成「due_line 有内容才 publish」，source 改 `weather` 并加入 `PUSH_SOURCES` 走 `QuietBudget` 独立额度。

### 2.5 区域名地理编码

`AgentToolbox._extract_area(keyword)` 从「桂林路 咖啡」剥掉 `_PLACE_WORDS` 末尾类型词留「桂林路」；`_execute_nearby_pois` 在 `not info.precise` 且有区域名时调 `AmapPoiGateway.geocode(area, city)` 拿街道级坐标替代 IP 城市中心，并在结果里标注「按区域名精确定位」。gecode 失败静默用原 IP 坐标。

### 2.6 配置兼容

`WeatherSettings` 从 `report_hour/report_minute` 改 `interval_min`（下限 30、默认 180）：`from_dict` 检测旧键 `report_hour`/`daily_report_hour` 存在则默认间隔折算 24h；设置窗 load 同理把旧 `report_hour` 显示成 1440 分钟。

### 2.7 否决策

- 按模型档位剥夺工具（否决：误伤 glm-4-flash，真机退化成「查不到」）；
- 继续只推 Open-Meteo（否决：本机 forecast 端点稳定超时，国内源 0.5s 可用）；
- 把天气塞回情境额度（否决：每天 2 条被「回来/深夜」挤掉，用户看不到）；
- 天气播报也用 markdown 卡片（否决：主动提示应像喝水久坐一句口语，卡片只用于聊天问答）；
- 在 EventTriggerService 保留天气当日去重（否决：与间隔语义冲突，一天只能报一次）。

## 3. 测试与基线

- 新增/扩展 `test_weather.py`（双源、间隔、格式兜底、agent_loop 集成、配置兼容）与 `test_choice_dialog.py`（贴纸风三选一）；
- 相关测试 36 passed、设置窗 UI 36 passed、单元全量 855 passed、改动文件 lints 0；
- PNG 字节复现与 markdown 渲染测试受本机工具链/可选依赖影响，非功能回归（同 v1.34）。

## 4. 验证方式

- 聊天问「今天天气」→ 应得到带时间/气温/湿度的 markdown 卡片（弱模型也由兜底保证格式）；
- 设置 → 体验与隐私 → 打开「定时播报天气」、设间隔（如 60 分钟）→ 首次立即报、之后按间隔报；
- 命令面板「定位诊断」看定位来源/系统定位源/高德 Key；
- 位置授权弹窗为贴纸风三选一。
