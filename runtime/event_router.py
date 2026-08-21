"""WeMai 入站事件路由器。

跨线程核心问题：
- OneBot 的 ``enable_recv_msg`` 会在它自己的线程上回调 ``on_message``，我无法在该线程
  里 ``await ctx.gateway.route_message(...)``，而 MaiBot 主的 RPC 调用必须在 MaiBot
  Runner 的 asyncio loop 内执行。
- 因此本路由采用：OneBot 线程 → ``queue.Queue`` → ``start()`` 启动的内部 async task
  ``_drain_loop`` → 在 MaiBot Runner 的 loop 内批量 ``route_message``。
"""

from __future__ import annotations

import asyncio
import logging
import queue
import time
from typing import Any, Mapping, Optional
from uuid import uuid4

from ..constants import (
    CHAT_TYPE_GROUP,
    CHAT_TYPE_PRIVATE,
    GATEWAY_NAME,
    LOGGER_NAME,
    WX_MSG_KIND,
)
from ..config import WemaiPluginSettings
from ..shared_types import ContactCache
from .transport import OneBotTransport


_LOGGER_NAME = LOGGER_NAME + ".event_router"


class WemaiEventRouter:
    """将微信消息流转为 MaiBot Host 可消费的事件。"""

    def __init__(
        self,
        *,
        settings: WemaiPluginSettings,
        transport: OneBotTransport,
        inbound_codec: Any,
        gateway_capability: Any,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._inbound_codec = inbound_codec
        self._gateway_capability = gateway_capability
        self._logger = logger or logging.getLogger(_LOGGER_NAME)
        self._contacts = ContactCache(refresh_interval_sec=600)
        self._msg_queue: queue.Queue[Any] = queue.Queue(maxsize=1024)
        self._drain_task: Optional[asyncio.Task[Any]] = None
        self._transport_host: str = f"{settings.server.host}:{settings.server.port}"
        # 与 napcat-adapter 一致的幂等记录，避免对 host 重复 update_state。
        self._last_reported_account: str = ""
        self._last_reported_scope: str = ""
        self._runtime_state_ready: bool = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._enabled = False

    # ----------------------------- 配置变更 -----------------------------

    def apply_settings(self, settings: WemaiPluginSettings) -> None:
        self._settings = settings

    # ----------------------------- 启用/禁用 -----------------------------

    def start(self) -> bool:
        """开启接收消息 + 启动内部 drain 异步任务。"""
        if self._enabled:
            self._logger.info("WeMai 事件路由已在运行")
            return True
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = None
        if self._loop is None:
            self._logger.error("无法获取 asyncio loop；drain 任务无法启动")
            return False
        try:
            self._refresh_contacts_blocking()
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(f"刷新通讯录失败：{exc}")
        ok = self._transport.enable_recv_msg(self._safe_inbound_callback)
        if not ok:
            self._logger.error("WeMai 启用接收消息失败")
            return False
        self._drain_task = self._loop.create_task(self._drain_loop())
        self._enabled = True
        self._logger.info("WeMai 入站事件路由已开启")
        return True

    def stop(self) -> None:
        if not self._enabled:
            return
        self._enabled = False
        try:
            self._transport.disable_recv_msg()
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(f"关闭接收消息时异常：{exc}")
        try:
            self._drain_blocking_now()
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(f"排空消息队列失败：{exc}")
        if self._drain_task is not None and self._loop is not None:
            try:
                self._drain_task.cancel()
            except Exception:  # noqa: BLE001
                pass
        self._drain_task = None
        self._logger.info("WeMai 入站事件路由已停止")

    def is_running(self) -> bool:
        return self._enabled

    # ----------------------------- 主回调 -----------------------------

    def _safe_inbound_callback(self, OneBotEvent: Any) -> None:
        """OneBot 在独立线程上调用此方法——只压入队列，不做 await。"""
        try:
            if self._msg_queue.full():
                self._logger.warning("WeMai 入站队列已满，丢弃消息")
                return
            self._msg_queue.put_nowait(OneBotEvent)
        except Exception as exc:  # noqa: BLE001
            self._logger.error(f"投递消息失败：{exc}")

    async def _drain_loop(self) -> None:
        """在 MaiBot Runner 的 asyncio loop 上持续把消息推送给 Host。"""
        self._logger.info("WeMai drain loop 启动")
        try:
            while self._enabled:
                await asyncio.sleep(0.02)
                drained: list[Any] = []
                try:
                    while True:
                        drained.append(self._msg_queue.get_nowait())
                except queue.Empty:
                    pass
                if not drained:
                    continue
                for OneBotEvent in drained:
                    try:
                        message_dict = self._build_message_dict(OneBotEvent)
                    except Exception as exc:  # noqa: BLE001
                        self._logger.error(f"构造 MessageDict 异常：{exc}", exc_info=True)
                        continue
                    if message_dict is None:
                        continue
                    await self._submit(message_dict)
        except asyncio.CancelledError:
            self._logger.info("WeMai drain loop 被取消")
            raise
        except Exception as exc:  # noqa: BLE001
            self._logger.error(f"WeMai drain loop 异常退出：{exc}", exc_info=True)

    def _drain_blocking_now(self) -> None:
        """关闭前尽量把队列里的剩余消息处理完。"""
        while True:
            try:
                OneBotEvent = self._msg_queue.get_nowait()
            except queue.Empty:
                return
            try:
                self._build_message_dict(OneBotEvent)
            except Exception:  # noqa: BLE001
                pass

    # ----------------------------- 构造 & 上报 -----------------------------

    def _build_message_dict(self, OneBotEvent: Any) -> Optional[dict[str, Any]]:
        settings = self._settings

        if settings.filters.ignore_self_message and getattr(OneBotEvent, "from_self", lambda: False)():
            return None

        msg_kind = WX_MSG_KIND.get(int(getattr(OneBotEvent, "type", 0) or 0), "other")
        if msg_kind not in settings.filters.accepted_msg_types:
            return None

        is_group = bool(getattr(OneBotEvent, "from_group", lambda: False)())
        chat_type = CHAT_TYPE_GROUP if is_group else CHAT_TYPE_PRIVATE

        if is_group:
            roomid = str(getattr(OneBotEvent, "roomid", "") or "")
            if not self._passes_chat_list(
                config_type=settings.chat.group_list_type,
                config_list=settings.chat.group_list,
                key=roomid,
            ):
                return None
            sender_wxid = str(getattr(OneBotEvent, "sender", "") or "")
        else:
            sender_wxid = str(getattr(OneBotEvent, "sender", "") or "")
            if not self._passes_chat_list(
                config_type=settings.chat.private_list_type,
                config_list=settings.chat.private_list,
                key=sender_wxid,
            ):
                return None

        self_wxid = self._transport.self_wxid() or self._transport.get_self_wxid() or ""
        user_nickname = self._contacts.lookup_nickname(sender_wxid) or sender_wxid
        group_id = str(getattr(OneBotEvent, "roomid", "") or "") if is_group else ""

        raw_message, plain_text = self._inbound_codec.build_segments(
            OneBotEvent=OneBotEvent, msg_kind=msg_kind, self_wxid=self_wxid, group_id=group_id,
        )
        if not raw_message:
            return None

        timestamp_seconds = float(getattr(OneBotEvent, "ts", 0) or time.time())
        message_id = str(getattr(OneBotEvent, "id", "") or f"wemai-{uuid4().hex}")

        additional_config: dict[str, Any] = {
            "self_id": self_wxid,
            "wemai_msg_kind": msg_kind,
            "wemai_chat_type": chat_type,
        }
        if not is_group:
            additional_config["platform_io_target_user_id"] = sender_wxid
        else:
            additional_config["platform_io_target_group_id"] = group_id

        is_at_self = False
        try:
            is_at_self = bool(getattr(OneBotEvent, "is_at", lambda *_: False)(self_wxid)) if is_group else False
        except Exception:  # noqa: BLE001
            is_at_self = False

        message_info: dict[str, Any] = {
            "user_info": {
                "user_id": sender_wxid,
                "user_nickname": user_nickname,
                "user_cardname": self._contacts.lookup_remark(sender_wxid) or None,
            },
            "additional_config": additional_config,
        }
        if is_group:
            message_info["group_info"] = {
                "group_id": group_id,
                "group_name": self._contacts.lookup_room_alias(group_id) or f"group_{group_id}",
            }

        return {
            "message_id": message_id,
            "timestamp": str(timestamp_seconds),
            "platform": "wechat",
            "message_info": message_info,
            "raw_message": raw_message,
            "is_mentioned": is_at_self,
            "is_at": is_at_self,
            "is_emoji": msg_kind == "emotion",
            "is_picture": msg_kind == "image",
            "is_command": plain_text.startswith("/"),
            "is_notify": False,
            "session_id": "",
            "processed_plain_text": plain_text,
            "display_message": plain_text,
        }

    async def _submit(self, message_dict: dict[str, Any]) -> None:
        gateway = self._gateway_capability
        if gateway is None:
            self._logger.debug(f"GatewayCapability 未注入，丢弃：{message_dict.get('message_id')}")
            return
        info = message_dict.get("message_info") if isinstance(message_dict, Mapping) else None
        info = info if isinstance(info, Mapping) else {}
        additional = info.get("additional_config") if isinstance(info, Mapping) else {}
        additional = additional if isinstance(additional, Mapping) else {}
        # 与 napcat-adapter 对齐：route_metadata 用 self_id + connection_id 描述路由键。
        # Host 端会把 route_metadata 与最近一次 update_state 上报的 account_id/scope 合成 RouteKey。
        self_id = str(additional.get("self_id") or "").strip()
        route_metadata: dict[str, Any] = {}
        if self_id and self_id != "wemai-diagnostic":
            route_metadata["self_id"] = self_id
        # 把 connection_id 标记为 OneBot 服务端的 host:port；诊断注入场景用 wemai-diagnostic。
        connection_id = "wemai-diagnostic" if self_id == "wemai-diagnostic" else self._transport_host
        route_metadata["connection_id"] = connection_id

        # 与 napcat-adapter 对齐：每条消息入站前 idempotent 上报 gateway ready 状态，
        # 避免 host 在 runtime 重启后清空了状态导致 route_message 被拒。
        try:
            await self._ensure_gateway_ready(self_id, connection_id)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(f"上报 gateway ready 状态失败：{exc}")
        external_message_id = str(message_dict.get("message_id") or "").strip()
        try:
            accepted = await gateway.route_message(
                gateway_name=GATEWAY_NAME,
                message=message_dict,
                route_metadata=route_metadata,
                external_message_id=external_message_id,
                dedupe_key=external_message_id or f"wemai-{time.time_ns()}",
            )
            if not accepted:
                self._logger.warning(
                    f"Host 未接受消息：{external_message_id}"
                    f" route_metadata={route_metadata}"
                )
        except Exception as exc:  # noqa: BLE001
            self._logger.error(f"提交消息到 Host 失败：{exc}", exc_info=True)

    # ----------------------------- 联系人 -----------------------------

    def _refresh_contacts_blocking(self) -> None:
        contacts = self._transport.get_contacts()
        if isinstance(contacts, list):
            self._contacts.bulk_load(contacts)

    # ----------------------------- Gateway ready 上报 -----------------------------

    async def _ensure_gateway_ready(self, account_id: str, scope: str) -> None:
        """幂等地上报 gateway ready 状态，避免 host 因 runtime 重启而把状态清空。

        仿照 napcat-adapter 的 ``report_connected``：若 account_id / scope 与上次一致，
        就跳过 update_state，避免每次消息都打一次 IPC。

        account_id 必须用**机器人自身**的 wxid（napcat 是 get_login_info().user_id），而不是发送方。
        """
        real_self_wxid = ""
        try:
            if self._transport is not None:
                real_self_wxid = self._transport.self_wxid() or ""
        except Exception:  # noqa: BLE001
            real_self_wxid = ""
        if real_self_wxid:
            normalized_account = real_self_wxid
            normalized_scope = "*"
        else:
            normalized_account = str(account_id or "").strip() or "anonymous"
            normalized_scope = str(scope or "").strip() or "wemai-pending"
        if (
            self._runtime_state_ready
            and self._last_reported_account == normalized_account
            and self._last_reported_scope == normalized_scope
        ):
            return
        accepted = await self._gateway_capability.update_state(
            gateway_name=GATEWAY_NAME,
            ready=True,
            platform="wechat",
            account_id=normalized_account,
            scope=normalized_scope,
            metadata={
                "protocol": "ob11",
                "diagnostic": normalized_account == "wemai-diagnostic",
            },
        )
        if not accepted:
            raise RuntimeError(f"host 未接受 update_state(ready=True) account_id={normalized_account}")
        self._runtime_state_ready = True
        self._last_reported_account = normalized_account
        self._last_reported_scope = normalized_scope
        self._logger.info(
            f"wemai_gateway 已激活路由: platform=wechat account_id={normalized_account}"
            f" scope={normalized_scope}"
        )

    # ----------------------------- 自检注入 -----------------------------

    async def inject_diagnostic_message(
        self,
        *,
        sender_wxid: str,
        text: str,
        timestamp: Optional[float] = None,
        message_id: Optional[str] = None,
    ) -> bool:
        """构造一条模拟微信私聊消息推送给 MaiBot。

        仅用于诊断自检；当 ``OneBot`` 不可用或微信未降级时，
        让 MaiBot 主机端验证 ``route_message → chat pipeline → MessageGateway outbound`` 路径。
        """
        ts = float(timestamp or time.time())
        raw_message = [{"type": "text", "data": text}]
        plain_text = str(text)
        mid = str(message_id or f"wemai-diag-{int(ts * 1000)}")
        message_info = {
            "user_info": {
                "user_id": sender_wxid,
                "user_nickname": sender_wxid,
                "user_cardname": None,
            },
            "additional_config": {
                "self_id": "wemai-diagnostic",
                "wemai_msg_kind": "diagnostic_inject",
                "wemai_chat_type": "private",
                "platform_io_target_user_id": sender_wxid,
                "wemai_diagnostic": True,
            },
        }
        message_dict: dict[str, Any] = {
            "message_id": mid,
            "timestamp": str(ts),
            "platform": "wechat",
            "message_info": message_info,
            "raw_message": raw_message,
            "is_mentioned": False,
            "is_at": False,
            "is_emoji": False,
            "is_picture": False,
            "is_command": plain_text.startswith("/"),
            "is_notify": False,
            "session_id": "",
            "processed_plain_text": plain_text,
            "display_message": plain_text,
        }
        await self._submit(message_dict)
        return True

    @staticmethod
    def _passes_chat_list(
        *,
        config_type: str,
        config_list: list[str],
        key: str,
    ) -> bool:
        if not config_list:
            return True
        if not key:
            return False
        if config_type == "whitelist":
            return key in config_list
        return key not in config_list


__all__ = ["WemaiEventRouter"]
