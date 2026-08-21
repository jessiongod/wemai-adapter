"""WeMai 适配器共享类型。"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional


class ContactCache:
    """简单的微信联系人缓存，存 ``wxid -> (nickname, remark)`` 映射。

    WeMai 的 ``get_contacts`` 返回全表，遍历一次足够。群信息不在此表里，
    MaiBot 端需要时可通过 ``get_chatroom_members``/``get_member_nickname``（如果有）
    再查询，这里先支持昵称/备注的简单查询与群备注存根。
    """

    def __init__(self, refresh_interval_sec: int = 600) -> None:
        self._lock = threading.RLock()
        self._user_map: dict[str, tuple[str, str]] = {}
        self._room_alias: dict[str, str] = {}
        self._refresh_interval = refresh_interval_sec
        self._last_refresh_at: float = 0.0

    def bulk_load(self, contacts: list[dict[str, Any]]) -> None:
        with self._lock:
            self._user_map.clear()
            for item in contacts:
                if not isinstance(item, dict):
                    continue
                wxid = str(item.get("wxid") or "").strip()
                if not wxid:
                    continue
                nickname = str(item.get("name") or "").strip()
                remark = str(item.get("remark") or "").strip()
                self._user_map[wxid] = (nickname, remark)
            self._last_refresh_at = time.time()

    def upsert_user(self, wxid: str, nickname: str = "", remark: str = "") -> None:
        with self._lock:
            cur_n, cur_r = self._user_map.get(wxid, ("", ""))
            self._user_map[wxid] = (
                nickname or cur_n or wxid,
                remark or cur_r,
            )

    def set_room_alias(self, roomid: str, alias: str) -> None:
        with self._lock:
            self._room_alias[roomid] = alias

    def lookup_nickname(self, wxid: str) -> Optional[str]:
        with self._lock:
            pair = self._user_map.get(wxid)
            if pair is None:
                return None
            return pair[0] or None

    def lookup_remark(self, wxid: str) -> Optional[str]:
        with self._lock:
            pair = self._user_map.get(wxid)
            if pair is None:
                return None
            return pair[1] or None

    def lookup_room_alias(self, roomid: str) -> Optional[str]:
        with self._lock:
            return self._room_alias.get(roomid)

    def is_stale(self) -> bool:
        with self._lock:
            if self._refresh_interval <= 0:
                return False
            return (time.time() - self._last_refresh_at) > self._refresh_interval

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._user_map)


__all__ = ["ContactCache"]
