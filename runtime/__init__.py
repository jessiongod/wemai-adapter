"""WeMai 运行时层。"""

from .transport import OneBotTransport

__all__ = ["OneBotTransport", "WemaiEventRouter"]

# 延迟导入，避免循环依赖
def __getattr__(name):
    if name == "WemaiEventRouter":
        from .event_router import WemaiEventRouter
        return WemaiEventRouter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
