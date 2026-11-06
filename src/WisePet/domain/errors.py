"""领域异常：跨层传递的错误语义。"""


class IllegalTransitionError(Exception):
    """行为状态机迁移不在白名单内（不变量 I1/I2）。"""

    def __init__(self, source, target):
        self.source = source
        self.target = target
        super().__init__(f"illegal behavior transition: {source.name} -> {target.name}")


class AIGatewayError(Exception):
    """AI 网关调用失败，kind 供上层映射用户可读话术。

    PROTOCOL 专指「HTTP 2xx 但回读不出内容」：网关把错误包在 200 里、
    或 Base URL 指向了非 OpenAI 兼容协议（如 /api/anthropic），
    既不是网络问题也不是服务端 5xx，不能与 SERVER 共用一句文案。
    """

    AUTH = "auth"
    NETWORK = "network"
    RATE = "rate"
    TIMEOUT = "timeout"
    SERVER = "server"
    PROTOCOL = "protocol"
    KINDS = (AUTH, NETWORK, RATE, TIMEOUT, SERVER, PROTOCOL)

    def __init__(self, kind: str, detail: str = ""):
        if kind not in self.KINDS:
            raise ValueError(f"unknown AIGatewayError kind: {kind}")
        self.kind = kind
        self.detail = detail
        super().__init__(f"AI gateway error [{kind}]: {detail}")
