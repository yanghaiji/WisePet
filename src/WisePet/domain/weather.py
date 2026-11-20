"""天气关怀的纯逻辑：WMO 天气码→中文 / 关怀话术 / 该不该报。

刻意不接任何联网库（网络在 infra/weather_api.py），这里只吃已取到的快照：
Open-Meteo 用 WMO code，配合温度给出一句有人情味的本地关怀。全部纯函数，
便于不带 Qt/网络单测。
"""
from __future__ import annotations

#: WMO weather code → 中文简述（Open-Meteo 口径）
_WMO: tuple[tuple[tuple[int, ...], str], ...] = (
    ((0,), "晴"),
    ((1, 2), "多云"),
    ((3,), "阴"),
    ((45, 48), "有雾"),
    ((51, 53, 55, 56, 57), "毛毛雨"),
    ((61, 63, 65, 66, 67), "下雨"),
    ((71, 73, 75, 77), "下雪"),
    ((80, 81, 82), "阵雨"),
    ((85, 86), "阵雪"),
    ((95, 96, 99), "雷阵雨"),
)

#: 会触发「带伞/添衣」提醒的坏天气 code
_BAD_CODES: frozenset[int] = frozenset(
    c for codes, _ in _WMO if codes[0] in (61, 63, 65, 66, 67, 71, 73, 75, 77,
                                            80, 81, 82, 85, 86, 95, 96, 99)
    for c in codes)


def describe_code(code: int) -> str:
    """WMO code → 中文简述；未知码回落「天气多变」。"""
    try:
        value = int(code)
    except (TypeError, ValueError):
        return "天气多变"
    for codes, text in _WMO:
        if value in codes:
            return text
    return "天气多变"


def is_bad_weather(code: int) -> bool:
    return int(code) in _BAD_CODES if code is not None else False


def format_report(*, city: str, code: int, temp: float, humidity: str = "",
                  wind: str = "", high: str = "", low: str = "",
                  notice: str = "", observed_at: str = "") -> str:
    """天气实况 → 一段已排好版的 markdown 成品，供模型原样输出。

    教训（v1.35 真机）：只给纯文本事实 + 软性「请分点」，弱模型会揉成一段话。
    改为直接给出带 markdown 的成品模板（加粗/列表/引用），模型照抄就有格式。
    所有数字来自工具结果，杜绝编造；时间用当前本地时刻。
    """
    import time
    now = time.strftime("%Y-%m-%d %H:%M", time.localtime())
    lines = [f"### {city or '当前位置'} · 天气", ""]
    lines.append(f"- **天气**：{describe_code(code)}")
    lines.append(f"- **气温**：{temp:.0f}℃")
    if low or high:
        lines.append(f"- **今日温度**：{' / '.join(p for p in (low, high) if p)}")
    if humidity:
        lines.append(f"- **湿度**：{humidity}")
    if wind:
        lines.append(f"- **风力**：{wind}")
    lines.append(f"- **查询时间**：{now}")
    if observed_at:
        lines.append(f"- **观测时间**：{observed_at}")
    if notice:
        lines += ["", f"> 小提示：{notice}"]
    return "\n".join(lines)


def ensure_card_format(reply: str, card: str) -> str:
    """弱模型把天气卡片揉成一段话时，用工具原始卡片兜底恢复格式。

    card 是工具结果里「——」之前的 markdown 卡片正文。仅当回复确实丢了
    markdown（既无标题又无加粗）才替换；模型若照抄了格式则原样放行。
    """
    reply = str(reply or "")
    card = str(card or "")
    if not card:
        return reply
    body = card.split("——", 1)[0].strip()
    if not body:
        return reply
    has_format = ("###" in reply) or ("**" in reply)
    if has_format:
        return reply
    # 保留模型口语化的关心话（若有），但把结构化卡片放到最前
    extra = reply.strip()
    return body + (f"\n\n{extra}" if extra and extra != body else "")


def care_line(*, code: int, temp: float, city: str = "", humidity: str = "",
              low: str = "", high: str = "") -> str:
    """把一条天气快照翻译成一句宠物口吻的关怀（纯函数）。

    v1.36 修正：主动播报也要带关键数据（温度/湿度/今日范围），不能再只说
    「挺冷的」这种没数字的空话——用户明确要求看到温度。仍是一句话口语，
    不甩 markdown 卡片（卡片只用于聊天问答）。
    """
    desc = describe_code(code)
    where = f"{city}" if city else "外面"
    try:
        t = float(temp)
    except (TypeError, ValueError):
        t = 0.0
    # 数据片段：温度必有；湿度/今日范围有就带上，凑成一句但信息完整
    bits = [f"{desc} {t:.0f}℃"]
    if low or high:
        rng = "/".join(p.replace("高温", "").replace("低温", "").strip()
                       for p in (low, high) if p)
        if rng:
            bits.append(f"今日 {rng}")
    if humidity:
        bits.append(f"湿度 {humidity}")
    data = "，".join(bits)
    if is_bad_weather(code):
        if "雪" in desc:
            return f"{where}{data}，穿暖点别冻着，我在这儿陪你～"
        return f"{where}{data}，出门记得带伞呀～"
    if t <= 5:
        return f"{where}{data}，挺冷的，厚外套别省哦～"
    if t >= 33:
        return f"{where}{data}，热得发慌，记得多喝水～"
    return f"{where}{data}，是个适合认真过一天的好天气～"
