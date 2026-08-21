"""WeMai codec 编解码层。"""

from .inbound import WemaiInboundCodec
from .outbound import WemaiOutboundCodec

__all__ = ["WemaiInboundCodec", "WemaiOutboundCodec"]
