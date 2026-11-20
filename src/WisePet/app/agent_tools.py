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
import time
import uuid
from pathlib import Path
from typing import Callable

from ..domain.errors import AIGatewayError
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
    _fn("get_weather", "查当前所在城市的天气实况（温度/晴雨）。用户问天气、"
        "冷不冷、下不下雨、要不要带伞时必须先调这个工具拿真实结果，不要凭记忆回答。",
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
    _fn("recall", "统一记忆检索：用户提到「之前/上次/记得吗/我说过」或需要翻旧账时，"
        "用它一次查遍长期记忆、历史聊天、资料、近况和摘要（比分别查更全）。",
        {"question": {"type": "string",
                      "description": "要找什么，尽量用用户原话里的实词"}},
        ["question"]),
    _fn("generate_image", "按用户的描述生成一张图片（画图/画一只猫/生成插画）。"
        "用户说「画一张」「画个」「生成图片」「来张图」等明确要图时必须调用这个工具，"
        "不要只回文字描述。",
        {"prompt": {"type": "string",
                    "description": "画面描述，尽量保留用户原话里的主体、风格、颜色"}},
        ["prompt"]),
    _fn("generate_video", "按用户的描述生成一段短视频（要视频/拍一段/做个动画）。"
        "视频是异步生成，可能要几十秒到几分钟，提交后会持续报告进度；"
        "用户明确要视频时必须调用，不要说「我不会拍视频」。",
        {"prompt": {"type": "string",
                    "description": "视频画面描述：主体、动作、镜头、风格，尽量用用户原话"}},
        ["prompt"]),
]

_READ_TOOLS = frozenset({
    "read_file", "list_folder", "search_knowledge", "search_history",
    "recall_memories", "recall", "get_status", "web_search", "get_location",
    "get_weather", "nearby_pois",
})

#: 循环内直接执行的工具（只读 + 无副作用的记忆写入）
SAFE_TOOLS = _READ_TOOLS | {"remember"}


class AgentToolbox:
    def __init__(self, *, knowledge=None, chat_history=None, memory_service=None,
                 tasks=None, pet=None, config_provider: Callable[[], object] | None = None,
                 remind_callback: Callable[[RemindAction], None] | None = None,
                 web_search=None, geo=None, amap=None, weather=None,
                 location_consent: Callable[[], bool] | None = None,
                 image_gateway=None, image_dir: str | Path | None = None,
                 image_cfg_provider: Callable[[], object] | None = None,
                 video_gateway=None, video_dir: str | Path | None = None,
                 video_cfg_provider: Callable[[], object] | None = None,
                 video_progress: Callable[[str], None] | None = None,
                 video_task: Callable[[str, str, str], None] | None = None,
                 unified_memory=None):
        self._knowledge = knowledge
        self._chat_history = chat_history
        self._memory = memory_service
        self._tasks = tasks
        self._pet = pet
        self._config_provider = config_provider
        self._remind_callback = remind_callback
        # 图像生成：网关 + 落盘目录 + 「取 image 用途方案」的提供者；本次工具产出
        # 的图片路径记在这里，由 AgentRunner 每轮取走塞进 AgentOutcome 回传 UI。
        self._image_gateway = image_gateway
        self._image_dir = Path(image_dir).expanduser() if image_dir else None
        self._image_cfg = image_cfg_provider
        # 视频生成：异步网关 + 落盘目录 + 「取 video 用途方案」的提供者
        self._video_gateway = video_gateway
        self._video_dir = Path(video_dir).expanduser() if video_dir else None
        self._video_cfg = video_cfg_provider
        self._video_progress = video_progress
        self._video_task = video_task
        self._unified_memory = unified_memory
        self.generated_images: list[str] = []
        self.generated_videos: list[str] = []
        # 联网能力（可选注入）：web_search 为 WebSearchGateway，geo 为 GeoLocator，
        # amap 为 AmapPoiGateway；location_consent 在需要授权弹窗时由组装根注入
        # （worker 线程阻塞等待用户选择）
        self._web = web_search
        self._geo = geo
        self._amap = amap
        self._weather = weather
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
            if name == "generate_image":
                return self._execute_generate_image(arguments)
            if name == "generate_video":
                return self._execute_generate_video(arguments)
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

    def _execute_generate_image(self, arguments: dict) -> tuple[str, None]:
        """调 image 用途的模型生成图片，落到本身份目录，路径记入本次产出。"""
        prompt = str(arguments.get("prompt") or "").strip()[:_MAX_ARG_TEXT]
        if not prompt:
            return "画图描述为空，没画成", None
        if self._image_gateway is None or self._image_cfg is None or self._image_dir is None:
            return "画图功能未启用：请在设置里添加一个「用途=image」的模型方案", None
        try:
            cfg = self._image_cfg()
            if cfg is None:
                return "还没配置画图模型：请在设置里添加一个「用途=image」的模型方案", None
            data, media = self._image_gateway.generate_image(prompt, cfg)
        except AIGatewayError as exc:
            logger.info("generate_image failed [%s]: %s", exc.kind, exc.detail)
            return f"画图失败：{exc.detail}", None
        except (OSError, ValueError) as exc:
            logger.info("generate_image failed: %s", exc)
            return f"画图失败：{exc}", None
        # 落盘：扩展名按媒体类型，文件名带时间戳+随机，避免覆盖
        ext = ".jpg" if "jpeg" in media else ".png"
        name = f"img-{int(time.time())}-{uuid.uuid4().hex[:8]}{ext}"
        try:
            self._image_dir.mkdir(parents=True, exist_ok=True)
            path = self._image_dir / name
            path.write_bytes(data)
        except OSError as exc:
            logger.info("generated image save failed: %s", exc)
            return f"图片生成了但保存失败：{exc}", None
        self.generated_images.append(str(path))
        return f"已经画好了，图片在：{path}", None

    def _execute_generate_video(self, arguments: dict) -> tuple[str, None]:
        """调 video 用途的模型生成视频（异步），成片落盘并记入本次产出。"""
        import itertools
        _video_seq = getattr(self, "_video_seq_gen", None)
        if _video_seq is None:
            _video_seq = self._video_seq_gen = itertools.count(1)
        task_id = f"video-{next(_video_seq)}"

        def emit(status: str, text: str = "", path: str = "") -> None:
            if self._video_task is not None:
                try:
                    self._video_task(task_id, status, text, path)
                except Exception:  # noqa: BLE001 - 通知失败不影响生成
                    logger.debug("video task event failed", exc_info=True)

        prompt = str(arguments.get("prompt") or "").strip()[:_MAX_ARG_TEXT]
        if not prompt:
            emit("failed", "视频描述为空")
            return "视频描述为空，没做成", None
        if self._video_gateway is None or self._video_cfg is None or self._video_dir is None:
            msg = "视频功能未启用：请在设置里添加一个「用途=video」的模型方案"
            emit("failed", msg)
            return msg, None
        emit("submitted", "视频任务已提交，正在排队…")
        try:
            cfg = self._video_cfg()
            if cfg is None:
                msg = "还没配置视频模型：请在设置里添加一个「用途=video」的模型方案"
                emit("failed", msg)
                return msg, None
            data, media = self._video_gateway.generate_video(
                prompt, cfg, progress=self._video_progress)
        except AIGatewayError as exc:
            logger.info("generate_video failed [%s]: %s", exc.kind, exc.detail)
            emit("failed", exc.detail)
            return (f"视频生成失败：{exc.detail}。"
                    "这次工具调用已经结束，你没有重试机会了；"
                    "请如实告诉用户视频没做成和原因，不要说「我再试试」「给你生成中」"
                    "这类正在生成的话——那会让用户空等。"), None
        except (OSError, ValueError) as exc:
            logger.info("generate_video failed: %s", exc)
            emit("failed", str(exc))
            return (f"视频生成失败：{exc}。请如实告诉用户没做成，"
                    "不要说「生成中」或承诺重试。"), None
        ext = ".webm" if "webm" in media else ".mp4"
        name = f"video-{int(time.time())}-{uuid.uuid4().hex[:8]}{ext}"
        try:
            self._video_dir.mkdir(parents=True, exist_ok=True)
            path = self._video_dir / name
            path.write_bytes(data)
        except OSError as exc:
            logger.info("generated video save failed: %s", exc)
            emit("failed", str(exc))
            return f"视频生成了但保存失败：{exc}", None
        self.generated_videos.append(str(path))
        emit("done", "视频生成完成", str(path))
        return f"视频做好了，文件在：{path}", None

    # ---------- 只读工具 ----------

    def _execute_read(self, name: str, args: dict) -> str:
        if name == "recall":
            if self._unified_memory is None:
                return "统一记忆检索不可用"
            hits = self._unified_memory.recall(str(args.get("question") or ""))
            if not hits:
                return "记忆里没有找到相关内容"
            return "\n".join(f"[{h.source}{' ' + h.detail if h.detail else ''}]"
                             f" {h.text[:300]}" for h in hits)
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
        if name == "get_weather":
            return self._execute_get_weather()
        if name == "nearby_pois":
            return self._execute_nearby_pois(args)
        return f"未知工具 {name}"

    # ---------- 联网工具 ----------

    def _execute_get_weather(self) -> str:
        """即时天气查询：用户主动问才调，不受「主动天气推送」开关影响。"""
        if self._weather is None:
            return ("天气工具暂不可用。请如实告诉用户你这边查不到实时天气，"
                    "不要编造温度或天气状况，可以建议用户看手机天气应用")
        snap = self._weather.fetch() if hasattr(self._weather, "fetch") else None
        if snap is None:
            return ("暂时取不到天气（网络或定位不可用）。请如实说明查不到，"
                    "不要编造温度/晴雨，可建议用户查看手机天气应用")
        from ..domain.weather import format_report
        report = format_report(
            city=getattr(snap, "city", ""), code=snap.code, temp=snap.temp,
            humidity=getattr(snap, "humidity", ""), wind=getattr(snap, "wind", ""),
            high=getattr(snap, "high", ""), low=getattr(snap, "low", ""),
            notice=getattr(snap, "notice", ""),
            observed_at=getattr(snap, "observed_at", ""))
        return (report + "\n\n——以上是要展示给用户的天气卡片，"
                "请把这段 markdown 原样输出（保留 ### 标题、- 列表、> 引用和所有加粗），"
                "不要改写成一整段话、不要去掉格式符号。"
                "可以在最后另加一句你自己的宠物口吻关心话，"
                "但不要改动卡片本身，也不要编造这里没有的数字。")

    #: 关键词末尾的场所类型词（从「桂林路 咖啡」里剥掉「咖啡」留下区域名「桂林路」）
    _PLACE_WORDS = ("美食", "咖啡", "咖啡馆", "餐厅", "饭店", "超市", "便利店",
                    "加油", "加油站", "药店", "医院", "学校", "酒店", "宾馆",
                    "奶茶", "火锅", "烧烤", "小吃", "停车", "停车场", "银行")

    @classmethod
    def _extract_area(cls, keyword: str) -> str:
        """从「区域 + 类型」的关键词里提取区域名；纯类型词返回空串。"""
        parts = [p for p in str(keyword or "").replace(",", " ").split() if p]
        if len(parts) < 2:
            return ""
        # 去掉末尾的场所类型词，剩下的拼成区域名
        area_parts = [p for p in parts
                      if not any(word in p for word in cls._PLACE_WORDS)]
        return "".join(area_parts).strip()[:40]

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
        # 精确定位不可用时（IP 城市中心误差几公里），若用户提到了区域名，
        # 先按区域名地理编码到街道级中心点——否则搜出来全是市中心的店。
        lat, lon, center_note = info.lat, info.lon, info.text
        area = self._extract_area(keyword)
        if area and not info.precise and self._amap is not None:
            geo = self._amap.geocode(area, city=getattr(info, "city", "") or "")
            if geo is not None:
                lat, lon = geo
                center_note = f"{info.city or ''}{area}附近（按区域名精确定位）"
        pois = self._amap.search_around(keyword, lat, lon,
                                        int(radius_km * 1000))
        if not pois:
            return (f"{radius_km:g}km 内没搜到「{keyword}」相关的场所"
                    f"（搜索中心：{center_note}）。可以建议用户加大半径、"
                    "或说出更具体的区域（如「XX路」「XX区」），"
                    "或如实说明暂时查不到，不要编造店铺")
        lines = []
        for i, p in enumerate(pois, 1):
            dist = f"，距你约 {p.distance_m} 米" if p.distance_m else ""
            tel = f"，电话 {p.tel}" if p.tel else ""
            lines.append(f"{i}. {p.name}（{p.address}{dist}{tel}）")
        return (f"搜索中心：{center_note}。附近「{keyword}」搜索结果：\n"
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
