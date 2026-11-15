"""AgentToolbox：agent 循环的工具注册表与执行器。

两类工具：
- 只读（agent 循环内直接执行，结果回喂模型）：read_file / list_folder /
  search_knowledge / search_history / recall_memories / get_status /
  web_search（联网，需设置开启）/ get_location（IP 定位，需用户授权）
- 写盘 / 排期（不在线程内执行）：返回 Action 对象，由组装根转投主线程走
  既有的三级权限 + 预览确认 + 审计 + 撤销链路——安全边界不因 agent 而放宽

只读沙箱：read_file / list_folder 只允许「资料文件夹」内的路径（与 RAG 同一
信任域），拒符号链接、限大小；想读别处就让用户把它设为资料文件夹。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from .action_protocol import (
    CreateFileAction, MakePptAction, RemindAction, describe_action,
)

logger = logging.getLogger(__name__)

MAX_READ_BYTES = 512 * 1024
#: 工具参数文本字段统一截断，防止模型幻觉塞进超大内容
_MAX_ARG_TEXT = 60_000

#: OpenAI tools 协议的 JSON Schema（模型可见的工具清单）
def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required}}}


TOOL_SCHEMAS: list[dict] = [
    _fn("read_file", "读取用户资料文件夹内的一个文本文件内容（仅限资料文件夹内）",
        {"path": {"type": "string",
                  "description": "资料文件夹内的相对或绝对路径"}}, ["path"]),
    _fn("list_folder", "列出用户资料文件夹里的文件（递归，含子文件夹）", {}, []),
    _fn("search_knowledge", "在用户的资料索引里按关键词检索相关片段",
        {"query": {"type": "string", "description": "检索关键词"}}, ["query"]),
    _fn("search_history", "跨全部日期检索用户与本宠物的历史聊天记录（不只当前会话），"
        "用户提起旧话题或上下文里找不到答案时用",
        {"query": {"type": "string",
                   "description": "用户提到的实词关键词（子串匹配，大小写不敏感）；"
                                  "可换不同关键词多查几次"}}, ["query"]),
    _fn("recall_memories", "查看记住的用户长期信息（名字/近况/项目）与变更时间线", {}, []),
    _fn("remember", "把对话中的重要事实记进长期记忆（如用户偏好、约定、进行中的事）",
        {"key": {"type": "string", "description": "事实主题，如 养了只猫"},
         "value": {"type": "string", "description": "具体内容"}},
        ["key", "value"]),
    _fn("get_status", "查看宠物状态（饱食/心情/精力）、当前时间与正在进行的托付", {}, []),
    _fn("remind", "给用户排一个定时提醒",
        {"delay_min": {"type": "number", "description": "N 分钟后（与 at_hm 二选一）"},
         "at_hm": {"type": "string", "description": "HH:MM 当天时刻（与 delay_min 二选一）"},
         "text": {"type": "string", "description": "提醒内容"}}, ["text"]),
    _fn("create_file", "在本机创建一个文本文件（需要用户确认后才会写入）",
        {"path": {"type": "string"}, "content": {"type": "string"}},
        ["path", "content"]),
    _fn("make_ppt", "生成一个 PPT 文件（需要用户确认后才会写入）",
        {"path": {"type": "string"}, "title": {"type": "string"},
         "slides": {"type": "array", "items": {"type": "object", "properties": {
             "title": {"type": "string"},
             "bullets": {"type": "array", "items": {"type": "string"}}}}}},
        ["path", "title", "slides"]),
    _fn("web_search", "联网搜索：查最新的网页资讯（新闻/天气/资料等实时信息）。",
        {"query": {"type": "string",
                   "description": "搜索关键词；本地生活类查询（附近美食/店铺）"
                                  "请改用 nearby_pois"}},
        ["query"]),
    _fn("get_location", "获取用户当前的大致位置（IP 城市级，用于附近搜索/天气）。",
        {}, []),
    _fn("nearby_pois", "查用户附近的本地生活场所（美食/咖啡/超市/加油站等）。"
        "用户问「附近的xx」时必须先调这个工具拿真实结果，绝对不要凭记忆编造店名。",
        {"keyword": {"type": "string",
                     "description": "场所类型 + 用户提到的区域，如「一汽35街区 美食」"
                                    "「桂林路 咖啡」；用户没提区域就只写类型，"
                                    "如 美食、咖啡馆、超市"},
         "radius_km": {"type": "number",
                       "description": "搜索半径公里数，默认 3"}},
        ["keyword"]),
]

_READ_TOOLS = frozenset({
    "read_file", "list_folder", "search_knowledge", "search_history",
    "recall_memories", "get_status", "web_search", "get_location",
    "nearby_pois",
})

#: 循环内直接执行的工具（只读 + 无副作用的记忆写入）
SAFE_TOOLS = _READ_TOOLS | {"remember"}


class AgentToolbox:
    def __init__(self, *, knowledge=None, chat_history=None, memory_service=None,
                 tasks=None, pet=None, config_provider: Callable[[], object] | None = None,
                 remind_callback: Callable[[RemindAction], None] | None = None,
                 web_search=None, geo=None, amap=None,
                 location_consent: Callable[[], bool] | None = None):
        self._knowledge = knowledge
        self._chat_history = chat_history
        self._memory = memory_service
        self._tasks = tasks
        self._pet = pet
        self._config_provider = config_provider
        self._remind_callback = remind_callback
        # 联网能力（可选注入）：web_search 为 WebSearchGateway，geo 为 GeoLocator，
        # amap 为 AmapPoiGateway；location_consent 在需要授权弹窗时由组装根注入
        # （worker 线程阻塞等待用户选择）
        self._web = web_search
        self._geo = geo
        self._amap = amap
        self._consent = location_consent

    @property
    def knowledge_folder(self) -> Path | None:
        cfg = self._config_provider() if self._config_provider else None
        folder = getattr(getattr(cfg, "assistant", None), "knowledge_dir", "") or ""
        return Path(folder).expanduser() if folder else None

    # ---------- 执行 ----------

    def execute(self, name: str, arguments: dict) -> tuple[str, object | None]:
        """执行一个工具调用。返回 (结果文本, 待主线程确认的动作或 None)。"""
        name = str(name or "").strip()
        arguments = dict(arguments or {})
        try:
            if name in _READ_TOOLS:
                return self._execute_read(name, arguments), None
            if name == "remember":
                key = str(arguments.get("key") or "")
                value = str(arguments.get("value") or "")
                if self._memory is None or not self._memory.remember(key, value):
                    return "记忆功能未启用或内容为空", None
                return f"已经记住了：{key}", None
            if name == "web_search":
                return self._execute_web_search(args), None
            if name == "get_location":
                return self._execute_get_location(), None
            if name == "nearby_pois":
                return self._execute_nearby_pois(args), None
            if name == "remind":
                return self._execute_remind(arguments)
            if name == "create_file":
                action = self._build_create_file(arguments)
                return f"已准备好创建文件，等待用户确认：{action.path}", action
            if name == "make_ppt":
                action = self._build_make_ppt(arguments)
                return f"已准备好生成 PPT，等待用户确认：{action.path}", action
            return f"未知工具 {name}", None
        except (OSError, ValueError) as exc:
            logger.info("tool %s failed: %s", name, exc)
            return f"工具执行失败：{exc}", None

    # ---------- 只读工具 ----------

    def _execute_read(self, name: str, args: dict) -> str:
        if name == "read_file":
            path = self._resolve_in_folder(str(args.get("path") or ""))
            if path is None:
                return "只能读取资料文件夹内的文件（设置 → 体验与隐私 → 资料文件夹）"
            if path.is_symlink() or not path.is_file():
                return f"不是可读的普通文件：{path.name}"
            if path.stat().st_size > MAX_READ_BYTES:
                return f"文件太大（>512KB），不读：{path.name}"
            return path.read_text(encoding="utf-8", errors="replace")[:_MAX_ARG_TEXT]
        if name == "list_folder":
            root = self._resolve_in_folder("")
            if root is None:
                return "未设置资料文件夹（设置 → 体验与隐私 → 资料文件夹）"
            lines = [f"{p.relative_to(root)}{'/' if p.is_dir() else ''}"
                     for p in sorted(root.rglob("*"))[:200]]
            return "\n".join(lines) or "（文件夹是空的）"
        if name == "search_knowledge":
            query = str(args.get("query") or "")
            if self._knowledge is None:
                return "资料索引未启用"
            hits = self._knowledge.search(query, top_k=3)
            if not hits:
                return "资料里没有找到相关内容"
            return "\n---\n".join(
                f"[{Path(h['path']).name}] {h['text'][:500]}" for h in hits)
        if name == "search_history":
            query = str(args.get("query") or "")
            if self._chat_history is None:
                return "历史检索不可用"
            records = self._chat_history.search(query, limit=20)
            if not records:
                return "聊天记录里没有找到相关内容"
            return "\n".join(
                f"[{r.day}] {r.role}: {str(r.content)[:200]}" for r in records)
        if name == "recall_memories":
            if self._memory is None:
                return "长期记忆不可用"
            return self._memory.facts() or "（还没有记住什么）"
        if name == "get_status":
            vitals = self._pet.vitals_summary() if self._pet is not None else ""
            tasks = self._tasks.describe() if self._tasks is not None else "（无）"
            return f"{vitals}\n进行中的托付：\n{tasks}"
        if name == "web_search":
            return self._execute_web_search(args)
        if name == "get_location":
            return self._execute_get_location()
        if name == "nearby_pois":
            return self._execute_nearby_pois(args)
        return f"未知工具 {name}"

    # ---------- 联网工具 ----------

    def _location_denial(self) -> str | None:
        """位置权限闸门：返回 None 表示放行，否则返回给模型的人话拒绝文案。"""
        loc_cfg = getattr(self._config_provider() or object(), "location", None)
        if loc_cfg is not None and not loc_cfg.enabled:
            return ("用户在设置里关闭了位置权限（设置 → 体验与隐私 → 位置权限），"
                    "不要强行获取，引导用户直接说出城市或区域")
        if loc_cfg is None or loc_cfg.consent == "ask":
            # 首次/每次询问：弹窗走主线程（worker 线程阻塞等结果），拒绝则如实回话
            if self._consent is None or not self._consent():
                return ("用户拒绝了获取位置。不要再尝试定位类工具，可以引导用户"
                        "直接说出想查询的城市或区域")
        return None

    def _execute_web_search(self, args: dict) -> str:
        if self._web is None:
            return "联网搜索功能未启用（需要重启后生效或检查配置）"
        if not self._web.enabled():
            return ("联网搜索在设置里被关闭了（设置 → 体验与隐私 → 联网搜索），"
                    "请告知用户后基于已有知识回答")
        query = str(args.get("query") or "").strip()[:200]
        if not query:
            return "搜索关键词不能为空"
        results = self._web.search(query)
        if not results:
            return ("这次搜索没有拿到结果（可能是网络不通或搜索服务不可用）。"
                    "请如实告知用户暂时查不到，不要编造信息")
        lines = [f"{i}. {r.title}\n   {r.url}\n   {r.snippet}"
                 for i, r in enumerate(results, 1)]
        return "搜索结果：\n" + "\n".join(lines)

    def _execute_get_location(self) -> str:
        if self._geo is None:
            return "定位功能不可用"
        denial = self._location_denial()
        if denial:
            return denial
        info = self._geo.locate()
        if info is None:
            return ("暂时取不到位置（IP 定位服务不可用）。请引导用户直接说出"
                    "所在城市或区域")
        return f"用户当前大致位置（IP 城市）：{info.text}"

    def _execute_nearby_pois(self, args: dict) -> str:
        keyword = str(args.get("keyword") or "").strip()[:50]
        if not keyword:
            return "场所类型关键词不能为空（如 美食、咖啡馆）"
        if self._amap is None or not self._amap.enabled():
            # 未配高德 Key：降级为网页搜索拿近似结果，并请 AI 转告配置方法
            return self._nearby_fallback_web(keyword)
        denial = self._location_denial()
        if denial:
            return denial
        if self._geo is None:
            return "定位功能不可用"
        info = self._geo.locate()
        if info is None or info.lat is None or info.lon is None:
            return ("暂时取不到位置（IP 定位服务不可用），无法按距离搜索。"
                    "请引导用户直接说出所在城市或区域，再改用 web_search 查询")
        radius_km = args.get("radius_km")
        try:
            radius_km = float(radius_km) if radius_km else 3.0
        except (TypeError, ValueError):
            radius_km = 3.0
        pois = self._amap.search_around(keyword, info.lat, info.lon,
                                        int(radius_km * 1000))
        if not pois:
            return (f"{radius_km:g}km 内没搜到「{keyword}」相关的场所"
                    f"（当前位置：{info.text}）。可以建议用户加大半径，"
                    "或如实说明暂时查不到，不要编造店铺")
        lines = []
        for i, p in enumerate(pois, 1):
            dist = f"，距你约 {p.distance_m} 米" if p.distance_m else ""
            tel = f"，电话 {p.tel}" if p.tel else ""
            lines.append(f"{i}. {p.name}（{p.address}{dist}{tel}）")
        return (f"用户位置：{info.text}。附近「{keyword}」搜索结果：\n"
                + "\n".join(lines))

    def _nearby_fallback_web(self, keyword: str) -> str:
        """未配高德 Key 时的降级：网页搜索近似结果 + 配 Key 提示。

        结果是网页摘要而非真实店铺点位，请模型整理后如实告知"仅供参考"；
        提示语必须带给用户——这是他们升级体验的唯一入口。
        """
        # 定位结果只用来拼城市关键词：授权闸门通过与否不影响降级可用性
        city = ""
        if (self._geo is not None
                and self._location_denial() is None):
            info = self._geo.locate()
            if info is not None and info.city:
                city = info.city
        query = " ".join(p for p in (city, keyword, "大众点评 推荐") if p)
        results: list = []
        if self._web is not None and self._web.enabled():
            results = self._web.search(query)
        if not results:
            return ("周边搜索需要高德 Key（当前未配置），网页搜索也没有拿到结果。"
                    "请如实告知用户暂时查不到，可以引导用户直接说出想查的区域，"
                    "或到 lbs.amap.com 免费申请「Web 服务」Key 填入 "
                    "设置 → 体验与隐私 → 高德 Key 后再试。"
                    "不要说「我没有这个能力」，不要编造店铺")
        lines = [f"{i}. {r.title} — {r.snippet}（{r.url}）"
                 for i, r in enumerate(results[:6], 1)]
        return (
            "以下是通过网页搜索拿到的真实资料，请直接基于这些内容给用户整理出"
            "「附近哪里能吃到」的推荐（挑出提到具体店名/商圈的条目，口语化转述，"
            "注明仅供参考，不要说「没办法查询」「没有能力」）。\n"
            "搜索资料：\n" + "\n".join(lines) +
            "\n（说明：这些是网页摘要而非地图点位，没有到店距离。结尾用一句话告诉"
            "用户：在 设置 → 体验与隐私 → 高德 Key 填入免费 Key（lbs.amap.com 申请，"
            "类型选 Web 服务）后，就能查到精确的附近店铺和距离。）")

    def _resolve_in_folder(self, relative: str) -> Path | None:
        root = self.knowledge_folder
        if root is None or not root.is_dir():
            return None
        candidate = (root / str(relative or "").strip()).resolve() \
            if str(relative or "").strip() else root
        # 防目录穿越：解析后的真实路径必须仍在资料文件夹内
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return None
        return candidate

    # ---------- 写盘 / 排期动作（转 Action 走主线程确认链路） ----------

    def _execute_remind(self, args: dict) -> tuple[str, object | None]:
        text = str(args.get("text") or "").strip()[:100]
        if not text:
            return "提醒内容不能为空", None
        delay_min = args.get("delay_min")
        at_hm = str(args.get("at_hm") or "").strip()
        action = RemindAction(text=text, delay_min=float(delay_min) if delay_min else 0.0,
                              at_hm=at_hm)
        if action.delay_min <= 0 and not action.at_hm:
            return "需要 delay_min 或 at_hm 之一", None
        if self._remind_callback is not None:
            self._remind_callback(action)      # 排期无副作用风险，线程内直接做
            return f"已排好提醒：{describe_action(action)}", None
        return describe_action(action), action

    def _build_create_file(self, args: dict) -> CreateFileAction:
        return CreateFileAction(
            path=str(args.get("path") or "").strip(),
            content=str(args.get("content") or "")[:_MAX_ARG_TEXT])

    def _build_make_ppt(self, args: dict) -> MakePptAction:
        slides = [dict(s) for s in (args.get("slides") or [])
                  if isinstance(s, dict)]
        return MakePptAction(path=str(args.get("path") or "").strip(),
                             title=str(args.get("title") or "")[:100],
                             slides=slides[:30])
