"""WeMai WeMai 适配器插件。"""

from __future__ import annotations

from .plugin import WemaiAdapterPlugin


def create_plugin() -> WemaiAdapterPlugin:
    """创建插件实例。

    Returns:
        WemaiAdapterPlugin: WeMai 适配器插件实例。
    """
    return WemaiAdapterPlugin()


__all__ = ["WemaiAdapterPlugin", "create_plugin"]
