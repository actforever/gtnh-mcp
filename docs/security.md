# 身份与工具权限

## 固定密钥边界

MCP /mcp 要求 `Authorization: Bearer <AUTH_SECRET>`；/health 公开。MCP 与恢复服务各自以常量时间比较固定密钥，无 JWT、签发流程或一分钟到期。密钥至少 32 字符，推荐随机生成。持有密钥的客户端可以直接调用全部工具，包括 confirm_restore 和全部任务查询；这是管理员级 API 凭据。

QQ 群/管理员 ACL 仅由 AstrBot 插件检查。不要把密钥提供给普通群友或模型，不要同时从 AstrBot 原生 MCP 页面添加本服务。后者会跳过插件 ACL，也直接暴露确认工具。密钥泄漏时同时更新后端和插件，重建后端并重载插件；未更换前旧密钥一直有效。

## 插件权限

| 工具 | 允许群内成员 | 配置中的管理员 |
| --- | --- | --- |
| list_players、announce、save_world、list_backups | 是 | 是 |
| restore_status | 仅本人任务 | 全部任务 |
| list_whitelist、add_whitelist、remove_whitelist | 否 | 是 |
| request_restore、request_undo_restore | 否 | 是 |
| confirm_restore | 否 | 仅人工 /gtnh_confirm 命令，且匹配原申请标识 |

插件读取实际事件的 platform、group、sender；不读取消息里的身份声明，不接受模型传入身份或角色。管理员也必须处于允许群，私聊拒绝调用。任务查询在返回模型前按完整 actor 过滤；指定他人任务编号也不能获取其内容，异常响应不回传原始敏感正文。插件配置页优先、环境变量回退，详见 [配置说明](configuration.md)。

## 任务归属与确认

插件通过 `X-GTNH-Actor` 传递 UTF-8 `platform:group:user` 的 Base64URL 编码。后端只解析为不透明字符串并转发给恢复服务，不解释 QQ 权限。缺少此头使用 `api-client`，非法编码、控制字符或超长标识被拒绝。旧日志 actor 保持原样兼容。

此头不是签名，拥有访问密钥者可以指定任意合法 actor；它用于可信客户端间的任务归属和误操作检查，不隔离不同密钥持有人。直接 Inspector 客户端不设置此头即可申请和确认自己的 api-client 任务。

恢复申请绑定 actor、来源内容指纹和十分钟期限。确认必须匹配原 actor（插件中含原群及用户），持久化后才能产生副作用。成功确认的重复请求返回同一任务，不重复停服。插件不将 confirm_restore 注册成 LLM 工具；固定密钥后端不再校验 purpose 字段。

## 文件、容器与日志

没有任意 RCON、shell、文件路径或容器选择工具。公告是最多 300 字符单行文本；玩家名限 1–16 位英文字母、数字、下划线。写操作不自动重试。

MCP 非 root 且不挂载游戏目录或 Docker socket；restore 通过私有 Unix socket 接受固定动作。Docker socket 本身具有高权限，根目录挂载也可写。运行目录和状态卷须保护，不向其他服务开放。

默认只监听 NAS 回环地址；远程调试走 SSH 隧道。HTTP 不加密，不将固定密钥直接通过公网明文传输。插件不继承 HTTP 代理、不跟随重定向。日志只记录操作、调用者标识、任务和状态，不记录访问密钥、密码或命令参数；传输异常不直接发送给群友。

共享密钥持有者和可读 AstrBot 配置的进程属于可信边界。/health 仅表示进程可用，不表示 GTNH、RCON 或备份已经验收。
