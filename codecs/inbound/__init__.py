"""WeMai 入站 codec：把 OneBot ``OneBotEvent`` 转换为 MaiBot 消息段。"""

from __future__ import annotations

from typing import Any, List, Tuple

from ...constants import WS_MSG_IMAGE, WS_MSG_VOICE, WS_MSG_VIDEO, WS_MSG_FILE, WS_MSG_EMOTION


def _text_segment(text: str) -> dict[str, Any]:
    return {"type": "text", "data": text}


def _image_segment(path: str) -> dict[str, Any]:
    return {"type": "image", "data": {"path": path, "url": "", "file_id": path}}


def _voice_segment(path: str) -> dict[str, Any]:
    return {"type": "voice", "data": {"path": path}}


def _video_segment(path: str) -> dict[str, Any]:
    return {"type": "video", "data": {"path": path}}


def _file_segment(path: str) -> dict[str, Any]:
    return {"type": "file", "data": {"path": path}}


def _emotion_segment(path: str) -> dict[str, Any]:
    return {"type": "emoji", "data": {"path": path, "emoji_id": "", "package_id": ""}}


def _other_segment(kind: str) -> dict[str, Any]:
    return {"type": "text", "data": f"[unsupported:{kind or 'other'}]"}


class WemaiInboundCodec:
    """将 OneBot 消息段映射到 MaiBot 标准 raw_message 段。"""

    def build_segments(
        self,
        *,
        OneBotEvent: Any,
        msg_kind: str,
        self_wxid: str,
        group_id: str,
    ) -> Tuple[List[dict[str, Any]], str]:
        """根据 OneBotEvent 产生 raw_message 段和可显示的纯文本。

        Args:
            OneBotEvent: ``OneBotEvent`` 实例。
            msg_kind: 已归类的消息类型 (``text``、``image`` 等)。
            self_wxid: 当前登录账户的 wxid。
            group_id: 群消息的 roomid，私聊为空。

        Returns:
            ``(raw_message_segments, plain_text)``。
        """
        raw_message: List[dict[str, Any]] = []
        plain_text_parts: List[str] = []
        content = str(getattr(OneBotEvent, "content", "") or "")
        sender = str(getattr(OneBotEvent, "sender", "") or "")
        is_group = bool(group_id)

        if msg_kind == "text":
            stripped = content.strip()
            if is_group and self_wxid and stripped.startswith("@所有人") or self._is_at_self(OneBotEvent, self_wxid):
                raw_message.append({"type": "at", "data": {"target_user_id": "all" if stripped.startswith("@所有人") else self_wxid}})
                plain_text_parts.append("@所有人" if stripped.startswith("@所有人") else f"@{sender}")
                content = stripped.replace("@所有人", "").lstrip()
            text_value = content.strip()
            if not text_value:
                text_value = "[empty text]"
            raw_message.append(_text_segment(text_value))
            plain_text_parts.append(text_value)
        elif msg_kind == WS_MSG_IMAGE:
            extra = str(getattr(OneBotEvent, "extra", "") or "")
            path = extra.strip() or str(getattr(OneBotEvent, "thumb", "") or "")
            if path:
                raw_message.append(_image_segment(path))
                plain_text_parts.append("[image]")
        elif msg_kind == WS_MSG_VOICE:
            extra = str(getattr(OneBotEvent, "extra", "") or "")
            if extra:
                raw_message.append(_voice_segment(extra))
                plain_text_parts.append("[voice]")
        elif msg_kind == WS_MSG_VIDEO:
            extra = str(getattr(OneBotEvent, "extra", "") or "")
            thumb = str(getattr(OneBotEvent, "thumb", "") or "")
            if extra:
                raw_message.append(_video_segment(extra))
                if thumb:
                    raw_message.append(_image_segment(thumb))
                plain_text_parts.append("[video]")
        elif msg_kind == WS_MSG_FILE:
            extra = str(getattr(OneBotEvent, "extra", "") or "")
            if extra:
                raw_message.append(_file_segment(extra))
                plain_text_parts.append(f"[file] {extra}")
        elif msg_kind == WS_MSG_EMOTION:
            extra = str(getattr(OneBotEvent, "extra", "") or "")
            if extra:
                raw_message.append(_emotion_segment(extra))
                plain_text_parts.append("[emotion]")
        else:
            extra = str(getattr(OneBotEvent, "extra", "") or "")
            fallback = content.strip() or f"[unsupported:{msg_kind}]"
            raw_message.append(_text_segment(fallback))
            plain_text_parts.append(fallback)
            if extra:
                plain_text_parts.append(f"[extra] {extra}")

        plain_text = "".join(part for part in plain_text_parts if part).strip() or "[empty]"
        return raw_message, plain_text

    @staticmethod
    def _is_at_self(OneBotEvent: Any, self_wxid: str) -> bool:
        if not self_wxid:
            return False
        try:
            return bool(getattr(OneBotEvent, "is_at", lambda *_: False)(self_wxid))
        except Exception:  # noqa: BLE001
            return False


__all__ = ["WemaiInboundCodec"]
