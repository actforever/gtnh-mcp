# 配置参考

## `.env` 怎样进入程序

`.env.example` 只是模板，部署前将它复制为 `.env`。Docker Compose 读取 `.env`，替换 Compose 文件中的 `${变量名}`，再通过服务的 `environment` 把值传入容器。Python 程序本身不会自动寻找或加载 `.env`。

```text
.env ──Compose 变量替换──> compose.yaml ──容器 environment──> Python Settings
```

修改 `.env` 后执行 `docker compose up -d --force-recreate` 才会更新容器环境变量；`docker compose restart` 不会注入新值。

`ALLOWED_GROUPS` 和 `ADMIN_USERS` 必须是合法 JSON 数组，建议用单引号包住整个 JSON。密码含空格、`#`、`$` 等符号时也建议使用单引号：

```dotenv
RCON_PASSWORD='p@ss word#2026$'
ALLOWED_GROUPS='["aiocqhttp:123456789","aiocqhttp:987654321"]'
ADMIN_USERS='["aiocqhttp:1122334455"]'
```

不要在等号两边加空格，也不要提交真实 `.env`。

## 身份与网络配置

### `AUTH_SECRET`

AstrBot 配套插件用它签发短期身份凭据，MCP 和恢复服务用同一值验签。必填，至少 32 字符，建议执行 `openssl rand -hex 32` 生成。AstrBot 容器内变量名是 `GTNH_AUTH_SECRET`，`compose.chat.yaml` 会从同一个 `.env` 映射；两者必须完全一致。

它不是 RCON 密码、AstrBot 管理密码、NapCat WebUI 密码、OneBot token 或模型 API Key。泄露后应生成新值，并重新创建 MCP、恢复服务和 AstrBot 容器。

### `RCON_HOST`

MCP 和恢复服务连接 Minecraft RCON 的主机地址。项目服务使用 host 网络，GTNH 也使用 host 网络时保持 `127.0.0.1`。若 GTNH 使用 bridge 网络，应把 RCON 端口仅发布到 NAS 回环地址，然后仍使用 `127.0.0.1`。

### `RCON_PORT`

NAS 实际监听的 RCON TCP 端口，默认 `25575`，有效范围 1–65535。它必须对应 `server.properties` 的 `rcon.port`，或 GTNH 容器发布到宿主机的端口。

### `RCON_PASSWORD`

连接 GTNH RCON 的密码，必须与 `server.properties` 的 `rcon.password` 完全一致。它只用于服务到游戏服的连接，不应与 `AUTH_SECRET` 共用。

### `RCON_TIMEOUT`

RCON 建连和单次响应读取的超时秒数，默认 10，范围 1–120。写命令超时后的结果可能未知，程序不会自动重发；应先查询服务器状态。

### `MCP_PORT`

FastMCP Streamable HTTP 的监听端口，默认 8000。MCP 固定监听 `127.0.0.1`，地址是 `http://127.0.0.1:8000/mcp`。修改后必须同步修改 AstrBot 插件的 `mcp_url`。

### `ALLOWED_GROUPS`

允许调用工具的群列表，必填 JSON 数组。元素格式为 `平台名:群ID`；QQ + NapCat 通常为 `aiocqhttp:QQ群号`。管理员也必须从允许的群发起操作。插件安装后在目标群发送 `/gtnh_identity`，以实际返回的 `platform` 和 `group` 核对。私聊没有群 ID，会被拒绝。

### `ADMIN_USERS`

拥有白名单管理和备份恢复权限的用户列表，JSON 数组。元素格式为 `平台名:用户ID`；QQ + NapCat 通常为 `aiocqhttp:QQ号`。空数组 `[]` 表示无人有管理权限。这里的权限独立于 QQ 群主、QQ 群管理员和 AstrBot 管理员，必须显式配置。

## GTNH 容器与目录配置

### `GTNH_CONTAINER_NAME`

恢复服务唯一允许停启的 Docker 容器名称。使用 `docker ps --format '{{.Names}}'` 查询；Compose service 名和最终容器名不一定相同。配置错误时恢复申请会失败，程序不会自动选择其他容器。

### `GTNH_SERVER_ROOT`

NAS 上的游戏服务端根目录，必须直接包含大小写准确的 `Worlds` 和 `visualprospecting`。恢复服务把它映射为 `/server`，并在其中创建 `.gtnh-restore/<任务ID>` 保存暂存和新旧存档。

这不是本项目代码目录。`Worlds`、`visualprospecting` 和 `.gtnh-restore` 必须在同一文件系统中；两个世界目录不能分别作为独立 bind mount，否则无法保证原子目录切换。

### `GTNH_BACKUP_DIR`

NAS 上存放现有 `*.tar.gz` 的目录，通常是 `${GTNH_SERVER_ROOT}/backup`。它以只读方式映射为 `/backups`。每个归档解压后必须只包含 `Worlds` 和 `visualprospecting` 两个顶层目录。

### `STOP_TIMEOUT`

恢复服务发送 RCON `stop` 后等待 Docker 确认 GTNH 容器退出的最长秒数，默认 180。到期后不会强杀进程或替换存档，而会进入人工检查状态。

### `STARTUP_TIMEOUT`

启动 GTNH 容器后等待 RCON `list` 成功的最长秒数，默认 900。大型整合包启动较慢时应提高；到期会触发回滚。

### `MAX_ARCHIVE_BYTES`

单个备份内所有普通文件解压后大小之和的上限，单位字节，默认 `107374182400`（100 GiB）。它不是 `.tar.gz` 压缩包大小，也不限制整个备份目录。

### `MAX_ARCHIVE_MEMBERS`

单个归档允许的文件和目录条目数上限，默认 1,000,000。它用于限制异常归档，不表示最多保存多少份备份。

### `FREE_SPACE_RESERVE`

磁盘满足本次备份暂存空间后还必须保留的可用空间，单位字节，默认 `1073741824`（1 GiB）。恢复会长期保留旧存档，应持续监控磁盘空间。

## AstrBot、NapCat 与内部配置

### `NAPCAT_UID` / `NAPCAT_GID`

NapCat 写入持久化目录时使用的宿主机用户和组 ID。SSH 登录 NAS 后运行 `id -u` 和 `id -g` 获取；常见值为 1000/1000，但应以实际输出为准。它们只供 `compose.chat.yaml` 使用。

`compose.chat.yaml` 使用独立项目名 `gtnh-chat`，并固定使用 AstrBot WebUI 端口 6185、NapCat WebUI 端口 6099、OneBot 反向 WebSocket 端口 6199 和时区 `Asia/Shanghai`。两者使用 host 网络，端口直接由 NAS 占用，无需 `ports` 映射。

`compose.yaml` 固定以下容器内值：`MCP_HOST=127.0.0.1`、`RUNTIME_DIR=/run/gtnh`、`SERVER_ROOT=/server`、`BACKUP_DIR=/backups`、`STATE_DIR=/state`、`CONTAINER_NAME=${GTNH_CONTAINER_NAME}`。宿主机路径由 volume 映射到内部路径，不要把宿主机路径直接写成容器路径。

- `runtime` 命名卷保存 Unix socket、跨进程锁和维护标记，由 MCP 与恢复服务共享。
- `restore-state` 命名卷保存恢复任务日志，用于服务重启后的恢复判断。
- `runtime/astrbot-data` 保存 AstrBot 配置和插件。
- `runtime/napcat-config` 与 `runtime/napcat-qq` 保存 NapCat 配置和 QQ 登录状态。

不要执行 `docker compose down -v`，否则会删除恢复状态卷。恢复进行中也不应停止或重建服务。

## 不同凭据不要混用

| 凭据 | 用途 | 填写位置 |
| --- | --- | --- |
| `AUTH_SECRET` / `GTNH_AUTH_SECRET` | 签发和验证群成员身份 | 项目 `.env`，由两个 Compose 模板注入 |
| RCON 密码 | 服务连接 GTNH | GTNH `server.properties` 与项目 `.env` |
| NapCat WebUI 密码 | 登录 NapCat 管理页面 | NapCat 首次启动日志/WebUI |
| OneBot token | NapCat 与 AstrBot 的反向 WebSocket 认证 | 两边 WebUI 填写同值 |
| 模型 API Key | AstrBot 调用大语言模型 | AstrBot WebUI 的模型提供商设置 |

这些凭据用途不同，不要复用，也不要发到群聊或写入 Git。
