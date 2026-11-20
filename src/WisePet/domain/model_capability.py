"""模型能力分级：给每个 AI 方案标注能干什么，作为工具循环与陷阱提示的护栏。

弱模型（flash / lite / mini 档）在实测里不守 function calling 协议、会编造
本地生活店名（见 AGENT.md）。这里把「这个模型能不能跑工具、要不要提醒用户
换档」做成纯函数，配置层只存一个档位覆盖值，运行期据此决定与提示。
"""
from __future__ import annotations

TIER_LITE = "lite"
TIER_STANDARD = "standard"
TIER_PRO = "pro"
TIERS: tuple[str, ...] = (TIER_LITE, TIER_STANDARD, TIER_PRO)

#: 模型名 → 档位的启发式规则（用户可在方案里显式覆盖）
_LITE_HINTS = ("flash", "lite", "mini", "haiku", "turbo", "nano", "4o-mini")
_PRO_HINTS = ("opus", "gpt-4.1", "gpt-4o", "claude-3.7", "claude-sonnet",
              "deepseek-v3", "r1", "o3", "o4")


def infer_tier(model: str) -> str:
    """从模型名推断能力档：显式覆盖优先，其次关键字，兜底 standard。"""
    name = str(model or "").strip().lower()
    if any(hint in name for hint in _LITE_HINTS):
        return TIER_LITE
    if any(hint in name for hint in _PRO_HINTS):
        return TIER_PRO
    return TIER_STANDARD


def normalize_tier(raw, fallback: str = "") -> str:
    """配置里的档位覆盖值归一；非法/空值回退到推断档或 standard。"""
    value = str(raw or "").strip().lower()
    if value in TIERS:
        return value
    return fallback if fallback in TIERS else TIER_STANDARD


def tools_allowed(tier: str, tool_calling_enabled: bool) -> bool:
    """工具循环准入：只看总开关，不按模型档位剥夺工具。

    教训（v1.35）：曾按 lite 档禁用工具循环，结果 glm-4-flash（v1.30/v1.33
    联网工具本就是它驱动的）连 get_location/nearby_pois 都用不了，只能回答
    「我查不到」。真正的问题不是弱模型不能调工具，而是它没工具结果时会编造——
    这靠工具结果注入 + 提示词约束解决，不能靠「不给工具」。档位现在只用于
    能力边界提示，不再拦截准入；保留 tier 参数以兼容旧调用。
    """
    return bool(tool_calling_enabled)


def capability_hint(model: str, tier: str) -> str:
    """可注入系统提示词的能力护栏（所有模型都注入，非 lite 返回空串以保持精简）。

    对 lite 档强调「先调工具、拿真实结果再说」——既给工具（不剥夺能力），
    又堵编造。语气是操作指引，不是「说查不到」的劝退。
    """
    resolved = normalize_tier(tier, infer_tier(model))
    if resolved != TIER_LITE:
        return ""
    return ("\n能力边界：你有一组可用工具。遇到商店、位置、天气、新闻、时间等"
            "实时或本地事实，必须先调用对应工具拿真实结果再回答，绝不能凭记忆"
            "编造店名、坐标、温度或数字；工具确实查不到才如实说不知道。")
