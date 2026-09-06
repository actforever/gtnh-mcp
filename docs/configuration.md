# 配置参考

## 配置如何生效

复制 `.env.example` 为 `.env`。Compose 用它替换挂载、GID 等 `${变量}`，两个服务还通过 `env_file: .env` 取得程序环境变量。Python 本身不自动加载 dotenv；直接使用 uv 启动时需在进程环境设置变量。修改后执行 `docker compose up -d --force-recreate`，普通 restart 不会更新环境。主 Compose 的 `pull_policy: build` 会在 `up` 时从当前源码重建共用镜像，避免继续运行本地同名旧镜像；不必再单独执行 `docker compose build`。

密码含空格、#、$ 时使用 dotenv 单引号，例如 `RCON_PASSWORD='p@ss word#2026$'`。不要提交真实 .env。QQ ACL 仅配置在 AstrBot 插件或 AstrBot 原有环境中，不属于后端 .env。

## .env 全部变量

| 变量 | 默认/示例 | 作用 |
| --- | --- | --- |
| `AUTH_SECRET` | 必填，至少 32 字符 | MCP 与恢复服务共同校验的固定 Bearer 密钥；建议 `openssl rand -hex 32`。不是 RCON 密码或 QQ AppSecret。持有者拥有全部 MCP 工具权限。 |
| `MCP_HOST` | `127.0.0.1` | MCP 监听 IP；默认仅 NAS 本机可访问。同机 host 网络 AstrBot 可用，远程 Inspector 建议 SSH 隧道。设成 `0.0.0.0` 会监听所有 IPv4 接口，需自行限制入口。 |
| `MCP_PORT` | `8000` | MCP HTTP 端口，1–65535；工具路径 /mcp，健康路径 /health。修改后同步插件 URL、Inspector URL 和隧道端口。 |
| `RCON_HOST` | `127.0.0.1` | MCP 和恢复服务访问 GTNH RCON 的主机；host 网络使用 NAS 回环地址。 |
| `RCON_PORT` | `25575` | RCON 实际可达 TCP 端口，1–65535；匹配 server.properties 或宿主机发布端口。 |
| `RCON_PASSWORD` | 必填 | 必须与 GTNH 的 rcon.password 一致，不能与访问密钥混用。 |
| `RCON_TIMEOUT` | `10` 秒 | RCON 建连和读取超时，范围 1–120；写入超时后结果可能未知，不自动重发。 |
| `GTNH_CONTAINER_NAME` | `gtnh` | 唯一允许管理的已有 GTNH 容器名，使用 docker ps 核对。Compose 映射为程序的 CONTAINER_NAME。 |
| `GTNH_SERVER_ROOT` | `/volume2/sharev9/minecraft/gtnh` | NAS 实际游戏根目录，直接包含 World、visualprospecting、backups；部署前确保存在。 |
| `DOCKER_GID` | 必填 | Docker socket 的数字组 ID；在 NAS 执行 `stat -c '%g' /var/run/docker.sock`，填结果。restore 用 group_add 加入该组，不需容器内存在同名组。 |
| `SERVER_ROOT` | `/gtnh` | restore 容器内游戏根目录，也是 GTNH_SERVER_ROOT 的挂载目标；不是项目 WORKDIR（仍为 /app）。 |
| `BACKUP_DIR` | `/gtnh/backups` | restore 容器内已有 ZIP/tar.gz 目录。必须位于映射的 SERVER_ROOT 内；修改根目录时同步调整。没有独立备份挂载及 GTNH_BACKUP_DIR 配置。 |
| `WORLD_DIRECTORY` | `World` | 世界单层目录名，必须与磁盘、归档和 level-name 一致；旧服可显式设 Worlds，不自动改名。 |
| `RUNTIME_DIR` | `/run/gtnh` | 两服务共享命名卷目标，存放 restore.sock、操作锁、维护标记；两者必须一致。 |
| `STATE_DIR` | `/state` | restore-state 命名卷目标，持久化任务日志及恢复进度。 |
| `STOP_TIMEOUT` | `180` 秒 | RCON stop 后等待容器退出的期限。到期不强杀、不切换目录，进入人工检查。 |
| `STARTUP_TIMEOUT` | `900` 秒 | 启动容器后等待 RCON list 成功的期限；超时触发失败回滚。大型整合包按实际耗时调整。 |
| `MAX_ARCHIVE_BYTES` | `107374182400`（100 GiB） | 归档解压后普通文件总大小上限；也限制撤销来源 previous 的文件总量。不是压缩包大小。 |
| `MAX_ARCHIVE_MEMBERS` | `1000000` | 单个归档或撤销来源的文件及目录数量上限，不是备份数量。 |
| `FREE_SPACE_RESERVE` | `1073741824`（1 GiB） | 满足暂存需求后必须剩余的空间；旧存档长期保留，应监控磁盘。 |

世界名只接受 1–64 位英文字母、数字、下划线、连字符或点，首位不能为点或连字符，末位不能为点，不接受保留名（如 backups、visualprospecting）。不接收绝对路径。新任务保存两个目录名，旧日志无目录字段时按 Worlds 兼容；修改配置前结束当前任务。

根目录、两个世界目录和 .gtnh-restore 必须在同一文件系统，世界目录不能是独立 bind mount。一个根目录挂载自然包含 backups，因此备份不再具有独立只读挂载保护；程序仅读取归档。

## AstrBot 插件配置

在插件配置页填写以下四项，保存后重载插件。首次先填写密钥，群和管理员设为 `[]`，加载后用目标群 `/gtnh_identity` 获取真实身份，再填写列表并重载。

| 页面字段 | 用途 | 留空时读取的 AstrBot 环境变量 |
| --- | --- | --- |
| `mcp_url` | 完整 Streamable HTTP 地址，默认 http://127.0.0.1:8000/mcp | 无 |
| `auth_secret` | 与后端 AUTH_SECRET 完全一致的固定密钥 | `GTNH_AUTH_SECRET` |
| `allowed_groups` | JSON 文本，例如 `["qq_official:GROUP_OPENID"]` | `ALLOWED_GROUPS` |
| `admin_users` | JSON 文本，例如 `["qq_official:USER_OPENID"]` | `ADMIN_USERS` |

非空页面值优先；只含空白视为留空。显式 `[]` 覆盖环境变量，分别表示禁用所有群、无人拥有管理员权限。未设置 ACL 时也按空数组处理。非法 JSON、非字符串数组、重复项、缺少平台前缀及短密钥会使插件初始化报错；不会悄悄退回环境配置。密钥必须先有效配置，插件才能加载并响应身份诊断。

ID 保留大小写，使用实际 `platform:group`、`platform:user`，不要用普通 QQ 号代替开放平台标识。管理员仍须来自允许群，私聊拒绝运维调用。管理员权限不继承 QQ 群主或 AstrBot 管理员身份。

环境变量回退仅用于已有 AstrBot 的原启动环境，例如：

```dotenv
GTNH_AUTH_SECRET='与后端AUTH_SECRET一致'
ALLOWED_GROUPS='["qq_official:实际群标识"]'
ADMIN_USERS='["qq_official:实际用户标识"]'
```

另一个 Compose 项目不会自动读本项目 .env。页面配置无需重建 AstrBot 容器；变更容器环境变量需在其原项目重建。密钥保存在插件配置或环境中，保护 AstrBot 数据目录，不发送到群聊。不要重复添加 AstrBot 原生 MCP 连接，否则绕过插件 ACL 和人工确认入口。

## 持久化及生命周期

runtime 和 restore-state 是命名卷；游戏 previous 副本在游戏根目录 .gtnh-restore 下。不要执行 `docker compose down -v`。恢复执行中不应重建服务。

restore 的健康检查 interval 和 start_period 均为 10s，timeout 5s、retries 3；start_period 是启动失败宽限期，不是固定等待。服务先恢复旧任务才就绪，耗时较长时仍需等待实际恢复。mcp 依赖 restore 健康启动。

`stop_grace_period: 35m` 保留给正在执行的恢复线程优雅退出，和健康检查无关。restore 当前 UID 0、主组 GID 10001，额外加入 DOCKER_GID；root 下缺少该组不一定是权限错误原因，仍需核对 NAS socket 权限、挂载及 Docker 日志。
