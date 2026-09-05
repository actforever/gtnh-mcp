# 备份恢复

## 输入与预检查

备份目录默认为 `/backups`，模板映射 NAS 的 `/volume2/sharev9/minecraft/gtnh/backups`。只枚举目录顶层的普通 `.zip` 和 `.tar.gz` 文件，列表返回 `format` 字段；ID 为文件名 SHA-256，任务另外绑定文件内容 SHA-256。重命名文件会改变 ID，修改文件会使已有恢复申请失效。

解压结构必须包含大小写准确的 `WORLD_DIRECTORY`（默认 `World`）和 `visualprospecting` 两个目录（允许归档省略显式目录条目），且不能包含其他顶层内容。拒绝绝对路径、`..`、反斜杠、冒号、控制字符、重复路径、链接和特殊文件。限制展开大小、条目数并检查暂存磁盘空间。ZIP 仅支持未加密的 Stored/Deflate；申请时完整读取文件内容验证 CRC，暂存时再次流式读取校验，不使用不受约束的 `extractall`。

例如 `2026-09-06-01-19-40.zip` 内应直接有 `World/...` 和 `visualprospecting/...`，不能再包一层 `gtnh/`。文件名中的日期不参与解析或验证；列表按文件修改时间排序。

任务保存申请时的目录名，之后修改配置不会改变该任务的切换、回滚目标。旧日志缺少 `world_dirs` 字段时继续使用 `Worlds` 与 `visualprospecting`。

服务端根目录必须整体挂载；两个子目录不可单独 bind mount，必须位于同一文件系统。暂存和旧存档均放在根目录下 `.gtnh-restore/<任务编号>/`，目录切换使用同一文件系统的 rename。恢复文件不继承归档中的 UID/GID 或特殊权限，使用当前对应存档目录所有者，目录 0750、文件 0640。

## 正常流程

1. 管理员通过对话申请备份 ID。校验归档、空间、存档布局和容器状态，返回十分钟有效的任务编号。
2. 原申请人在原群发送 `/gtnh_confirm <任务编号>`。持久化维护标记和 queued 状态；后台独占恢复锁。
3. 再次验证内容指纹并暂存到 `incoming`，再次核对指纹。此时仍不修改当前存档。
4. 发送公告，将阶段写为 stopping，关闭容器自动重启策略，发送 RCON `stop`，等待 Docker 确认进程退出。不调用会超时强杀的 Docker stop 接口。
5. 写入 switching 阶段，每次 rename 前检查容器仍已停止，将当前目录移动到 `previous`，将暂存目录移动到服务端根目录。
6. 写入 starting，启动同一个容器，等待 RCON `list` 成功，再恢复原 Docker 重启策略，记录 succeeded 并解除维护状态。

不要在恢复期间用其他 NAS 管理工具、Watchtower 或外部守护服务重建或启动 GTNH 容器；本项目只能控制指定容器的 Docker 重启策略。启动脚本必须用单次 `exec java`，不能用 `while true` 在容器内重启 Java，否则 RCON stop 后容器不会退出。参见 [部署教程](deployment.md) 的启动脚本调整。

## 失败与重启

- `failed`：停服前失败，当前存档未修改；暂存内容可能保留。
- `rolled_back`：停服或切换后的错误触发回滚，旧存档已恢复并通过 RCON 就绪检测。
- `manual_intervention`：无法确认停服、容器被替换、目录状态异常或回滚启动失败；保留现场与维护标记，继续拒绝写操作。
- 启动时发现 manual_intervention 任务会重新建立维护标记，即使 runtime 标记意外丢失也不会自动解除限制。
- 服务启动时扫描持久化任务。queued/preparing 中断标记失败；stopping/switching/starting 或回滚途中中断按目录 rename 记录回滚，不盲目继续覆盖。
- 已存在的 `previous` 表示旧目录尚未移回；回滚把新存档移入 `failed` 后将旧目录移回。各步可通过实际目录存在情况重入。

修改前保留的旧存档和失败存档不自动删除，也不提供聊天清理命令。成功任务的 `previous` 可用于人工回退。因回滚恢复到线上位置的旧目录不会在 `previous` 中额外留副本。

## 人工处理

维护状态不能通过聊天强制解除。先查询任务 ID、错误和阶段，停止 MCP 与恢复服务，检查指定 GTNH 容器状态、`.gtnh-restore/<ID>/{incoming,previous,failed}` 与线上两个目录。确认进程停止后才可人工整理文件；先做额外副本，切勿合并覆盖新旧世界。

修复目录、启动测试及恢复 Docker 重启策略后，管理员可离线将该任务 JSON 的 phase 改为 `failed` 并填写处理说明，保留所有其他字段，然后启动恢复服务。它会依据终态清除该任务维护标记。操作前备份任务 JSON；不要只删除维护标记绕过未处理任务。

恢复服务正常关闭会等待任务完成，Compose 给出 35 分钟退出窗口。超过实际关闭窗口或断电仍依赖持久化日志恢复；目录 fsync 的保证取决于 NAS 文件系统和磁盘。任务和 runtime 卷必须持久保留，不能使用 `docker compose down -v` 丢弃状态。
