# 开发测试与 NAS 验收

## Windows 本地验证

使用本地 uv，不需要 WSL、真实 GTNH、AstrBot 或 Docker Engine：

```powershell
uv sync --locked --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pytest -m blackbox -q
```

默认测试验证固定密钥及调用者编码、插件 ACL 和配置优先级、任务结果过滤、RCON 参数/超时/认证、ZIP CRC 与 tar.gz 结构限制、恢复确认归属和过期、失败回滚、撤销、旧日志与目录切换中断恢复。全部游戏文件位于 pytest 临时目录。

blackbox 默认排除，可独立运行。它启动真实 MCP 与恢复代码的独立进程，经 HTTP/MCP/RCON 和 Docker SDK 调用模拟游戏及 Docker API，检查临时存档内容。Windows 测试启动器把 helper 传输替换为回环 TCP；生产仍用 Unix socket。测试未使用真实 Docker Engine、GTNH 或 QQ 平台。

## 本次验证记录（2026-09-06）

- 固定密钥与插件 ACL 修改后，默认测试 118 项通过。
- 原有恢复、撤销两项模拟黑箱通过：覆盖插件权限、原申请人确认、恢复成功、文件变化拒绝、启动失败回滚、未确认退出时保留现场，以及重启读取日志。
- 增加直接 Inspector 等价客户端用例：只带 Bearer 密钥，缺省 actor 为 api-client，验证申请及确认恢复；另检查 helper 独立拒绝缺失/错误密钥和非法 actor。该用例单独运行 1 项通过；合计 3 项模拟黑箱通过。
- Ruff 静态和格式检查通过；三份 Compose YAML、健康检查 Python 语法、插件配置 JSON 和 .env.example 的 20 个变量说明已静态核对。测试使用旧 MCP 客户端入口时有弃用提示，不影响运行结果。

这不是实际 Inspector 浏览器界面验收，也不能证明 NAS 的 Unix socket、Docker 组权限、挂载或镜像构建正确。本地不调用 Docker（包括 compose config），不使用 WSL，不访问真实存档；用户将在 NAS 隔离测试服验收。

## NAS 验收顺序

1. 复制服务端建立隔离测试容器，使用不同游戏/RCON 端口，容器名和根目录都指向测试副本。
2. 确认 World、visualprospecting、backups 布局，实际 ZIP 只有配置的两个世界根目录，核对 UID/GID。GTNH 使用单次 exec java 启动，由 Docker 控制重启。
3. 按 [部署教程](deployment.md) 填写 DOCKER_GID，检查 Compose、构建并启动。核对两服务健康、共享 socket 权限及回环地址。
4. 使用 Inspector 固定 Bearer 密钥列出工具和备份，缺密钥应被拒绝；/health 无需密钥。
5. 配置插件真实群/用户标识，群友验证玩家、公告、保存和备份；普通用户不能调用管理员操作或读取他人任务。
6. 管理员申请恢复，确认未停服；原申请人在原群发送 /gtnh_confirm，等待 succeeded。核对两个目录内容、previous 旧存档、RCON、玩家连接与原重启策略。
7. 重复确认不应再次停服；其他人或其他群不能代确认。
8. 申请撤销成功回档，确认后恢复原进度，并保留撤销前世界；必要时再撤销此次撤销。

失败演练仅在测试服执行。manual_intervention 时按 [恢复文档](restore.md) 检查现场，不直接删除维护标记。

当前验证无法替代真实模组输出、Java 启动耗时、AstrBot 事件、Linux 所有权及 NAS 文件系统验收。不要以 YAML 解析通过代替 Compose 插值或镜像构建通过。
