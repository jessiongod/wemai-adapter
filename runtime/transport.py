"""WeMai OneBot v11 WebSocket 客户端传输层（对接 Akasha-WeChat bridge）。

替代原 WcfTransport：不注入微信，而是作为 OneBot v11 WS 客户端连接
Akasha-WeChat bridge（其内部接 WeFlow 收消息 + UIA 发消息）。

接口与 WcfTransport 保持一致：
- start() / stop() / is_running()
- self_wxid() / is_login() / get_self_wxid() / get_user_info() / get_contacts()
- enable_recv_msg(on_message) / disable_recv_msg()
- send_text(msg, receiver, aters) / send_image / send_file / send_emotion / send_pat_msg

OneBot 事件 → OneBotEvent 兼容对象（.content/.sender/.extra/.thumb/.is_at()）
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any, Callable, Optional

_LOGGER_NAME = "wemai.transport.ob11"


class OneBotEventCompat:
    """把 OneBot 消息事件包装成与 OneBot OneBotEvent 兼容的对象。"""

    def __init__(self, event: dict) -> None:
        self._event = event
        self._content = event.get("raw_message", "") or ""
        self._message = event.get("message", [])
        self._sender = event.get("sender", {})
        self._self_id = event.get("self_id", "")

    # ---- OneBot OneBotEvent 兼容属性 ----
    @property
    def content(self) -> str:
        return self._content

    @property
    def sender(self) -> str:
        """发送者标识（群聊里是发言成员）。"""
        uid = str(self._sender.get("user_id", "")) if isinstance(self._sender, dict) else ""
        return uid or self._content or "unknown"

    @property
    def extra(self) -> str:
        return json.dumps(self._event, ensure_ascii=False)

    @property
    def thumb(self) -> str:
        return ""

    @property
    def type(self) -> int:
        return 1  # 文本

    @property
    def ts(self) -> int:
        """消息时间戳（秒）。"""
        try:
            return int(self._event.get("time", 0) or 0)
        except Exception:
            return 0

    @property
    def id(self) -> str:
        """消息 ID。"""
        msg_id = self._event.get("message_id", "")
        return str(msg_id) if msg_id else ""

    @property
    def roomid(self) -> str:
        if self._event.get("message_type") == "group":
            return str(self._event.get("group_id", ""))
        return ""

    @property
    def is_group(self) -> bool:
        return self._event.get("message_type") == "group"

    def from_group(self) -> bool:
        """兼容 OneBot OneBotEvent 的群聊判断方法。"""
        return self._event.get("message_type") == "group"

    def from_self(self) -> bool:
        """是否机器人自己发的消息。"""
        try:
            self_id = str(self._event.get("self_id", ""))
            sender_id = str(self._sender.get("user_id", "")) if isinstance(self._sender, dict) else ""
            return bool(self_id) and self_id == sender_id
        except Exception:
            return False

    def is_at(self, wxid: str) -> bool:
        """检测消息里是否 @ 了机器人。"""
        # 优先用 bridge 已检测的 is_mentioned 标志（跨进程 hash 不可靠）
        mentioned = self._event.get("is_mentioned")
        if mentioned is not None:
            return bool(mentioned)
        if not wxid:
            return False
        for seg in self._message:
            if not isinstance(seg, dict):
                continue
            if seg.get("type") == "at":
                data = seg.get("data", {})
                if str(data.get("qq", "")) == str(wxid):
                    return True
                if str(data.get("user_id", "")) == str(wxid):
                    return True
                if data.get("qq") == "all" or data.get("user_id") == "all":
                    return True
        # 兜底：文本 @昵称
        return f"@{wxid}" in self._content

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OneBotEventCompat {self._content[:30]!r}>"


class OneBotTransport:
    """OneBot v11 WS 客户端：连接 Akasha-WeChat bridge 服务端。"""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 7999,
        token: str = "",
        logger: Optional[logging.Logger] = None,
        reconnect_delay: float = 3.0,
    ) -> None:
        self._host = host
        self._port = port
        self._token = token
        self._logger = logger or logging.getLogger(_LOGGER_NAME)
        self._reconnect_delay = reconnect_delay
        self._ws = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._on_message: Optional[Callable[[Any], None]] = None
        self._connected = threading.Event()
        # 发送响应等待表
        self._pending: dict[str, queue.Queue] = {}
        self._pending_lock = threading.Lock()
        self._self_wxid_cache = ""
        self._contacts_cache: list[dict[str, Any]] = []

    # ----------------------------- 生命周期 -----------------------------

    def start(self) -> bool:
        """启动 WS 客户端后台线程。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._logger.info("OneBotTransport 已在运行")
                return True
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run_loop, daemon=True, name="wemai-ob11-ws"
            )
            self._thread.start()
            # 等待首次连接（最多 10 秒）
            self._connected.wait(timeout=10)
            return self._connected.is_set()

    def stop(self) -> None:
        """停止 WS 客户端。"""
        with self._lock:
            self._stop.set()
            ws = self._ws
            if ws is not None:
                try:
                    import asyncio

                    loop = getattr(ws, "_loop", None)
                    if loop and loop.is_running():
                        asyncio.run_coroutine_threadsafe(ws.close(), loop)
                except Exception:
                    pass
            self._ws = None
            self._connected.clear()
            self._thread = None
            self._logger.info("OneBotTransport 已停止")

    def is_running(self) -> bool:
        return self._connected.is_set()

    # ----------------------------- 账户信息 -----------------------------

    def self_wxid(self) -> str:
        return self._self_wxid_cache or ""

    def is_login(self) -> bool:
        return self._connected.is_set()

    def get_self_wxid(self) -> str:
        return self._self_wxid_cache or ""

    def get_user_info(self) -> dict[str, Any]:
        return {"wxid": self._self_wxid_cache, "nickname": "wechat-bot"}

    def get_contacts(self) -> list[dict[str, Any]]:
        return self._contacts_cache

    def get_msg_types(self) -> dict[int, str]:
        return {1: "文本消息", 3: "图片消息", 34: "语音消息"}

    # ----------------------------- 消息接收 -----------------------------

    def enable_recv_msg(self, on_message: Callable[[Any], None]) -> bool:
        self._on_message = on_message
        return True

    def disable_recv_msg(self) -> None:
        self._on_message = None

    # ----------------------------- 出站发送 -----------------------------

    def _is_group_receiver(self, receiver: str) -> bool:
        """判断 receiver 是否为群聊（群 roomid 以 @chatroom 结尾）。"""
        return isinstance(receiver, str) and receiver.endswith("@chatroom")

    def send_text(self, msg: str, receiver: str, aters: str = "") -> int:
        """发送文本。receiver 是 MaiBot 给的 user_id/group_id。"""
        if self._is_group_receiver(receiver):
            return self._send_action(
                "send_msg",
                {"message_type": "group", "group_id": receiver, "message": [
                    {"type": "text", "data": {"text": msg}}
                ]},
            )
        return self._send_action(
            "send_msg",
            {"message_type": "private", "user_id": receiver, "message": [
                {"type": "text", "data": {"text": msg}}
            ]},
        )

    def send_image(self, path: str, receiver: str) -> int:
        if self._is_group_receiver(receiver):
            return self._send_action(
                "send_msg",
                {"message_type": "group", "group_id": receiver, "message": [
                    {"type": "image", "data": {"file": path}}
                ]},
            )
        return self._send_action(
            "send_msg",
            {"message_type": "private", "user_id": receiver, "message": [
                {"type": "image", "data": {"file": path}}
            ]},
        )

    def send_file(self, path: str, receiver: str) -> int:
        return self.send_image(path, receiver)

    def send_emotion(self, path: str, receiver: str) -> int:
        return self._send_action(
            "send_msg",
            {"message_type": "private", "user_id": receiver, "message": [
                {"type": "face", "data": {"id": 0}}
            ]},
        )

    def send_xml(self, receiver: str, xml: str, xml_type: int, path: Optional[str] = None) -> int:
        return -1

    def send_rich_text(
        self,
        *,
        name: str, account: str, title: str, digest: str, url: str, thumburl: str, receiver: str,
    ) -> int:
        return self._send_action(
            "send_msg",
            {"message_type": "private", "user_id": receiver, "message": [
                {"type": "text", "data": {"text": f"{title}\n{digest}\n{url}"}}
            ]},
        )

    def send_pat_msg(self, roomid: str, wxid: str) -> int:
        return -1

    def forward_msg(self, msg_id: int, receiver: str) -> int:
        return -1

    # ----------------------------- 内部实现 -----------------------------

    def _id_of(self, contact: str) -> int:
        """把联系名映射为稳定整数 ID（与 bridge 的 _wxid_to_int 语义一致）。"""
        return abs(hash(contact)) % (2 ** 31)

    def _send_action(self, action: str, params: dict[str, Any]) -> int:
        import uuid

        echo = uuid.uuid4().hex
        q: queue.Queue = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[echo] = q
        payload = {"action": action, "params": params, "echo": echo}
        ok = self._ws_send_json(payload)
        if not ok:
            with self._pending_lock:
                self._pending.pop(echo, None)
            return -1
        try:
            resp = q.get(timeout=15)
        except queue.Empty:
            resp = None
        finally:
            with self._pending_lock:
                self._pending.pop(echo, None)
        if resp is None:
            return -1
        # MaiBot 判定发送成功 = status == 0；bridge 返回 {"status":"ok","retcode":0} 即成功。
        # 注意：bridge 的 data.message_id 是随机数，不能用作成功判定。
        retcode = resp.get("retcode", -1)
        status = resp.get("status", "")
        if retcode == 0 or status == "ok":
            return 0
        return -1

    def _ws_send_json(self, payload: dict[str, Any]) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            import asyncio

            loop = getattr(ws, "_loop", None)
            if loop is None or not loop.is_running():
                return False
            fut = asyncio.run_coroutine_threadsafe(
                ws.send(json.dumps(payload, ensure_ascii=False)), loop
            )
            fut.result(timeout=5)
            return True
        except Exception as exc:
            self._logger.warning(f"WS 发送失败: {exc}")
            return False

    def _run_loop(self) -> None:
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._client_main(loop))
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _client_main(self, loop) -> None:
        import websockets

        url = f"ws://{self._host}:{self._port}"
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        self._logger.info(f"[OB11] 连接 bridge: {url}")

        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    url, additional_headers=headers, max_size=16 * 1024 * 1024
                ) as ws:
                    ws._loop = loop  # type: ignore[attr-defined]
                    self._ws = ws
                    self._connected.set()
                    self._logger.info("[OB11] ✅ 已连接 bridge")
                    # 拉取登录信息
                    try:
                        await self._fetch_login_info(loop)
                    except Exception:
                        pass
                    try:
                        async for raw in ws:
                            try:
                                data = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            self._dispatch(loop, data)
                    except Exception:
                        pass
            except Exception as exc:
                self._logger.warning(f"[OB11] 连接断开/失败: {exc}")
            finally:
                self._ws = None
                self._connected.clear()
                if self._stop.is_set():
                    break
                for _ in range(int(self._reconnect_delay * 2)):
                    if self._stop.is_set():
                        break
                    time.sleep(0.5)

    async def _fetch_login_info(self, loop) -> None:
        import uuid

        echo = uuid.uuid4().hex
        q: queue.Queue = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[echo] = q
        ws = self._ws
        if ws is None:
            return
        await ws.send(json.dumps({"action": "get_login_info", "params": {}, "echo": echo}))
        try:
            resp = await asyncio.get_running_loop().run_in_executor(None, q.get, 5)
            data = resp.get("data", {})
            self._self_wxid_cache = str(data.get("user_id", "")) or self._self_wxid_cache
            self._logger.info(f"[OB11] 登录信息: user_id={self._self_wxid_cache}")
        except Exception:
            pass
        finally:
            with self._pending_lock:
                self._pending.pop(echo, None)

    def _dispatch(self, loop, data: dict) -> None:
        """处理收到的消息（事件或响应）。"""
        # 响应（有 echo）
        echo = data.get("echo")
        if echo:
            with self._pending_lock:
                q = self._pending.get(echo)
                if q is not None:
                    try:
                        q.put_nowait(data)
                    except queue.Full:
                        pass
                    return

        # 事件
        if data.get("post_type") == "message":
            OneBotEvent = OneBotEventCompat(data)
            cb = self._on_message
            if cb is not None:
                try:
                    cb(OneBotEvent)
                except Exception as exc:
                    self._logger.error(f"消息回调异常: {exc}")


__all__ = ["OneBotTransport", "OneBotEventCompat"]
