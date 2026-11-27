"""DeepSeek 服务端链接阅读工具插件。"""


def apply(ctx):
    ctx.effect(ctx.tools.register({"type": "deepseek_read_url"}, plugin_id=ctx.plugin_id))
