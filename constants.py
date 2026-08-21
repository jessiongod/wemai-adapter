"""WeMai 适配器全局常量。"""

from __future__ import annotations

from typing import Final


GATEWAY_NAME: Final[str] = "wemai_gateway"
"""与 MaiBot Host 端约定的消息网关组件名称。"""


PLATFORM_ID: Final[str] = "wechat"
"""插件声明的 platform_io 平台标识。"""


PROTOCOL_ID: Final[str] = "ob11"
"""桥接实现协议，对应 OneBot v11 WebSocket。"""


DEFAULT_WCF_HOST: Final[str] = "127.0.0.1"
"""bridge 的 OneBot WS 服务端默认监听主机。"""


DEFAULT_WCF_PORT: Final[int] = 7999
"""bridge 的 OneBot WS 服务端默认监听端口。"""


MSG_PORT_OFFSET: Final[int] = 1
"""（兼容字段，已不使用）预留消息端口偏移。"""


TYPE_TEXT: Final[int] = 1
TYPE_IMAGE: Final[int] = 3
TYPE_VOICE: Final[int] = 34
TYPE_EMOTION: Final[int] = 47
TYPE_FILE: Final[int] = 49
TYPE_VIDEO: Final[int] = 43
TYPE_SYSTEM: Final[int] = 10000
TYPE_RECALL: Final[int] = 10002
TYPE_FRIEND_REQUEST: Final[int] = 37
TYPE_CARD: Final[int] = 49
TYPE_XML: Final[int] = 48


CHAT_TYPE_PRIVATE: Final[str] = "private"
CHAT_TYPE_GROUP: Final[str] = "group"


WS_MSG_TEXT: Final[str] = "text"
WS_MSG_IMAGE: Final[str] = "image"
WS_MSG_VOICE: Final[str] = "voice"
WS_MSG_VIDEO: Final[str] = "video"
WS_MSG_FILE: Final[str] = "file"
WS_MSG_EMOTION: Final[str] = "emotion"
WS_MSG_LOCATION: Final[str] = "location"
WS_MSG_CARD: Final[str] = "card"
WS_MSG_OTHER: Final[str] = "other"


WX_MSG_KIND: Final[dict[int, str]] = {
    TYPE_TEXT: WS_MSG_TEXT,
    TYPE_IMAGE: WS_MSG_IMAGE,
    TYPE_VOICE: WS_MSG_VOICE,
    TYPE_VIDEO: WS_MSG_VIDEO,
    TYPE_EMOTION: WS_MSG_EMOTION,
    TYPE_FILE: WS_MSG_FILE,
    TYPE_CARD: WS_MSG_CARD,
    TYPE_XML: WS_MSG_OTHER,
}


SUPPORTED_CONFIG_VERSION: Final[str] = "0.1.0"
"""本插件期待的 config_version。"""

LOGGER_NAME: Final[str] = "wemai_adapter"
"""统一 logger 名称。"""
