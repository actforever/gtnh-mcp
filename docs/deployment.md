# NAS 完整部署教程：GTNH + MCP + AstrBot 官方 QQ 接入

本文以 NAS 上已有 GTNH 和已接入 QQ 官方机器人的 AstrBot 为主线：在现有 AstrBot 内安装 GTNH 插件，再接入 MCP。已有机器人凭据、模型配置及数据继续沿用，不需要另建 AstrBot 实例。示例路径使用 `/volume1/docker`，请替换成 NAS 的实际路径。命令均在 NAS SSH 终端执行。

```text
QQ 群 <-> QQ 官方机器人 API <-> 现有 AstrBot + GTNH 插件
                                                   |
                                      Streamable HTTP /mcp
                                                   |
                     gtnh-mcp <-> RCON <-> GTNH    |
                         |
                  Unix socket
                         |
                  restore 服务 <-> Docker + 存档目录
```

## 1. 部署前检查

准备以下内容：

- Linux NAS、Docker 和 Docker Compose v2；`docker compose version` 能正常执行。
- 已运行的 GTNH 容器、游戏根目录、RCON 端口和密码。
- 已在目标群可用的 AstrBot QQ 官方机器人接入。
- 支持工具调用的模型服务及 API Key。
- 未占用的 MCP 端口 8000；已有 AstrBot 沿用其管理端口，首次部署示例使用 6185。

查询 GTNH 的真实容器名和挂载：

```sh
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}'
docker inspect <GTNH容器名> --format '{{json .Mounts}}'
```

找到宿主机上直接包含以下目录的游戏根目录：

```text
/volume2/sharev9/minecraft/gtnh/
├── World/
├── visualprospecting/
└── backups/
    └── 2026-09-06-01-19-40.zip
```

`World`（由 `WORLD_DIRECTORY` 配置）和 `visualprospecting` 必须大小写一致，且不能分别挂载到其他文件系统。ZIP 内直接包含这两个目录；也兼容相同结构的 tar.gz。

### 调整 GTNH 启动脚本

已查看的 NAS 启动脚本 `startserver-java9.sh` 使用 `while true`，Java 退出后等待 12 秒重启。恢复服务需要 RCON stop 后整个容器退出；仅关闭 Docker 重启策略无法关闭脚本内部循环。

在安排好的停服窗口，先保存游戏并通过现有管理方式停止 GTNH，再备份原 Compose 和启动脚本。将本项目的 `examples/gtnh/startserver-managed.sh` 复制到 `/volume2/sharev9/minecraft/gtnh/startserver-managed.sh`（保持 LF 换行）。该脚本保留当前 Java 21 启动参数、8 GiB 堆和 `java9args.txt`，改为单次 `exec java`。

在本项目根目录执行复制（NAS 用户需要目标目录写权限）：

```sh
cp examples/gtnh/startserver-managed.sh /volume2/sharev9/minecraft/gtnh/startserver-managed.sh
```

修改 **GTNH 自己的 Compose**：工作目录保持 `/gtnh`，挂载保持服务端根目录，移除原 `command`，将入口和重启策略设置为：

```yaml
entrypoint: ["/bin/sh", "/gtnh/startserver-managed.sh"]
restart: unless-stopped
```

完整示例见 [GTNH Compose](../examples/gtnh/compose.yaml)，使用已部署的 `openjdk:21` 镜像；合并时保留自己其他需要的设置，不要用它覆盖 MCP 的 Compose。在 GTNH 的 Compose 所在目录执行 `docker compose up -d gtnh`，检查启动日志及游戏连接。正常故障后的重启交由 Docker，恢复期间程序会临时关闭此策略并在结束时还原。这里提供操作步骤，本次开发没有修改 NAS 文件或重启真实容器。

## 2. 启用 GTNH RCON

确认 `server.properties` 包含：

```properties
enable-rcon=true
rcon.port=25575
rcon.password=替换为独立的强密码
broadcast-rcon-to-ops=false
```

如果 GTNH 镜像会通过环境变量重写该文件，应在 GTNH 自己的 Compose 或 NAS 容器界面配置等价选项。修改后重启 GTNH。

GTNH 使用 host 网络时，项目可直接访问 `127.0.0.1:25575`。GTNH 使用 bridge 网络时，在它自己的 Compose 中仅向 NAS 回环地址发布 RCON：

```yaml
services:
  gtnh:
    ports:
      - "127.0.0.1:25575:25575"
```

不要将 RCON 发布到 `0.0.0.0` 或公网。重建 GTNH 容器后可运行 `ss -lnt | grep 25575` 检查监听。

## 3. 放置项目并填写 `.env`

```sh
mkdir -p /volume1/docker/gtnh-mcp
cd /volume1/docker/gtnh-mcp
git clone <本仓库地址> .
cp .env.example .env
openssl rand -hex 32
```

没有 Git 时可上传整个项目目录。编辑 `.env`，以下中文占位值都要替换：

```dotenv
AUTH_SECRET='粘贴刚生成的64位十六进制字符串'
RCON_HOST=127.0.0.1
RCON_PORT=25575
RCON_PASSWORD='与server.properties一致的RCON密码'
RCON_TIMEOUT=10
MCP_PORT=8000
MCP_HOST=127.0.0.1
RUNTIME_DIR=/run/gtnh
STATE_DIR=/state
GTNH_CONTAINER_NAME=gtnh
GTNH_SERVER_ROOT=/volume2/sharev9/minecraft/gtnh
DOCKER_GID=替换为实际数字组ID
SERVER_ROOT=/gtnh
BACKUP_DIR=/gtnh/backups
WORLD_DIRECTORY=World
STOP_TIMEOUT=180
STARTUP_TIMEOUT=900
MAX_ARCHIVE_BYTES=107374182400
MAX_ARCHIVE_MEMBERS=1000000
FREE_SPACE_RESERVE=1073741824
```

先在 NAS 执行 `stat -c '%g' /var/run/docker.sock`，将输出填入 DOCKER_GID。用 `test -d /volume2/sharev9/minecraft/gtnh/World` 和 `test -d /volume2/sharev9/minecraft/gtnh/backups` 确认目录存在，避免短挂载语法因拼写错误创建空目录。容器内游戏数据默认位于 /gtnh，项目代码工作目录仍是 /app；备份随根目录挂载，不再单独挂载。群权限不写进后端 .env，第 8–9 步在插件配置。

全部变量的用途、单位及相互关系见 [配置参考](configuration.md)。仅在 NAS 检查 Compose；不要公开粘贴包含密码的渲染结果：

```sh
docker compose config --quiet
```

## 4. 构建 MCP 与恢复服务

```sh
docker compose up -d --force-recreate
docker compose ps
docker compose logs --tail=100 restore mcp
curl --fail http://127.0.0.1:8000/health
```

健康接口应返回 `{"status":"ok"}`。它只证明 MCP 进程可用，不证明 RCON 或备份正确，也不代表 /mcp 无需认证。

restore 健康检查间隔及启动宽限期均为 10s，mcp 继续等待 restore 健康。start_period 是失败宽限期，不强制延迟健康状态；启动时若需恢复旧任务，仍必须等待恢复完成。35m 的 stop_grace_period 是独立的优雅停止时间。

### Inspector 连接

在自己的电脑执行并保持 SSH 隧道运行：

```sh
ssh -N -L 8000:127.0.0.1:8000 pineclone.nas
```

MCP Inspector 选择 **Streamable HTTP**，URL 填 `http://127.0.0.1:8000/mcp`，自定义请求头名称填 `Authorization`，值填 `Bearer <AUTH_SECRET>`，替换为 .env 的实际密钥，不带尖括号。若界面提供 Bearer Token 输入框，只填密钥本身；无需 OAuth 或 JWT 签发。Inspector 代理自身的令牌与本项目密钥是两种配置，不要混用。界面字段随版本变化，参见 [Inspector 服务连接配置](https://github.com/modelcontextprotocol/inspector/blob/main/docs/mcp-server-configuration.md)。

连接后先 List Tools，再调用 list_players、list_backups。默认不填 X-GTNH-Actor，任务归属为 api-client，申请与确认须保持标识一致。Inspector 持有全部 API 权限，确认恢复会真正停服，只在隔离测试服执行。

本地 8000 被占用时，将隧道第一个端口换成 18000，Inspector URL 同步使用 18000。健康可达但 MCP 返回 401/403 时，核对 Bearer 请求头、密钥及后端容器是否重建；连接拒绝则先排查隧道、监听 IP/端口和容器日志。

`mcp` 以非 root 用户提供 HTTP 工具并访问 RCON；`restore` 挂载 Docker socket 与游戏目录，负责受控恢复。恢复服务没有 TCP 端口，只接受共享 Unix socket 请求。GTNH 不是本项目 Compose 的一部分，恢复服务只操作 `.env` 指定的现有容器。

## 5. 复用现有 AstrBot（首次部署可选）

已有 AstrBot 时跳过本节的启动命令，保留它原有的 Compose/NAS 项目和数据挂载。记录实际容器名、Compose service 名以及 `/AstrBot/data` 对应的宿主机目录，第 8 步会用到。可通过 `docker inspect <现有AstrBot容器名> --format '{{json .Mounts}}'` 查看挂载，不要把旧数据改成空的新目录。

本项目 MCP 仅监听 NAS 的 `127.0.0.1`。同一 NAS 上的 AstrBot 以 host 网络或直接宿主机进程运行时才能用此地址；bridge 容器内的 `127.0.0.1` 指向该容器自己。若现有 AstrBot 使用 bridge 网络，需在其原部署配置中安排切换到 `network_mode: host`，移除该服务的 `ports` 映射，核对实际监听端口是否冲突，保留原数据卷、机器人及模型设置后重建。不要仅把 MCP 地址改成 NAS IP；当前 MCP 不监听该地址。其他主机部署需要另行配置受控网络入口，不在本教程默认方案内。

**只有尚未部署 AstrBot 时**，才在本项目根目录使用 AstrBot-only 模板：

```sh
docker compose -f compose.chat.yaml config --quiet
docker compose -f compose.chat.yaml pull
docker compose -f compose.chat.yaml up -d
docker compose -f compose.chat.yaml ps
docker compose -f compose.chat.yaml logs --tail=100 astrbot
```

持久化数据写入：

```text
runtime/astrbot-data/
```

模板使用独立 Compose 项目名 `gtnh-chat` 和 host 网络。按 AstrBot 启动日志完成首次登录，浏览器访问 `http://<NAS局域网IP>:6185`，设置管理密码；管理端口只允许可信局域网或 VPN 访问。

## 6. 确认 AstrBot 内置 QQ 官方接入

已有官方 QQ 接入时，直接在目标群 @ 机器人发送 `/help`，确认 AstrBot 能收到消息并回复；保留原有平台和接入方式。

首次配置时，在 AstrBot WebUI 的机器人/消息平台中创建“QQ 官方机器人（WebSocket）”，填写 QQ 开放平台的 AppID、AppSecret 并启用；支持一键创建的版本也可以按其扫码流程操作。根据开放平台当前要求完成群使用权限和测试/发布配置，再在目标群验证。详细步骤以 [AstrBot 官方 QQ 接入文档](https://docs.astrbot.app/platform/qqofficial/websockets.html) 为准。已有 Webhook 接入的用户继续沿用现有回调配置。

QQ AppID/AppSecret 只配置在 AstrBot 官方适配器中，与本项目的 `AUTH_SECRET` 无关，也不写入 MCP `.env`。

## 7. 配置 AstrBot 模型

在 AstrBot WebUI 添加模型提供商，填写提供商类型、API 地址、API Key 和模型名。选择支持 function/tool calling 的模型，并让当前会话使用 AstrBot 内置 Agent。模型 API Key 只填在 AstrBot，不写进本项目 `.env`。

普通对话正常但从不调用工具时，检查模型是否支持工具调用、内置 Agent 和工具是否启用。

## 8. 安装配套插件

### 已有 AstrBot

推荐在插件页面填写 auth_secret。如需环境回退，可在现有 AstrBot 的原 Compose/NAS 配置中加入 GTNH_AUTH_SECRET，值与后端 AUTH_SECRET 完全一致。以下是可选示例，使用页面配置时无需添加：

```yaml
services:
  astrbot: # 替换为原有 service 名
    environment:
      GTNH_AUTH_SECRET: ${GTNH_AUTH_SECRET:?Set GTNH_AUTH_SECRET}
```

修改本项目 `.env` 不会自动影响另一个 Compose 项目。以下是现有实例的具体安装流程；替换绝对路径及容器/service 名，挂载路径按第 5 步检查结果填写：

```sh
# 从本项目目录复制到现有 AstrBot 的持久化插件目录
mkdir -p /实际AstrBot数据目录/plugins/astrbot_plugin_gtnh
cp -R astrbot_plugin/. /实际AstrBot数据目录/plugins/astrbot_plugin_gtnh/
# 仅变更环境变量时重建；页面配置方式跳过这一条
docker compose --env-file /实际AstrBot项目目录/.env -f /实际AstrBot项目目录/compose.yaml up -d --force-recreate <AstrBot服务名>
docker exec <AstrBot容器名> python -m pip install -r /AstrBot/data/plugins/astrbot_plugin_gtnh/requirements.txt
docker restart <AstrBot容器名>
```

由 NAS 容器界面管理的实例在原界面更新环境并重建，再执行上述依赖安装与 restart；直接运行在宿主机的实例将插件放到其实际数据目录，用原 Python 环境安装依赖，在原启动环境设置密钥后重启 AstrBot。

### 使用本项目可选 AstrBot 模板

仅第 5 步新建的实例，在本项目根目录执行：

```sh
mkdir -p runtime/astrbot-data/plugins/astrbot_plugin_gtnh
cp -R astrbot_plugin/. runtime/astrbot-data/plugins/astrbot_plugin_gtnh/
docker compose -f compose.chat.yaml up -d --force-recreate astrbot
docker compose -f compose.chat.yaml exec astrbot \
  python -m pip install -r /AstrBot/data/plugins/astrbot_plugin_gtnh/requirements.txt
docker compose -f compose.chat.yaml restart astrbot
```

`--force-recreate` 会把 `.env` 中的 `AUTH_SECRET` 作为 `GTNH_AUTH_SECRET` 注入 AstrBot；只执行 restart 不会更新环境变量。

顺序必须是先重建容器、再安装依赖、最后普通 restart。pip 安装的依赖位于容器可写层，重建会丢失；以后每次重新创建 AstrBot 容器都应重新执行安装和 restart。插件文件本身位于持久化目录，不会因重建丢失。

进入 AstrBot 插件管理，配置页面填写以下内容并重载。缺少密钥时初始化会报错，先填写再重载；群列表暂设 [] 后仍可使用本地身份诊断。配置文件由 AstrBot 根据 schema 创建，见 [官方插件配置说明](https://github.com/AstrBotDevs/AstrBot/blob/master/docs/en/dev/star/guides/plugin-config.md)：

```text
mcp_url = http://127.0.0.1:8000/mcp
auth_secret = 与后端 AUTH_SECRET 一致（使用环境回退时留空）
allowed_groups = []
admin_users = []
```

改过 MCP_PORT 时同步修改地址。显式 [] 拒绝所有群/管理员；留空分别读取 AstrBot 环境的 ALLOWED_GROUPS、ADMIN_USERS，非空页面值优先。非法 JSON 或短密钥会导致初始化报错。启用十个 gtnh_* 对话工具。不要从 AstrBot 原生 MCP 页面重复添加本服务，否则绕过插件权限并向模型提供确认工具。

## 9. 核对群和管理员身份

由管理员账号在目标 QQ 群发送 `/gtnh_identity`，预期返回：

```text
platform=qq_official group=GROUP_OPENID_EXAMPLE user=USER_ID_EXAMPLE
```

群内若需 @ 触发，发送“@机器人 /gtnh_identity”。在插件页面用实际 `platform:group` 填写 `allowed_groups` JSON 数组，用实际 `platform:user` 填写 `admin_users` JSON 数组，保留大小写及完整字符串。上面的输出只是格式示例，不要照抄。QQ 官方群消息使用开放平台标识（群为 `group_openid`），不能直接填写普通群号、QQ 号；平台前缀也以实际输出为准。修改插件页面后保存并重载插件即可。示例为 `["qq_official:GROUP_OPENID_EXAMPLE"]` 和 `["qq_official:USER_ID_EXAMPLE"]`，使用真实值替换。使用环境回退时，在 AstrBot 原项目更新环境并重建；后端不读取 QQ ACL。下列命令仅在更改后端 .env 时需要：

```sh
docker compose up -d --force-recreate
```

管理员权限完全由插件配置决定，不自动继承 QQ 群主、群管理员或 AstrBot 管理员身份。

## 10. 首次功能检查

按以下顺序从群中验证：

1. 群友询问在线玩家。
2. 群友发送测试公告并在游戏内确认。
3. 群友要求保存世界；`save_world` 只执行 `save-all`，不会创建备份。
4. 群友查询备份列表，应看到文件名、ID、大小和修改时间。
5. 普通群友尝试修改白名单或恢复，应被拒绝。
6. 管理员查询和修改白名单，按服务端原始响应判断结果。

## 11. 在隔离测试服验证恢复

不要把生产服作为首次恢复测试。复制 GTNH 服务端目录，创建名称和端口不同的测试容器，再让 `.env` 暂时指向测试容器、测试根目录与备份目录。

1. 管理员查询备份并选择备份 ID。
2. 管理员申请恢复；此时只生成任务，不停服。
3. 核对备份名和任务编号。
4. 原申请人本人在原群发送 `/gtnh_confirm <任务编号>`。
5. 查询状态，直到 `succeeded`、`rolled_back`、`failed` 或 `manual_intervention`。

成功后确认 GTNH 和 RCON 可用、两个在线目录来自备份、旧存档位于 `${GTNH_SERVER_ROOT}/.gtnh-restore/<任务编号>/previous/`，且容器原重启策略已恢复。再次发送同一确认编号不应再次停服。

出现 `manual_intervention` 时不要删除维护标记或继续写操作，按 [恢复文档](restore.md) 检查现场。

如需撤销成功回档，让机器人根据该成功任务编号申请撤销，再由本人确认新任务编号。测试服应验证世界回到原回档前状态，撤销前的世界保存在新任务的 `previous`，详见 [撤销回档](restore.md#撤销已经完成的回档)。更新到支持撤销的版本时，要同时更新 MCP/恢复镜像和 AstrBot 插件。

## 12. 更新和日常管理

### 从旧 JWT 版本迁移

先等待恢复任务结束，保留现有 .env、状态卷和游戏 .gtnh-restore 副本。将旧 .env 中 ALLOWED_GROUPS、ADMIN_USERS 的 JSON 值迁移到 AstrBot 插件页面（或 AstrBot 原有环境），后端已不读取这些字段。auth_secret 填原 AUTH_SECRET，或留空使用原 GTNH_AUTH_SECRET；页面非空值会覆盖环境值。

对照新 .env.example 补齐 DOCKER_GID、MCP_HOST、SERVER_ROOT、BACKUP_DIR、RUNTIME_DIR、STATE_DIR，不用模板覆盖真实密码。删除已废弃的 GTNH_BACKUP_DIR，确认原备份实际位于游戏根目录的 backups 下。更新后的根目录挂载从 /server 变为 /gtnh，但 NAS 根目录、Compose 项目名、runtime/restore-state 卷应保持原值，以继续读取日志和 previous。

本次必须同时更新后端镜像和插件，旧 JWT 插件不能连接新固定密钥后端。维护窗口内重建后端、复制插件、安装依赖并重载，最后验证 Inspector 与 QQ 调用。无需删除历史任务或重新部署 AstrBot。

先更新 MCP 服务，再按第 8 步对应的部署方式更新现有插件及依赖。已有 AstrBot 继续使用原项目管理命令，不执行下面可选模板的聊天服务命令。

```sh
git pull
docker compose up -d --force-recreate
```

仅使用本项目可选 AstrBot 模板的实例继续执行：

```sh
cp -R astrbot_plugin/. runtime/astrbot-data/plugins/astrbot_plugin_gtnh/
docker compose -f compose.chat.yaml up -d --force-recreate astrbot
docker compose -f compose.chat.yaml exec astrbot \
  python -m pip install -r /AstrBot/data/plugins/astrbot_plugin_gtnh/requirements.txt
docker compose -f compose.chat.yaml restart astrbot
```

查看状态和日志（`compose.chat.yaml` 两条仅适用于可选模板实例）：

```sh
docker compose ps
docker compose -f compose.chat.yaml ps
docker compose logs --tail=100 mcp restore
docker compose -f compose.chat.yaml logs --tail=100 astrbot
```

仅可选模板的聊天服务使用 `docker compose -f compose.chat.yaml down` 停止；已有 AstrBot 使用原项目命令。停止 MCP 使用 `docker compose down`。恢复任务执行中不要停止、更新或重建相关服务。不要使用 `docker compose down -v`，它会删除恢复状态卷。

## 常见故障

| 现象 | 检查内容 |
| --- | --- |
| `/health` 失败 | 8000 是否占用；MCP 日志；`.env` 必填项是否合法 |
| MCP 健康但查询玩家失败 | RCON 是否启用；端口映射、密码与地址是否正确 |
| 插件加载失败 | 插件目录和文件；requirements 是否安装；AstrBot 是否为兼容的 4.x |
| 401/403 或调用被拒绝 | 页面 auth_secret（优先）或 GTNH_AUTH_SECRET 是否匹配 AUTH_SECRET；是否重载插件或重建环境 |
| 群未授权 | 用 `/gtnh_identity` 核对平台和群 ID；检查 JSON 数组格式 |
| QQ 消息进不了 AstrBot | 官方机器人是否启用、凭据与开放平台群权限、目标群是否需 @ 触发；检查原适配器日志 |
| AstrBot 连不上健康的 MCP | 是否同一 NAS 且 AstrBot 使用 host 网络；bridge 容器的回环地址不能访问宿主机 MCP |
| 模型不调用工具 | 模型工具调用能力、内置 Agent 与 `gtnh_*` 工具是否启用 |
| 备份列表为空 | 宿主机备份路径；文件是否为顶层普通 `.zip` 或 `.tar.gz` |
| 恢复申请失败 | 容器名、目录结构、磁盘空间、归档结构和 Docker socket 权限 |
| 一直处于维护状态 | 查询是否为 `manual_intervention`，按恢复文档处理 |
| WebUI 无法访问 | 原有 AstrBot 管理端口（模板默认 6185）是否监听；NAS 防火墙设置 |

变量细节见 [配置参考](configuration.md)，状态和回滚见 [恢复文档](restore.md)，验证边界见 [测试说明](testing.md)。AstrBot 界面字段会随版本调整，名称不一致时以当前官方文档为准。

参考资料：[AstrBot Docker 部署](https://docs.astrbot.app/deploy/astrbot/docker.html)、[AstrBot QQ 官方机器人](https://docs.astrbot.app/platform/qqofficial/websockets.html)、[官方适配器身份映射源码](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/platform/sources/qqofficial/qqofficial_platform_adapter.py)。
