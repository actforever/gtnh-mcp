import os

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .bridge import Bridge, resolve_config


@register("astrbot_plugin_gtnh", "qqa", "GTNH 运维与受控备份恢复", "0.1.0")
class GTNHPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.bridge = Bridge(**resolve_config(config, os.environ))

    async def call(self, event, name, arguments=None):
        try:
            return await self.bridge.tool(event, name, arguments or {})
        except ValueError as exc:
            return str(exc)

    @filter.llm_tool(name="gtnh_list_players")
    async def list_players(self, event: AstrMessageEvent):
        """查询 GTNH 在线玩家。"""
        return await self.call(event, "list_players")

    @filter.llm_tool(name="gtnh_announce")
    async def announce(self, event: AstrMessageEvent, message: str):
        """发送 GTNH 服务器公告。

        Args:
            message(string): 单行公告文本，最多300字符
        """
        return await self.call(event, "announce", {"message": message})

    @filter.llm_tool(name="gtnh_save_world")
    async def save_world(self, event: AstrMessageEvent):
        """保存 GTNH 当前世界，不创建备份。"""
        return await self.call(event, "save_world")

    @filter.llm_tool(name="gtnh_list_whitelist")
    async def list_whitelist(self, event: AstrMessageEvent):
        """管理员查询 GTNH 白名单。"""
        return await self.call(event, "list_whitelist")

    @filter.llm_tool(name="gtnh_add_whitelist")
    async def add_whitelist(self, event: AstrMessageEvent, player: str):
        """管理员添加 GTNH 白名单。

        Args:
            player(string): Minecraft玩家名
        """
        return await self.call(event, "add_whitelist", {"player": player})

    @filter.llm_tool(name="gtnh_remove_whitelist")
    async def remove_whitelist(self, event: AstrMessageEvent, player: str):
        """管理员移除 GTNH 白名单。

        Args:
            player(string): Minecraft玩家名
        """
        return await self.call(event, "remove_whitelist", {"player": player})

    @filter.llm_tool(name="gtnh_list_backups")
    async def list_backups(self, event: AstrMessageEvent):
        """查询 GTNH 备份列表，以返回的备份ID申请恢复。"""
        return await self.call(event, "list_backups")

    @filter.llm_tool(name="gtnh_request_restore")
    async def request_restore(self, event: AstrMessageEvent, backup_id: str):
        """管理员申请恢复 GTNH 备份。必须请管理员本人随后发送确认指令，不能代替确认。

        Args:
            backup_id(string): 查询备份工具返回的备份ID
        """
        return await self.call(event, "request_restore", {"backup_id": backup_id})

    @filter.llm_tool(name="gtnh_restore_status")
    async def restore_status(self, event: AstrMessageEvent, job_id: str = ""):
        """查询 GTNH 恢复进度或结果。

        Args:
            job_id(string): 任务编号，留空查询可见任务列表
        """
        return await self.call(event, "restore_status", {"job_id": job_id})

    @filter.llm_tool(name="gtnh_request_undo_restore")
    async def request_undo_restore(self, event: AstrMessageEvent, job_id: str):
        """管理员申请撤销成功回档，恢复那次回档前的存档，不合并进度。须本人随后发送确认指令。

        Args:
            job_id(string): 要撤销的成功恢复任务编号，由恢复状态工具查询
        """
        return await self.call(event, "request_undo_restore", {"job_id": job_id})

    @filter.command("gtnh_confirm", priority=100)
    async def confirm(self, event: AstrMessageEvent, job_id: str):
        # Confirmation is only exposed as a message command, never an LLM tool.
        try:
            response = await self.bridge.confirm(event, job_id)
        except ValueError as exc:
            response = str(exc)
        yield event.plain_result(response)
        event.stop_event()

    @filter.command("gtnh_identity")
    async def identity(self, event: AstrMessageEvent):
        yield event.plain_result(
            f"platform={event.get_platform_name()} group={event.get_group_id()} user={event.get_sender_id()}"
        )
        event.stop_event()
