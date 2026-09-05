# GTNH MCP

让 AstrBot 群聊通过经过身份校验的 MCP 工具执行 GTNH 日常运维，并由管理员确认后恢复备份。

支持在线玩家、公告、保存世界、白名单管理、备份查询、恢复申请与进度查询。恢复处理 `backup/*.tar.gz` 中的 `Worlds` 和 `visualprospecting`，保留旧存档并支持失败回滚。

使用 FastMCP Streamable HTTP，默认地址 `http://127.0.0.1:8000/mcp`。MCP 和 AstrBot 使用 Linux Docker host 网络；恢复服务独立持有 Docker 控制权限，通过私有 Unix socket 接受请求。

- [部署与 AstrBot 接入](docs/deployment.md)
- [架构与模块](docs/architecture.md)
- [身份与工具权限](docs/security.md)
- [恢复流程与人工处理](docs/restore.md)
- [开发约定](AGENTS.md)

本地开发（Windows PowerShell）：

```powershell
uv sync --locked --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

首次部署请先在隔离 GTNH 测试服使用真实备份验收。`save_world` 只执行保存，不创建备份；本项目读取已有备份，不接管备份生成计划。
