"""DeepSeek 服务端搜索工具插件；搜索与上下文组装由 API 完成。"""


def apply(ctx):
    ctx.effect(ctx.tools.register({"type": "deepseek_web_search"}, plugin_id=ctx.plugin_id))
