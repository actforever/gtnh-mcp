# Docker 与 AstrBot 部署

## 前提

- Linux NAS 的 Docker Compose；已有运行中的 GTNH 容器。
- GTNH 已启用 RCON，NAS 的 `127.0.0.1:<端口>` 可达。若 GTNH 使用 bridge 网络，将 RCON 端口绑定到宿主机回环地址，如 `127.0.0.1:25575:25575`。
- `GTNH_SERVER_ROOT` 是包含 `Worlds`、`visualprospecting`、`backup` 的宿主机目录。不要分别挂载两个世界目录。
- AstrBot 4.x，插件声明最低 4.16；使用内置 Agent 及支持工具调用的模型。部署时仍需在你的具体版本和群平台验收消息身份。

## 服务配置

复制 `.env.example` 为 `.env`，填写真实路径、GTNH 容器名和 RCON 密码。用 `openssl rand -hex 32` 等方式生成 `AUTH_SECRET`。不要提交 `.env`。

```sh
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
```

Compose 中 MCP、恢复服务均为 host 网络，不使用 ports 映射。MCP 默认监听 `127.0.0.1:8000`；恢复服务只监听共享卷中的 Unix socket。GTNH 容器不由该 Compose 创建，配置 `GTNH_CONTAINER_NAME` 指向现有容器。

| 环境变量 | 默认值 / 说明 |
| --- | --- |
| `RCON_HOST` / `RCON_PORT` | `127.0.0.1` / `25575` |
| `RCON_PASSWORD` | 必填 |
| `RCON_TIMEOUT` | 10 秒，范围 1–120 |
| `AUTH_SECRET` | 必填，随机密钥，至少 32 字符 |
| `ALLOWED_GROUPS` | 必填 JSON 数组，例如 `["aiocqhttp:123456789"]` |
| `ADMIN_USERS` | 默认空数组，例如 `["aiocqhttp:987654321"]` |
| `MCP_PORT` | 8000；插件 URL 必须同步修改 |
| `GTNH_CONTAINER_NAME` | 必填，现有 Docker 容器名 |
| `GTNH_SERVER_ROOT` / `GTNH_BACKUP_DIR` | 必填，宿主机绝对路径 |
| `STOP_TIMEOUT` / `STARTUP_TIMEOUT` | 180 / 900 秒 |
| `MAX_ARCHIVE_BYTES` | 107374182400，最大展开大小（100 GiB） |
| `MAX_ARCHIVE_MEMBERS` | 1000000，最大归档条目数 |
| `FREE_SPACE_RESERVE` | 1073741824，展开所需空间之外至少保留 1 GiB |

程序还读取 `MCP_HOST`、`RUNTIME_DIR`、`SERVER_ROOT`、`BACKUP_DIR`、`STATE_DIR`、`CONTAINER_NAME`。Compose 固定其容器内布局；`GTNH_*` 的对应项由 Compose 转换为程序配置。不要直接在 `.env` 中覆盖内部路径后忘记修改挂载。

镜像只包含服务端包；MCP 用 UID/GID 10001 运行，恢复服务用 UID 0 / GID 10001 运行。共享 runtime 卷由恢复服务初始化组权限。恢复服务需要 Docker socket 和存档写权限；宿主机启用 userns-remap、rootless Docker 或特殊 NAS ACL 时应先验证权限映射。

## AstrBot 插件

把本仓库整个 `astrbot_plugin` 目录复制到 AstrBot 的 `data/plugins/astrbot_plugin_gtnh`。目录中的 `main.py`、`bridge.py`、`metadata.yaml`、`_conf_schema.json`、`requirements.txt` 必须一起保留。通过 AstrBot 插件管理安装依赖并重新加载；如需手工安装，在 AstrBot 容器内运行：

```sh
python -m pip install -r /AstrBot/data/plugins/astrbot_plugin_gtnh/requirements.txt
```

`/AstrBot/data` 是常见镜像路径；自定义镜像应使用实际 data 路径。

在**已有 AstrBot Compose 服务**中合并以下配置，保留原镜像、数据卷及其他环境变量：

```yaml
services:
  astrbot:
    network_mode: host
    environment:
      GTNH_AUTH_SECRET: ${AUTH_SECRET}
```

这里的 `${AUTH_SECRET}` 必须与 MCP/恢复服务一致；如果 AstrBot 位于另一个 Compose 项目，在那个项目的 `.env` 中也设置同值。重新创建 AstrBot 容器使环境变量生效。

插件设置 `mcp_url=http://127.0.0.1:8000/mcp`。**不要再通过 AstrBot 原生 MCP 配置添加本服务**，所有调用经配套插件获取真实身份。启用插件的九个 `gtnh_*` 对话工具。

在目标群发送 `/gtnh_identity` 查看实际平台名、群 ID、用户 ID。以返回值填写服务端 ACL，不能假设平台名一定是 `aiocqhttp`。机器人提示词可以说明「恢复需要本人发确认指令」，但权限由代码强制校验。

示例交互：

1. 群友：「现在有谁在线？」「发公告：五分钟后维护。」「有哪些备份？」
2. 管理员：「申请恢复列表里的某个备份。」机器人返回任务编号和待确认信息。
3. 管理员本人发送 `/gtnh_confirm <任务编号>`，随后询问该任务进度。
4. 普通群友尝试修改白名单或恢复，必须收到拒绝结果。

## 验收与运行限制

`http://127.0.0.1:8000/health` 验证 MCP 进程；没有签名凭据不能调用 MCP 工具。健康检查不验证 RCON 是否就绪。

开发测试使用 Windows 本地 `uv`，不依赖 WSL。单元测试使用临时文件，RCON 测试使用真实本地 TCP。最终独立进程黑箱测试的运行方式和覆盖范围见 [测试说明](testing.md)。

上线前，在隔离 GTNH 测试容器中放入真实备份，验证两个目录内容、所有者、RCON 命令支持、启动耗时及 AstrBot 实际群消息身份。当前未指定 GTNH 和 AstrBot 精确版本，不能以模拟服务测试替代这个验收。

不要让外部自动化在恢复期间重建或启动 GTNH；不要删除 state/runtime 卷。长期运行需要管理员监控旧存档、任务和日志的磁盘使用。人工恢复步骤见 [恢复文档](restore.md)。

接口依据：[FastMCP HTTP](https://gofastmcp.com/deployment/running-server)、[AstrBot 工具接口](https://docs.astrbot.app/dev/star/guides/ai.html)、[AstrBot 插件配置](https://docs.astrbot.app/dev/star/guides/plugin-config.html)。
