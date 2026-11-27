"""最小本地 function 工具示例。"""

from datetime import datetime


def get_local_time(arguments: dict) -> str:
    now = datetime.now().astimezone()
    return now.strftime("%Y-%m-%d %H:%M:%S %Z%z")


def apply(ctx):
    schema = {
        "type": "function",
        "function": {
            "name": "get_local_time",
            "description": "获取运行本服务的设备的当前本地日期、时间和时区。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    }
    ctx.effect(ctx.tools.register(schema, get_local_time, plugin_id=ctx.plugin_id))
