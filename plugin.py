"""WeMai 微信适配器插件入口（OneBot v11 WS 桥接方案）。"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Mapping, Optional

from maibot_sdk import MaiBotPlugin, MessageGateway, PluginConfigBase
from pydantic import BaseModel, ConfigDict

from .codecs import WemaiInboundCodec, WemaiOutboundCodec
from .config import WemaiPluginSettings
from .constants import GATEWAY_NAME, LOGGER_NAME
from .runtime import OneBotTransport
from .runtime.event_router import WemaiEventRouter

import asyncio
import logging
import os


class _AdapterRuntimeContext(BaseModel):
    """用于在 transport/router/codec 之间持久化引用。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    transport: Any
    router: Any
    inbound: Any
    outbound: Any


class WemaiAdapterPlugin(MaiBotPlugin):
    """MaiBot WeMai 微信适配器。"""

    config_model: ClassVar[type[PluginConfigBase] | None] = WemaiPluginSettings

    def __init__(self) -> None:
        super().__init__()
        self._logger = logging.getLogger(LOGGER_NAME)
        self._transport: Optional[OneBotTransport] = None
        self._router: Optional[WemaiEventRouter] = None
        self._inbound: Optional[WemaiInboundCodec] = None
        self._outbound: Optional[WemaiOutboundCodec] = None
        self._started = False

    # ----------------------------- 生命周期 -----------------------------

    async def on_load(self) -> None:
        self._logger.info("WeMai 插件开始加载")
        if not self._is_available_dependencies():
            self._logger.error("websockets 不可用；请确认已在 MaiBot Python venv 中安装 websockets")
            return
        settings = self._load_settings()
        if not settings.validate_runtime(self._logger):
            return
        if not settings.should_connect():
            self._logger.info("WeMai 插件未启用（plugin.enabled=false），保持空闲状态")
            return
        self._ensure_runtime(settings)
        if self._transport is not None:
            transport_ok = self._transport.start()
        else:
            transport_ok = False
        if transport_ok:
            if self._router is None or not self._router.start():
                self._logger.error("WeMai 事件路由启动失败")
                try:
                    self._transport.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._started = False
            else:
                await self._publish_ready_state(settings, ready=True)
                self._started = True
                self._logger.info("WeMai 插件已加载完成，进入运行状态")
        else:
            self._logger.warning(
                "WeMai 适配器运行态未就绪：无法连接 OneBot WS 服务端（bridge）。请确认 bridge 已启动且端口/token 配置正确。"
            )
            self._started = False
            # 即使 transport 未就绪，也要让 MaiBot host 把消息网关标记为 ready，否则
            # host 端 inbound 路由不会把消息发到本插件的 handle_wemai_gateway。
            try:
                await self._publish_ready_state(settings, ready=True)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(f"上报网关状态失败（transport 未就绪路径）：{exc}")

        if settings.plugin.diagnostic_test_inject_on_load and self._router is not None:
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(self._run_diagnostic_inject())
            except RuntimeError:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._run_diagnostic_inject())
                except RuntimeError:
                    self._logger.warning("无法获取 asyncio loop，跳过诊断自检")

    async def _run_diagnostic_inject(self) -> None:
        await asyncio.sleep(5)
        if self._router is None:
            return
        try:
            accepted = await self._router.inject_diagnostic_message(
                sender_wxid="wemai-diag-inject-user",
                text="[wemai-diag] 你好麦麦（来自 wemai-adapter 诊断注入）",
            )
            self._logger.info(
                f"WeMai 诊断：已注入假私聊消息 accepted={accepted}。"
                "请在 maibot.log 查 handle_wemai_gateway 是否被回调。"
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.error(f"WeMai 诊断注入失败：{exc}")

    async def on_unload(self) -> None:
        self._logger.info("WeMai 插件开始卸载")
        try:
            if self._router is not None:
                self._router.stop()
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(f"关闭事件路由异常：{exc}")
        try:
            if self._transport is not None:
                self._transport.stop()
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(f"关闭 OneBot 客户端异常：{exc}")
        settings = self._load_settings_or_default()
        try:
            await self._publish_ready_state(settings, ready=False)
        except Exception:  # noqa: BLE001
            pass
        self._started = False
        self._logger.info("WeMai 插件已卸载")

    async def on_config_update(
        self, scope: str, config_data: Dict[str, Any], version: str
    ) -> None:
        self._logger.info(f"on_config_update 触发 scope={scope} config_keys={list((config_data or {}).keys())}")
        if scope != "self":
            return
        self.set_plugin_config(config_data)
        settings = self._load_settings()
        if self._router is not None:
            self._router.apply_settings(settings)
        if not settings.should_connect():
            if self._started:
                self._logger.info("配置更新：已关闭 WeMai 适配器")
                if self._router is not None:
                    self._router.stop()
                if self._transport is not None:
                    self._transport.stop()
                self._started = False
                await self._publish_ready_state(settings, ready=False)
            return

        if self._started:
            self._ensure_runtime(settings)
            if self._transport is not None and self._transport.is_running():
                await self._publish_ready_state(settings, ready=True)
            # 诊断自检：如果配置要求开启诊断自检，立刻推一条假消息看 MaiBot 是否能完整路由。
            if settings.plugin.diagnostic_test_inject_on_load and self._router is not None:
                self._logger.info("on_config_update 触发诊断自检注入")
                try:
                    loop = asyncio.get_event_loop()
                    loop.create_task(self._run_diagnostic_inject())
                except RuntimeError:
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self._run_diagnostic_inject())
                    except RuntimeError:
                        self._logger.warning("无法获取 asyncio loop，跳过诊断自检")
            return

        self._ensure_runtime(settings)
        if self._transport is None or not self._transport.start():
            return
        if self._router is not None and self._router.start():
            self._started = True
            await self._publish_ready_state(settings, ready=True)

    # ----------------------------- MessageGateway -----------------------------

    @MessageGateway(
        route_type="duplex",
        name=GATEWAY_NAME,
        platform="wechat",
        protocol="ob11",
        description="WeMai OneBot v11 双向消息网关",
    )
    async def handle_wemai_gateway(
        self,
        message: Mapping[str, Any],
        route: Optional[Mapping[str, Any]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """处理 MaiBot Host 出站的消息并通过 bridge 发送到微信。"""
        del metadata
        del kwargs
        info = message.get("message_info") if isinstance(message, Mapping) else {}
        additional = info.get("additional_config") if isinstance(info, Mapping) else {}
        is_diag = bool(isinstance(additional, Mapping) and additional.get("wemai_diagnostic"))
        raw_message_size = (
            len(message.get("raw_message"))
            if isinstance(message, Mapping) and isinstance(message.get("raw_message"), list)
            else 0
        )
        self._logger.info(
            "handle_wemai_gateway 接收到出站消息"
            + ("（诊断）" if is_diag else "")
            + f"：message_id={message.get('message_id') if isinstance(message, Mapping) else None}"
            f"，raw_segments={raw_message_size}"
        )
        settings = self._load_settings_or_default()
        if not settings.should_connect():
            return {"success": False, "error": "adapter disabled"}
        if self._transport is None or not self._transport.is_running():
            return {"success": False, "error": "onebot client not running"}
        if self._outbound is None:
            return {"success": False, "error": "outbound codec not ready"}

        try:
            user_id, group_id, raw_message = self._resolve_route(message, route)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        try:
            segment_results = self._outbound.send_message(
                raw_message=raw_message,
                user_id=user_id,
                group_id=group_id,
            )
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"send failed: {exc}"}

        any_ok = any(int(item.get("status", 0)) == 0 for item in segment_results if isinstance(item, Mapping))
        any_failed = any(int(item.get("status", 0)) != 0 for item in segment_results if isinstance(item, Mapping))
        if any_ok and not any_failed:
            return {"success": True, "metadata": {"segments": segment_results}}
        if any_ok and any_failed:
            return {"success": True, "metadata": {"segments": segment_results}, "warning": "partial"}
        return {"success": False, "error": "all segments failed", "metadata": {"segments": segment_results}}

    # ----------------------------- 内部工具 -----------------------------

    def _is_available_dependencies(self) -> bool:
        try:
            import websockets  # noqa: F401
            return True
        except ImportError:
            try:
                ctx = self.ctx
                _ = ctx
            except Exception:  # noqa: BLE001
                pass
            return False

    def _load_settings(self) -> WemaiPluginSettings:
        try:
            instance = self.config
            if isinstance(instance, WemaiPluginSettings):
                return instance
        except Exception:  # noqa: BLE001
            pass
        return WemaiPluginSettings()

    def _load_settings_or_default(self) -> WemaiPluginSettings:
        try:
            return self._load_settings()
        except Exception:  # noqa: BLE001
            return WemaiPluginSettings()

    def _ensure_runtime(self, settings: WemaiPluginSettings) -> None:
        if self._inbound is None:
            self._inbound = WemaiInboundCodec()
        if self._transport is None:
            # 对接 Akasha-WeChat bridge（OneBot v11 WS 服务端）
            token = getattr(settings.server, "token", "") or ""
            self._transport = OneBotTransport(
                host=settings.server.host,
                port=settings.server.port,
                token=token,
                logger=self._logger,
            )
        if self._outbound is None:
            self._outbound = WemaiOutboundCodec(self._transport)
        if self._router is None:
            gateway_capability = self._safe_gateway_capability()
            self._router = WemaiEventRouter(
                settings=settings,
                transport=self._transport,
                inbound_codec=self._inbound,
                gateway_capability=gateway_capability,
                logger=self._logger,
            )
        self._router.apply_settings(settings)

    def _safe_gateway_capability(self) -> Any:
        try:
            ctx = self.ctx
            capability = getattr(ctx, "gateway", None)
            if capability is None:
                self._logger.warning("PluginContext 没有 gateway 能力，将退化为日志模式")
            return capability
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(f"无法拿到 PluginContext.gateway：{exc}")
            return None

    async def _publish_ready_state(self, settings: "WemaiPluginSettings", *, ready: bool) -> None:
        try:
            gateway = getattr(self.ctx, "gateway", None)
        except Exception:  # noqa: BLE001
            return
        if gateway is None:
            return
        try:
            await gateway.update_state(
                gateway_name=GATEWAY_NAME,
                ready=ready,
                platform="wechat",
                account_id=settings.server.host + ":" + str(settings.server.port),
                scope="",
                metadata={"protocol": "ob11"},
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(f"上报网关状态失败：{exc}")

    @staticmethod
    def _resolve_route(
        message: Mapping[str, Any], route: Optional[Mapping[str, Any]]
    ) -> tuple[str, str, Any]:
        raw_message = message.get("raw_message") if isinstance(message, Mapping) else None
        info = message.get("message_info", {}) if isinstance(message, Mapping) else {}
        if not isinstance(info, Mapping):
            info = {}
        additional = info.get("additional_config", {}) if isinstance(info.get("additional_config"), Mapping) else {}
        group_id = ""
        if isinstance(info.get("group_info"), Mapping):
            group_id = str(info["group_info"].get("group_id") or "")
        group_id = group_id or str(additional.get("platform_io_target_group_id") or "")

        user_id = ""
        if isinstance(additional, Mapping):
            user_id = str(additional.get("platform_io_target_user_id") or "")
        if not user_id and isinstance(route, Mapping):
            user_id = str(route.get("target_user_id") or "")
        if not group_id and not user_id:
            raise ValueError("missing target_user_id or target_group_id")

        return user_id, group_id, raw_message


def create_plugin() -> "WemaiAdapterPlugin":
    """按 MaiBot 1.x 插件合约返回插件实例。"""
    return WemaiAdapterPlugin()


__all__ = ["WemaiAdapterPlugin", "create_plugin"]
