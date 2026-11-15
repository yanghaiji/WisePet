# WisePet 技术设计 v1.33

- 版本：v1.33
- 状态：已实现（待评审）
- 对应需求：FR v1.33（联网搜索 / 位置感知 / 周边搜索 / agent 真流式 / 防编造）

## 1. 模块清单

| 模块 | 职责 |
|---|---|
| `infra/web_search.py` | `WebSearchGateway`：四供应商（bing/duckduckgo/tavily/bocha）统一接口，`_HttpClient` 共享 httpx.Client（连接复用 + 合并信任链 + 跟随重定向） |
| `infra/geo_location.py` | `GeoLocator` 两级定位（系统精确优先 / IP 城市回落）+ `GeoInfo` 结构化结果 + `wgs84_to_gcj02` 坐标转换 |
| `infra/amap_api.py` | `AmapPoiGateway`：高德 v3 place/around 周边搜索，POI 解析与错误分类 |
| `ui/consent_bridge.py` | `LocationConsentBridge`：worker 线程阻塞等待主线程授权弹窗（信号排队 + threading.Event，120s 超时防挂死） |
| `ui/position_bridge.py` | `PositionBridge`：Qt Positioning 异步定位桥（worker 阻塞等主线程 `positionUpdated`/超时）；**QtPositioning 模块缺失时安全降级** |
| `app/agent_tools.py` | `web_search` / `get_location` / `nearby_pois` 三工具 schema 与执行器；`_location_denial()` 授权闸门两工具共用 |
| `infra/openai_client.py` | `chat_with_tools_stream`：SSE 流式 content 增量回调 + delta.tool_calls 分片聚合 |
| `app/agent_loop.py` | `AgentRunner.run(content_callback=...)`：每步走 `_chat_step`，流式被拒单次回落非流式 |
| `app/chat_service.py` | `_try_agent` 的 `on_content` 钩子：增量实时上屏（首次增量切 REPLYING），零增量回落拟流式 |
| `domain/config_model.py` | `WebSearchSettings`（enabled/provider/api_key/max_results/amap_key）与 `LocationSettings`（enabled/consent）两配置节 |
| `ui/settings_dialog.py` | 体验与隐私分区新增 5 控件 + 载入/保存 |
| `main.py` | 装配：网关/定位器/定位桥/授权桥注入 AgentToolbox；`_ask_location_consent` 三键弹窗；`_web_capability_line()` 能力声明注入系统提示词 |

## 2. 关键实现要点

### 2.1 SSL 信任链合并

真机日志 `CERTIFICATE_VERIFY_FAILED: self-signed certificate`：本机代理/安全软件做
HTTPS 拦截，签发的证书在 Windows 系统信任库里但不在 certifi 里。`_merged_ssl_context()`
以 certifi 为底，`ssl.enum_certificates("CA"/"ROOT")` 枚举系统证书逐张
`load_verify_locations`（单张失败跳过）；模块级只建一次，`_HttpClient` 进程级共享。
非 Windows（无 `enum_certificates`）安全回落纯 certifi。

### 2.2 两级定位与缓存策略

```
locate()
├─ 缓存命中（精确 10min / IP 6h 各自 TTL）→ 直接返回
├─ precise_provider 存在且不在失败退避期 → 主线程异步取系统定位
│   ├─ 成功 → WGS84→GCJ02 → 若无城市名补一次 IP 查询 → GeoInfo(precise=True)
│   └─ 失败 → 记失败时间戳（10min 退避，避免位置服务没开时每轮干等）
└─ 回落 IP 归属地（ip-api → ipapi.co 逐端点降级）
```

系统定位走 `PositionBridge`：worker 线程 `request(timeout)` 阻塞 → Qt 信号排队到主
线程 → 异步等 `positionUpdated`（首帧常为无效值，等有效坐标）/ `errorOccurred` /
QTimer 超时 → `threading.Event` 唤醒 worker。与授权弹窗桥同一套跨线程模式。

### 2.3 流式 tool_calls 聚合

SSE 分片协议：`delta.tool_calls[i]` 按 `index` 聚合，`function.name` 与
`arguments` 都可能分多片到达（字符串累加）；`id` 以非空为准。聚合后 JSON 解析
arguments，与 `chat_with_tools` 非流式同构返回 `(content, calls)`。流式被网关拒绝
（PROTOCOL）时 `_chat_step` **单次**回落非流式——不在网关层静默重试，保持可观测。

### 2.4 无 Key 降级链（nearby_pois）

```
未配 Key → 网页搜索（city + keyword + "大众点评 推荐"）→ 结果置前 + 指令
"直接基于内容整理推荐，不要说没办法查询" → 结尾一句话转告配置方法
网页也失败 → 引导话术（说出区域 / 申请 Key），绝不编造店铺
```

提示词顺序是实测教训：配置指引放前面时 glm-4-flash 会只复读配置步骤、丢掉搜索结果。

### 2.5 授权闸门

`_location_denial()` 统一三态判定（enabled 总开关 → consent 三态 → ask 时回调弹窗），
get_location 与 nearby_pois 共用；拒绝文案明确要求模型"引导用户直接说出城市、
不要强行再取"。main.py 的 `_ask_location_consent` 三键弹窗：仅本次（内存）/ 总是
允许（写 config.location.consent）/ 拒绝（写 config），持久化走既有 `_config_repo.save`。

## 3. 真机实测记录

| 项 | 结果 |
|---|---|
| Bing 免 Key 中文查询 | 8 条相关结果（cn.bing.com 200，certifi 直连可达） |
| Windows 系统定位 | `43.86236,125.24302 / accuracy=85.0`（Qt Positioning 默认源可用） |
| DuckDuckGo | SSL 拦截不可达（即信任链合并的动机） |
| PySide6 精简安装 | 无 QtPositioning 模块 → import 保护降级（防启动崩溃） |

## 4. 测试

- 冒烟：配置往返 / 供应商选择 / DDG 解析器 / GeoInfo 结构化 / GCJ02 转换 /
  授权闸门拒绝路径 / 降级链（有结果、无结果、网页不可用）/ AgentRunner 流式回调与
  PROTOCOL 回落 / ChatService 增量上屏不重复；
- 回归口径：配置解析沿用 `_bool/_choice/_int_clamp` 钳制测试范式；`compileall` 全量通过。

## 5. 否决方案

| 否决 | 理由 |
|---|---|
| `verify=False` 跳过证书校验 | 关闭安全边界；合并系统证书两头都兼容 |
| 只支持 tavily/bocha 等 Key 型供应商 | 免 Key 默认是开箱可用性的底线，bing 国内直连实测可行 |
| 浏览器 geolocation / 商店签名精确定位 | 桌面端无浏览器 API；Windows 商店级权限对桌宠过重，Qt Positioning 实测够用 |
| 弹窗塞进工具循环返回值（Action 协议） | 授权是即时同步决策，走 pending_action 会截断工具循环；跨线程阻塞桥更贴语义 |
| 精确定位每轮实时取 | 缓存 10 分钟 + 失败退避：位置半天不变，实时取徒增延迟与电量 |
| WGS84 坐标直接喂高德 | GCJ-02 偏移数百米，"附近"场景不可接受；标准偏移算法零依赖 |
| agent 全链路流式（工具调用也流式） | 只需最后一轮逐字上屏；中间轮 content 通常为空，全流式徒增解析面 |
| 在网关层静默重试流式 | 可观测性规约：降级必须留日志（`stream+tools rejected by gateway`） |

## 6. 与历史文档的关系

- FR v1.30 §范围外"流式 tool_calls"由本版 R4 实现（不回改 v1.30）；
- FR v1.29/RAG 等"零联网"口径不变：联网能力全部默认可关、位置需显式授权，
  聊天内容不上传搜索引擎。
