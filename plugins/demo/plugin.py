"""可离线运行的演示模型，用于完整验证本地链路。"""

from __future__ import annotations

import time


class DemoProvider:
    @staticmethod
    def _pieces(text: str, size: int = 8):
        for offset in range(0, len(text), size):
            yield text[offset:offset + size]

    def stream(self, request: dict, cancel_event):
        user_text = next(
            (message.get("content", "") for message in reversed(request["messages"]) if message.get("role") == "user"),
            "",
        )
        if request.get("thinking", True):
            reasoning = "本地演示正在检查消息、插件状态和会话上下文。切换到 DeepSeek 后，这里会显示真实模型返回的推理内容。"
            for piece in self._pieces(reasoning, 12):
                if cancel_event.is_set():
                    return
                yield {"type": "reasoning", "text": piece}
                time.sleep(0.008)
        preview = user_text.strip().replace("\n", " ")[:120] or "空消息"
        answer = (
            "本地链路已接通。\n\n"
            f"你发送的是：**{preview}**\n\n"
            "- Python 标准库后端\n"
            "- 可撤销的插件注册\n"
            "- 流式消息与本地会话\n\n"
            "在设置中切换为 `DeepSeek API` 并填写 Key，即可进行真实对话。"
        )
        for piece in self._pieces(answer):
            if cancel_event.is_set():
                return
            yield {"type": "content", "text": piece}
            time.sleep(0.012)
        yield {"type": "usage", "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


def apply(ctx):
    ctx.effect(ctx.models.register("demo", DemoProvider()))
