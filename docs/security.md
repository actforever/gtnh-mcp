# 身份与工具权限

## 权限表

| MCP 工具 | 允许群内成员 | 配置中的管理员 |
| --- | --- | --- |
| `list_players`, `announce`, `save_world` | 是 | 是 |
| `list_backups` | 是 | 是 |
| `restore_status` | 本人任务 | 全部任务 |
| `list_whitelist`, `add_whitelist`, `remove_whitelist` | 否 | 是 |
| `request_restore`, `request_undo_restore` | 否 | 是 |
| `confirm_restore` | 否 | 原申请人在原群发送确认指令 |

无任意 RCON、shell、任意文件路径或容器选择工具。公告只允许最多 300 字符的单行文本；玩家名只能由 1–16 位英文字母、数字和下划线组成。

## 信任边界

AstrBot 插件直接从消息事件读取 `get_platform_name()`、`get_group_id()`、`get_sender_id()`。不读取消息里的身份声明，不接受 LLM 传入身份或角色。私聊拒绝调用。

QQ 官方接入复用这些统一事件接口。部署时在目标群用 `/gtnh_identity` 获取实际平台、群和用户字符串，不把开放平台身份替换成普通 QQ 号/群号；管理员确认仍须匹配原申请的完整平台、群、用户。QQ AppSecret 只供 AstrBot 官方适配器使用，插件签名使用独立的 `GTNH_AUTH_SECRET`。

插件用共享随机密钥签发 HS256 JWT：固定 issuer `astrbot-gtnh`、audience `gtnh-mcp`，`iat`/`exp` 最大间隔 60 秒；身份字段为 `platform`、`group`、`sub`。MCP 和恢复服务分别验证，不依赖会话缓存的旧角色。

`ALLOWED_GROUPS` 为 `平台:群ID` JSON 数组；`ADMIN_USERS` 为 `平台:用户ID` JSON 数组。两项以服务端配置为准；管理员也必须来自允许的群。更改 ACL 或密钥后重新创建两个服务，并同步更新 AstrBot 环境变量。

普通工具凭据的 `purpose=tool`。只有插件的 `/gtnh_confirm <任务编号>` 指令处理器产生 `purpose=confirm` 和绑定编号的 `confirmation` 字段；确认凭据不能调用其他工具。确认必须匹配原申请人的平台、群和用户，十分钟内有效，重复确认返回同一任务。

`confirm_restore` 作为 MCP 接口存在，但不注册为 AstrBot LLM 工具，且 MCP 强制校验专用凭据。请勿同时在 AstrBot 原生 MCP 配置中再次添加本服务。仅靠系统提示词限制恢复不构成权限控制。

## 部署与日志

默认只监听 localhost。共享密钥至少 32 字符，应随机生成；不应让不可信 AstrBot 插件或工具读取进程环境，也不应向该机器人开放通用 shell、文件读取或命令派发能力。持有签名密钥的进程属于可信边界。

插件使用直接 HTTP 连接，不继承宿主环境的 HTTP 代理，也不自动跟随重定向。MCP URL 应直接填写完整 `/mcp` 地址。

操作日志记录真实身份、工具、任务和状态，不记录密码、JWT、公告内容或命令参数。异常只回传经过筛选的错误，不把传输异常中的请求头发送到群里。`/health` 仅报告进程健康，不代表 GTNH 已启动。

短期 JWT 在有效期内可重用，用于 MCP 握手和调用；它不是一次性票据。恢复确认的幂等性由持久化任务保证。日志、状态卷和旧存档不自动清理，管理员需要监控磁盘占用。
