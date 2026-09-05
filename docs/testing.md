# 开发测试与 NAS 验收

## 本地验证

本项目使用 Windows 本地 `uv` 开发，不使用 WSL，不要求本机存在 GTNH、AstrBot 或运行中的 Docker Engine。

```powershell
uv sync --locked --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

默认测试包含身份签名与权限、命令参数、真实本地 TCP RCON 的认证/断连/超时、归档结构限制、确认绑定与过期、恢复失败回滚，以及目录切换中断后的恢复。测试文件使用 pytest 临时目录，不访问实际游戏存档。

另保留一个**可选**的独立进程黑箱测试：

```powershell
uv run pytest -m blackbox -q
```

它启动真实 MCP/恢复服务代码，通过真实 HTTP、MCP、RCON 和 Docker SDK 调用模拟游戏端与 Docker API，并检查临时存档变化。Windows 下恢复服务的测试传输使用 localhost HTTP 代替 Linux Unix socket；该替换只在测试启动器中存在，不会打包到镜像或成为生产配置选项。它不能证明真实模组、AstrBot 消息平台、Docker host 网络或 NAS 权限兼容。

按照项目所有者要求，本地不搭建完整 GTNH 环境、不进行严格的部署验收。最终可用性由 NAS 测试服验证。

ZIP 兼容验证（2026-09-06）：默认测试 85 项通过，可选独立进程黑箱流程 1 项通过；Ruff 检查通过。新增覆盖 ZIP CRC 损坏、路径与类型限制、加密和压缩方式限制、目录配置、正常恢复、失败回滚以及旧日志在五种目录切换中断位置的恢复。本地没有 Docker，未执行 Docker 命令或镜像构建，也没有连接真实 GTNH 或 AstrBot 群平台。模拟黑箱仍使用 tar.gz，真实 NAS ZIP 的适配通过本地 ZIP 用例验证。

## NAS 上推荐的首次验证顺序

1. **隔离存档和容器**：先复制游戏服务端建立测试容器，使用不同游戏/RCON 端口，确保 `.env` 中容器名和根目录都指向测试副本。
2. **确认目录布局**：根目录实际存在配置的世界目录（默认 `World`）、`visualprospecting`，不是分别挂载的目录；选一个真实 `.zip` 或 `.tar.gz`，确认其中只有这两个根目录，核实原目录 UID/GID。启动脚本使用单次 `exec java`，Docker 负责自动重启。
3. **启动服务**：执行 Compose 配置检查、镜像构建与启动；检查两个服务健康状态、Unix socket 组权限、MCP localhost 可达。
4. **普通操作**：在允许的群测试在线玩家、公告、保存和备份查询；核对服务端原始响应。确认普通群友无法查询/修改白名单及恢复。
5. **管理员恢复**：申请指定备份，核对备份名和任务编号；确认未发送指令前游戏没有停服。由本人发送 `/gtnh_confirm <编号>`，查询任务到 succeeded。
6. **核对结果**：两个目录内容来自选定备份，原目录保留于 `.gtnh-restore/<编号>/previous`；玩家可进入测试服，RCON 可用，容器原重启策略已恢复。
7. **确认幂等性**：再次发送同一确认编号应只返回原任务，不再次停服。其他管理员或其他群不能代替原申请人确认。

确认正常流程后，再决定是否在测试服演练启动失败回滚和人工处理；不要在生产服通过人为破坏存档验证失败分支。出现 manual_intervention 时参照 [恢复文档](restore.md)，先检查现场，再离线处理状态。

## 验证范围

当前环境可以验证 Python 逻辑、参数协议和模拟网络通信；无法验证真实 GTNH 版本的具体命令输出、整合包备份格式差异、Java 启动耗时、AstrBot 的实际平台事件、Linux 文件所有者以及 NAS 容器挂载。镜像构建需要可用的 Docker Engine，不能以 `docker compose config` 通过替代镜像构建通过。
