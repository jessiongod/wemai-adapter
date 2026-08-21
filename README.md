# WeMai Adapter 微信适配器

MaiBot 的微信个人号适配器插件，通过 OneBot v11 WebSocket 协议连接微信桥接服务（WeFlow + bridge），把微信的私聊和群聊消息桥接到 MaiBot。

方案特点：

1. 无需 DLL 注入、无需 Hook 微信进程，以独立进程方式与微信桥接服务通信
2. 支持最新版微信 4.1.x（实测 4.1.10.27）
3. 支持私聊与群聊，群聊默认在被 @ 时回复
4. 接收消息走 WeFlow（只读微信本地数据库），发送消息走 UIA 键盘模拟

## 架构

```
微信 4.1.x ←→ WeFlow(读微信本地DB, SSE推消息) ←→ bridge(OneBot v11 WS) ←→ 本插件(MaiBot)
      ↑                                                                          ↓
      └──────────────── UIA 键盘发送 ←── bridge ←── 麦麦回复 ←────────────────────┘
```

## 安装

1. 将本插件目录复制到 MaiBot 的 `plugins/` 目录：

```
<MaiBot>/plugins/wemai-adapter/
```

2. 在 MaiBot OneKey 中启用插件并重启。

## 配置

编辑 `config.toml`：

```toml
[server]
host = "127.0.0.1"
port = 7999
token = "你的随机token"
```

其中 `token` 需要与 bridge 配置中的 `ob_server_token` 保持一致。

## 前置依赖

本插件是微信桥接方案的一部分，还需要以下组件配合：

1. WeFlow：读取微信本地数据库并提供 HTTP API + SSE 推送，监听端口 5031
2. bridge：桥接程序（OneBot v11 WS 服务端，端口 7999 + Web 面板 8766），负责收 WeFlow 消息、转 OneBot 事件推给 MaiBot，并把回复用 UIA 键盘模拟发到微信

## 行为说明

- 群聊：默认 mention 模式，被 @ 才回复；bridge 的 `group_reply_mode` 可切换为 `all` 让麦麦主动参与聊天
- 群名映射：微信搜索框无法直接搜索 `xxx@chatroom` 形式的群 ID，需在 bridge 的 `group_name_map` 中把群 ID 映射为真实群名（群名建议使用简短且无歧义的名称，避免触发微信的网页搜索）

## 许可证

GPL-v3.0-or-later
