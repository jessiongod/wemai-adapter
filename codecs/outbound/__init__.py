"""WeMai 出站 codec：把 MaiBot 标准消息段转换为 OneBot send_msg 调用参数。"""

from __future__ import annotations

import os
import re
from typing import Any, List, Mapping, Tuple


_MAX_IMAGE_BYTES = 25 * 1024 * 1024
"""微信图片大小上限保护位，超过会被拒；预留 25MB。"""


class WemaiOutboundCodec:
    """MaiBot → OneBot 出站编码器。

    不同段类型映射到不同的 OneBot 消息段；当包含多个段时，会逐段独立发送。
    """

    def __init__(self, transport: Any) -> None:
        self._transport = transport

    def send_message(
        self,
        raw_message: Any,
        *,
        user_id: str,
        group_id: str = "",
    ) -> List[dict[str, Any]]:
        """把 Host 段列表通过 OneBot send_msg 发送。

        Args:
            raw_message: Host 提供的段列表（list[Mapping]）。
            user_id: 私聊目标 wxid，群聊时填 ``@all`` 群成员 wxid 或为空。
            group_id: 群消息的目标 roomid；非空时表示群聊。

        Returns:
            每段一个 ``{type, status}`` 元素的状态列表。
        """
        receiver = group_id if group_id else user_id
        if not receiver:
            return [{"type": "config", "status": -1, "error": "missing receiver"}]

        results: List[dict[str, Any]] = []
        if not isinstance(raw_message, list):
            results.append({"type": "config", "status": -2, "error": "raw_message is not a list"})
            return results

        segments = [seg for seg in raw_message if isinstance(seg, Mapping)]
        if not segments:
            results.append({"type": "config", "status": -3, "error": "empty segments"})
            return results

        for seg in segments:
            seg_type = str(seg.get("type") or "").strip().lower()
            data = seg.get("data")
            if seg_type == "reply":
                # 引用回复段：微信 UIA 无法构造引用，跳过
                continue
            elif seg_type == "text":
                results.append(self._send_text(data, receiver))
            elif seg_type == "image":
                results.append(self._send_image(data, receiver))
            elif seg_type == "voice":
                results.append(self._send_file(data, receiver))
            elif seg_type in {"video", "file"}:
                results.append(self._send_file(data, receiver))
            elif seg_type in {"emoji", "emotion"}:
                results.append(self._send_emotion(data, receiver))
            elif seg_type == "at":
                results.append(self._send_at(data, segments, receiver))
            else:
                results.append(self._send_text(f"[unsupported:{seg_type}]", receiver))
        return results

    def _send_text(self, data: Any, receiver: str) -> dict[str, Any]:
        text = str(data or "").strip()
        if not text:
            return {"type": "text", "status": -10, "error": "empty text"}
        try:
            status = self._transport.send_text(text, receiver)
        except Exception as exc:
            return {"type": "text", "status": -11, "error": str(exc)}
        return {"type": "text", "status": int(status)}

    def _send_image(self, data: Any, receiver: str) -> dict[str, Any]:
        path = self._resolve_media_path(data)
        if not isinstance(path, str):
            return {"type": "image", "status": path, "error": "invalid path"}
        try:
            status = self._transport.send_image(path, receiver)
        except Exception as exc:
            return {"type": "image", "status": -21, "error": str(exc)}
        return {"type": "image", "status": int(status), "path": path}

    def _send_file(self, data: Any, receiver: str) -> dict[str, Any]:
        path = self._resolve_media_path(data)
        if not isinstance(path, str):
            return {"type": "file", "status": path, "error": "invalid path"}
        try:
            status = self._transport.send_file(path, receiver)
        except Exception as exc:
            return {"type": "file", "status": -31, "error": str(exc)}
        return {"type": "file", "status": int(status), "path": path}

    def _send_emotion(self, data: Any, receiver: str) -> dict[str, Any]:
        path = self._resolve_media_path(data)
        if not isinstance(path, str):
            return {"type": "emotion", "status": path, "error": "invalid path"}
        try:
            status = self._transport.send_emotion(path, receiver)
        except Exception as exc:
            return {"type": "emotion", "status": -41, "error": str(exc)}
        return {"type": "emotion", "status": int(status), "path": path}

    def _send_at(self, data: Any, all_segments: List[Mapping[str, Any]], receiver: str) -> dict[str, Any]:
        if not isinstance(data, Mapping):
            return {"type": "at", "status": -50, "error": "invalid at data"}
        target = str(data.get("target_user_id") or data.get("qq") or "").strip()
        if not target or "@" in target:
            aters = "notify@all"
        else:
            aters = target

        following_text = self._collect_following_text(all_segments)
        text = ("@" + aters + " " + following_text).strip() if following_text else "@" + aters
        try:
            status = self._transport.send_text(text, receiver, aters=aters)
        except Exception as exc:
            return {"type": "at", "status": -51, "error": str(exc)}
        return {"type": "at", "status": int(status), "aters": aters}

    @staticmethod
    def _collect_following_text(segments: List[Mapping[str, Any]]) -> str:
        chunks: List[str] = []
        started = False
        for seg in segments:
            seg_type = str(seg.get("type") or "").strip().lower()
            if seg_type == "at":
                started = True
                continue
            if not started:
                continue
            if seg_type == "text":
                chunks.append(str(seg.get("data") or ""))
        return "".join(chunks).strip()

    @staticmethod
    def _resolve_media_path(data: Any) -> Any:
        if isinstance(data, Mapping):
            candidates = [
                data.get("path"),
                data.get("file"),
                data.get("url"),
            ]
            for candidate in candidates:
                path = WemaiOutboundCodec._coerce_existing_path(candidate)
                if path:
                    return path
            return -101
        path = WemaiOutboundCodec._coerce_existing_path(data)
        if path:
            return path
        return -101

    @staticmethod
    def _coerce_existing_path(value: Any) -> str | None:
        if not value:
            return None
        text = str(value).strip()
        if not text:
            return None
        if text.startswith("base64://"):
            return None
        if text.startswith("http://") or text.startswith("https://"):
            return text  # 图片由微信侧自动下载
        try:
            normalized = os.path.normpath(text)
            if os.path.isfile(normalized):
                return normalized
        except Exception:  # noqa: BLE001
            return None
        return None


__all__ = ["WemaiOutboundCodec"]
