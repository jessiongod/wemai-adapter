"""WeMai 适配器配置模型。"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Literal, Optional

from maibot_sdk import Field, PluginConfigBase
from pydantic import field_validator

from .constants import (
    DEFAULT_WCF_HOST,
    DEFAULT_WCF_PORT,
    SUPPORTED_CONFIG_VERSION,
)


def _i18n(label_en: str, label_zh: str, hint_en: str = "", hint_zh: str = "") -> Dict[str, Dict[str, str]]:
    return {
        "en_US": {"label": label_en, "hint": hint_en} if hint_en else {"label": label_en},
        "zh_CN": {"label": label_zh, "hint": hint_zh} if hint_zh else {"label": label_zh},
    }


def _meta(label: str, order: int, **extra: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {"label": label, "order": order}
    data.update(extra)
    return data


class WemaiPluginOptions(PluginConfigBase):
    """插件级开关与配置版本。"""

    __ui_label__: ClassVar[str] = "插件设置"
    __ui_order__: ClassVar[int] = 0

    enabled: bool = Field(
        default=False,
        description="是否启用 WeMai 微信适配器。",
        json_schema_extra=_meta(
            "启用适配器",
            0,
            i18n=_i18n("Enable adapter", "启用适配器", "Disable to keep the adapter idle.", "关闭后适配器保持空闲"),
        ),
    )
    config_version: str = Field(
        default=SUPPORTED_CONFIG_VERSION,
        description="当前适配器配置结构版本。",
        json_schema_extra=_meta(
            "配置版本",
            99,
            disabled=True,
            hidden=True,
            i18n=_i18n("Config version", "配置版本"),
        ),
    )
    diagnostic_test_inject_on_load: bool = Field(
        default=False,
        description="若启用，则 plugin 加载完成后 5 秒内主动向 MaiBot Host 推入一条测试消息（不依赖真实微信），用以验证 inbound→outbound 全链路。仅触发一次。",
        json_schema_extra=_meta(
            "诊断：注入一条假私聊",
            1,
            i18n=_i18n(
                "Diagnostic: inject one fake private message",
                "诊断：注入一条假私聊",
                "On plugin load, push a fake private message into host to verify the full pipeline.",
                "插件加载完成后向 MaiBot 推一条假私聊用于验证整条管道。",
            ),
        ),
    )

    def should_connect(self) -> bool:
        return bool(self.enabled)

    @field_validator("config_version", mode="before")
    @classmethod
    def _normalize_config_version(cls, value: Any) -> str:
        return (str(value).strip() if value is not None else "") or SUPPORTED_CONFIG_VERSION


class WemaiServerConfig(PluginConfigBase):
    """OneBot v11 桥接服务端连接配置（对接 WeFlow + bridge）。"""

    __ui_label__: ClassVar[str] = "桥接服务端连接"
    __ui_order__: ClassVar[int] = 1

    host: str = Field(
        default=DEFAULT_WCF_HOST,
        description="bridge 的 OneBot WS 服务端监听地址。",
        json_schema_extra=_meta(
            "桥接主机",
            0,
            placeholder="127.0.0.1",
            i18n=_i18n("Bridge host", "桥接主机", "Address of the OneBot bridge server", "OneBot 桥接服务端地址"),
        ),
    )
    port: int = Field(
        default=DEFAULT_WCF_PORT,
        description="bridge 的 OneBot WS 服务端端口。",
        json_schema_extra=_meta(
            "桥接端口",
            1,
            i18n=_i18n("Bridge port", "桥接端口"),
        ),
    )
    debug_spy: bool = Field(
        default=False,
        description="是否输出调试日志。",
        json_schema_extra=_meta(
            "调试日志",
            2,
            i18n=_i18n("Debug logging", "调试日志"),
        ),
    )
    auto_launch_wcf: bool = Field(
        default=False,
        description="（兼容字段，已不使用）是否由 MaiBot 进程自动启动本地服务。",
        json_schema_extra=_meta(
            "自动启动本地服务",
            3,
            i18n=_i18n("Auto-launch local service", "自动启动本地服务"),
        ),
    )
    block_on_login: bool = Field(
        default=False,
        description="（兼容字段，已不使用）是否阻塞等待登录。",
        json_schema_extra=_meta(
            "阻塞等待登录",
            4,
            i18n=_i18n("Block until login", "阻塞等待登录"),
        ),
    )
    rpc_timeout_ms: int = Field(
        default=5000,
        description="连接超时时间（毫秒）。",
        json_schema_extra=_meta(
            "连接超时（毫秒）",
            5,
            i18n=_i18n("Connection timeout (ms)", "连接超时（毫秒）"),
        ),
    )
    token: str = Field(
        default="",
        description="OneBot 桥接服务端访问令牌（对应 bridge 的 ob_server_token）。",
        json_schema_extra=_meta(
            "桥接令牌",
            6,
            i18n=_i18n("Bridge token", "桥接令牌", "Bearer token for the OneBot bridge server", "OneBot 桥接服务端 Bearer 令牌"),
        ),
    )

    @field_validator("host", mode="before")
    @classmethod
    def _normalize_host(cls, value: Any) -> str:
        text = str(value or "").strip() if value is not None else ""
        return text or DEFAULT_WCF_HOST

    @field_validator("port", mode="before")
    @classmethod
    def _normalize_port(cls, value: Any) -> int:
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
            if parsed > 0:
                return parsed
        return DEFAULT_WCF_PORT

    @field_validator("rpc_timeout_ms", mode="before")
    @classmethod
    def _normalize_timeout(cls, value: Any) -> int:
        if isinstance(value, int) and value > 0:
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
            if parsed > 0:
                return parsed
        return 5000


class WemaiChatConfig(PluginConfigBase):
    """聊天白名单 / 黑名单。"""

    __ui_label__: ClassVar[str] = "聊天过滤"
    __ui_order__: ClassVar[int] = 2

    enable_chat_list_filter: bool = Field(
        default=True,
        description="是否启用聊天名单过滤。",
        json_schema_extra=_meta(
            "启用聊天名单过滤",
            0,
            i18n=_i18n("Enable chat list filter", "启用聊天名单过滤"),
        ),
    )
    private_list_type: Literal["whitelist", "blacklist"] = Field(
        default="blacklist",
        description="私聊名单模式（白名单仅放行，黑名单仅屏蔽）。",
        json_schema_extra=_meta(
            "私聊名单模式",
            1,
            i18n=_i18n("Private list mode", "私聊名单模式"),
        ),
    )
    private_list: List[str] = Field(
        default_factory=list,
        description="私聊白/黑名单（接受微信号或 wxid）。",
        json_schema_extra=_meta(
            "私聊名单",
            2,
            placeholder="alice@example.com 或 wxid_xxx",
            i18n=_i18n("Private list", "私聊名单"),
        ),
    )
    group_list_type: Literal["whitelist", "blacklist"] = Field(
        default="blacklist",
        description="群聊名单模式。",
        json_schema_extra=_meta(
            "群聊名单模式",
            3,
            i18n=_i18n("Group list mode", "群聊名单模式"),
        ),
    )
    group_list: List[str] = Field(
        default_factory=list,
        description="群聊白/黑名单（接受 roomid）。",
        json_schema_extra=_meta(
            "群聊名单",
            4,
            placeholder="12345678@chatroom",
            i18n=_i18n("Group list", "群聊名单"),
        ),
    )

    @field_validator("private_list_type", "group_list_type", mode="before")
    @classmethod
    def _normalize_list_mode(cls, value: Any) -> str:
        text = str(value or "").strip().lower()
        return text if text in {"whitelist", "blacklist"} else "blacklist"

    @field_validator("private_list", "group_list", mode="before")
    @classmethod
    def _normalize_list(cls, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        seen: set[str] = set()
        result: List[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result


class WemaiFilterConfig(PluginConfigBase):
    """消息过滤配置。"""

    __ui_label__: ClassVar[str] = "消息过滤"
    __ui_order__: ClassVar[int] = 3

    ignore_self_message: bool = Field(
        default=True,
        description="是否忽略自身发送的消息。",
        json_schema_extra=_meta(
            "忽略自身消息",
            0,
            i18n=_i18n("Ignore self messages", "忽略自身消息"),
        ),
    )
    accepted_msg_types: List[str] = Field(
        default_factory=lambda: ["text", "image", "voice", "video", "file", "emotion", "card"],
        description="WeMai 接收并转发给 MaiBot 的消息类型白名单（包含 text/image/voice/video/file/emotion/card/other）。",
        json_schema_extra=_meta(
            "接受的消息类型",
            1,
            i18n=_i18n("Accepted message types", "接受的消息类型"),
        ),
    )

    @field_validator("accepted_msg_types", mode="before")
    @classmethod
    def _normalize_accepted_types(cls, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        result: List[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip().lower()
            if text and text not in result:
                result.append(text)
        return result


class WemaiPluginSettings(PluginConfigBase):
    """WeMai 插件完整配置。"""

    plugin: WemaiPluginOptions = Field(default_factory=WemaiPluginOptions)
    server: WemaiServerConfig = Field(default_factory=WemaiServerConfig)
    chat: WemaiChatConfig = Field(default_factory=WemaiChatConfig)
    filters: WemaiFilterConfig = Field(default_factory=WemaiFilterConfig)

    def should_connect(self) -> bool:
        return self.plugin.should_connect()

    def validate_runtime(self, logger: Any) -> bool:
        cfg_ver = self.plugin.config_version
        if cfg_ver != SUPPORTED_CONFIG_VERSION:
            logger.error(
                f"WeMai 配置版本不兼容: 当前 {cfg_ver}，插件要求 {SUPPORTED_CONFIG_VERSION}"
            )
            return False
        if not self.server.host:
            logger.error("WeMai server.host 为空")
            return False
        if self.server.port <= 0 or self.server.port > 65535:
            logger.error(f"WeMai server.port 非法: {self.server.port}")
            return False
        if self.server.rpc_timeout_ms <= 0:
            logger.error(f"WeMai server.rpc_timeout_ms 非法: {self.server.rpc_timeout_ms}")
            return False
        return True


__all__ = [
    "WemaiPluginOptions",
    "WemaiServerConfig",
    "WemaiChatConfig",
    "WemaiFilterConfig",
    "WemaiPluginSettings",
]
