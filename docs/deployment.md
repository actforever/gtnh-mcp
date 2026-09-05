# NAS 完整部署教程：GTNH + MCP + AstrBot + NapCat

本文从一台已有 GTNH Docker 服务的 Linux NAS 开始，最终让 QQ 群成员通过 AstrBot 调用 GTNH 工具。示例路径使用 `/volume1/docker`，请替换成 NAS 的实际路径。命令均在 NAS SSH 终端执行。

```text
QQ 群 <-> NapCat <-> OneBot 反向 WebSocket <-> AstrBot 配套插件
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
- 一个已经加入目标群的机器人 QQ 号。
- 支持工具调用的模型服务及 API Key。
- 未占用的端口：8000（MCP）、6185（AstrBot WebUI）、6099（NapCat WebUI）、6199（OneBot 反向 WebSocket）。

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
ALLOWED_GROUPS='["aiocqhttp:你的QQ群号"]'
ADMIN_USERS='["aiocqhttp:你的QQ号"]'
GTNH_CONTAINER_NAME=gtnh
GTNH_SERVER_ROOT=/volume2/sharev9/minecraft/gtnh
GTNH_BACKUP_DIR=/volume2/sharev9/minecraft/gtnh/backups
WORLD_DIRECTORY=World
STOP_TIMEOUT=180
STARTUP_TIMEOUT=900
MAX_ARCHIVE_BYTES=107374182400
MAX_ARCHIVE_MEMBERS=1000000
FREE_SPACE_RESERVE=1073741824
NAPCAT_UID=1000
NAPCAT_GID=1000
```

运行 `id -u` 和 `id -g`，用实际输出替换最后两项。全部变量的用途、单位及相互关系见 [配置参考](configuration.md)。检查两个 Compose 文件；渲染结果包含密码，不要公开粘贴：

```sh
docker compose config --quiet
docker compose -f compose.chat.yaml config --quiet
```

## 4. 构建 MCP 与恢复服务

```sh
docker compose build
docker compose up -d
docker compose ps
docker compose logs --tail=100 restore mcp
curl --fail http://127.0.0.1:8000/health
```

健康接口应返回 `{"status":"ok"}`。它只证明 MCP 进程可用，不证明 RCON 或备份正确。

`mcp` 以非 root 用户提供 HTTP 工具并访问 RCON；`restore` 挂载 Docker socket 与游戏目录，负责受控恢复。恢复服务没有 TCP 端口，只接受共享 Unix socket 请求。GTNH 不是本项目 Compose 的一部分，恢复服务只操作 `.env` 指定的现有容器。

## 5. 从零启动 AstrBot 与 NapCat

`compose.chat.yaml` 使用独立 Compose 项目名 `gtnh-chat` 和 Linux host 网络，因此不会与 MCP 栈的服务名混淆；AstrBot 能通过 `127.0.0.1:8000` 访问 MCP，NapCat 能通过 `127.0.0.1:6199` 连接 AstrBot。

```sh
docker compose -f compose.chat.yaml pull
docker compose -f compose.chat.yaml up -d
docker compose -f compose.chat.yaml ps
docker compose -f compose.chat.yaml logs --tail=100 astrbot napcat
```

持久化数据写入：

```text
runtime/
├── astrbot-data/
├── napcat-config/
└── napcat-qq/
```

从 `docker compose -f compose.chat.yaml logs astrbot` 查找初始用户名和随机密码。浏览器访问 `http://<NAS局域网IP>:6185`，登录后立即修改管理密码。管理端口 6185 和 6099 只应允许可信局域网或 VPN 访问。

## 6. 登录 NapCat 并连接 QQ

运行 `docker compose -f compose.chat.yaml logs napcat` 获取 NapCat WebUI token，访问 `http://<NAS局域网IP>:6099/webui`，登录并修改密码。进入 QQ 登录页面，用机器人 QQ 扫码登录。

在 AstrBot WebUI 的“消息平台/平台”中添加 OneBot v11（`aiocqhttp`）：

- 启用：是。
- 反向 WebSocket 地址：优先 `127.0.0.1`；若当前版本不接受则用 `0.0.0.0`，同时用 NAS 防火墙限制 6199。
- 反向 WebSocket 端口：`6199`。
- token：新生成一个只用于 OneBot 的随机值。

在 NapCat WebUI 的网络配置中新建“WebSocket 客户端/反向 WebSocket”：

- 启用：是。
- URL：`ws://127.0.0.1:6199/ws`。
- token：与 AstrBot 的 OneBot token 完全相同。
- 消息格式：Array（数组）。

保存后查看 AstrBot 日志。出现类似 `aiocqhttp(OneBot v11) adapter connected` 表示成功。反复断开时检查 6199、两边 token，以及 URL 是否包含 `/ws`。

## 7. 配置 AstrBot 模型

在 AstrBot WebUI 添加模型提供商，填写提供商类型、API 地址、API Key 和模型名。选择支持 function/tool calling 的模型，并让当前会话使用 AstrBot 内置 Agent。模型 API Key 只填在 AstrBot，不写进本项目 `.env`。

普通对话正常但从不调用工具时，检查模型是否支持工具调用、内置 Agent 和工具是否启用。

## 8. 安装配套插件

在项目根目录执行：

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

进入 AstrBot 插件管理，确认 `astrbot_plugin_gtnh` 已加载。插件设置填写：

```text
mcp_url = http://127.0.0.1:8000/mcp
```

改过 `MCP_PORT` 时同步修改该地址。启用九个 `gtnh_*` 对话工具。不要再从 AstrBot 原生 MCP 页面添加本服务，否则那条连接无法携带群成员的可信身份。

## 9. 核对群和管理员身份

由管理员账号在目标 QQ 群发送 `/gtnh_identity`，预期返回：

```text
platform=aiocqhttp group=123456789 user=987654321
```

用实际 `platform:group` 更新 `ALLOWED_GROUPS`，用实际 `platform:user` 更新 `ADMIN_USERS`。修改后重新创建服务：

```sh
docker compose up -d --force-recreate
```

管理员权限完全由项目配置决定，不自动继承 QQ 群主、群管理员或 AstrBot 管理员身份。

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

## 12. 更新和日常管理

```sh
git pull
docker compose build
docker compose up -d
cp -R astrbot_plugin/. runtime/astrbot-data/plugins/astrbot_plugin_gtnh/
docker compose -f compose.chat.yaml up -d --force-recreate astrbot
docker compose -f compose.chat.yaml exec astrbot \
  python -m pip install -r /AstrBot/data/plugins/astrbot_plugin_gtnh/requirements.txt
docker compose -f compose.chat.yaml restart astrbot
```

查看状态和日志：

```sh
docker compose ps
docker compose -f compose.chat.yaml ps
docker compose logs --tail=100 mcp restore
docker compose -f compose.chat.yaml logs --tail=100 astrbot napcat
```

停止聊天服务使用 `docker compose -f compose.chat.yaml down`，停止 MCP 使用 `docker compose down`。恢复任务执行中不要停止、更新或重建相关服务。不要使用 `docker compose down -v`，它会删除恢复状态卷。

## 常见故障

| 现象 | 检查内容 |
| --- | --- |
| `/health` 失败 | 8000 是否占用；MCP 日志；`.env` 必填项是否合法 |
| MCP 健康但查询玩家失败 | RCON 是否启用；端口映射、密码与地址是否正确 |
| 插件加载失败 | 插件目录和文件；requirements 是否安装；AstrBot 是否为兼容的 4.x |
| 身份无效 | `GTNH_AUTH_SECRET` 是否与 `AUTH_SECRET` 一致；是否 force-recreate |
| 群未授权 | 用 `/gtnh_identity` 核对平台和群 ID；检查 JSON 数组格式 |
| QQ 消息进不了 AstrBot | NapCat 登录状态、反向 WS URL、6199 和两边 token |
| 模型不调用工具 | 模型工具调用能力、内置 Agent 与 `gtnh_*` 工具是否启用 |
| 备份列表为空 | 宿主机备份路径；文件是否为顶层普通 `.zip` 或 `.tar.gz` |
| 恢复申请失败 | 容器名、目录结构、磁盘空间、归档结构和 Docker socket 权限 |
| 一直处于维护状态 | 查询是否为 `manual_intervention`，按恢复文档处理 |
| WebUI 无法访问 | host 网络端口 6099/6185 是否监听；NAS 防火墙设置 |

变量细节见 [配置参考](configuration.md)，状态和回滚见 [恢复文档](restore.md)，验证边界见 [测试说明](testing.md)。AstrBot 与 NapCat 界面字段会随版本调整，名称不一致时以当前官方文档为准。

参考资料：[AstrBot Docker 部署](https://docs.astrbot.app/deploy/astrbot/docker.html)、[AstrBot OneBot v11 接入](https://docs.astrbot.app/platform/aiocqhttp.html)、[NapCat Docker](https://github.com/NapNeko/NapCat-Docker)。
