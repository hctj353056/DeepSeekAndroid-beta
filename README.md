# DeepSeekAndroid-beta

当前版本：**DeepSeekAndroid-betaV0.0.4**

面向 Android / Termux 的非官方 DeepSeek 文本聊天客户端。Python 后端监听本机回环地址，手机浏览器渲染竖屏界面；V0.0.2 建立模型、存储、Agent 循环和工具的插件化骨架；V0.0.3 加强插件依赖校验与生命周期可靠性。

> 这不是原生 APK。运行时零第三方 Python 依赖，不需要 pip、Node.js 或前端构建，建议 Python 3.10+。

## 版本说明

- **V0.0.4**：修复手机插件弹窗滚动；支持从项目目录读取 API Key 与主体系统提示词文件。
- **V0.0.3**：加强插件依赖校验、依赖循环检测与生命周期测试；继续使用 `main.py` 启动。
- **V0.0.2**：引入插件化实现、服务端工具调用历史保存、本地 API 来源校验和自动化测试。
- **V0.0.1**：保留的 `deepseek_mobile.py` 单文件版本，便于回溯和对比。

V0.0.2 与 V0.0.3 共用项目下的 `data/`；保留的 V0.0.1 使用 `~/.deepseek-termux/`，两者数据不共享。

## V0.0.3 已实现

- 启用插件前检查 `requires` 是否为有效列表，并提前报告未知依赖。
- 检测插件依赖循环，错误信息包含依赖路径，避免无限递归加载。
- 依赖注册关系在插件停用时同步清理。
- 新增依赖循环与未知依赖回归测试。

## V0.0.2 已实现

- 竖屏优先的移动界面，兼顾桌面；浅色、深色和跟随系统主题。
- SSE 流式正文、思考过程折叠区、停止生成、Markdown 与代码复制。
- 多会话、自动标题、重命名、删除和 JSON 本地持久化。
- DeepSeek Chat Completions：思考开关、`low / high / max` 推理强度。
- 本地 Agent 循环：模型 → 本地 function 工具 → 模型，最多 6 步。
- DeepSeek 服务端联网搜索与链接阅读插件，按输入区开关注入请求；服务端工具调用与签名原样保存在会话历史中。
- 本地时钟 function 工具，以及无需网络和 Key 的演示模型。
- 插件发现、依赖加载、即时启停、错误展示和可逆注册。
- 同源静态托管，无 CDN；基础 PWA 清单与静态资源缓存。
- 默认仅监听 `127.0.0.1`；API 校验 Host、Origin、Fetch Metadata 与 JSON 请求类型，并提供静态路径穿越防护、请求体限制和 Key 不回显。

V0.0.2 聚焦纯文本对话，未纳入附件、语音、消息分支、历史 ZIP 导入导出、KaTeX 和 Mermaid。

## 在 Termux 启动 V0.0.3

获取仓库并进入目录：

```bash
pkg install python git
git clone https://github.com/hctj353056/DeepSeekAndroid-beta.git
cd DeepSeekAndroid-beta
python main.py --open
```

若浏览器没有自动打开，手动访问：

```text
http://127.0.0.1:8080
```

停止服务：在 Termux 中按 `Ctrl+C`。端口被占用时：

```bash
python main.py --port 8081 --open
```

首次真实对话前，在「侧边栏 → 设置」中选择 **DeepSeek API** 并填写 API Key。默认接口为 `https://api.deepseek.com`，默认模型为 `deepseek-flash`。也可以先切换为 **本地演示（无需 Key）** 验证完整流程。

使用环境变量提供 Key：

```bash
DEEPSEEK_API_KEY="你的 Key" python main.py
```

环境变量优先于本地文件中的 Key。`GET /api/config` 不会返回 Key；保存到文件时位于 `data/settings.json`，权限设为 `0600`。

## 插件架构

V0.0.2 借鉴 DeepSeek Harness 的架构思想：

1. **共享上下文**：插件通过稳定服务键查找能力，不直接依赖另一个插件的具体实现。
2. **依赖声明**：`plugin.json` 中的 `requires` 决定加载顺序。
3. **服务注册表**：模型进入 `ctx.models`，工具进入 `ctx.tools`；设置、存储与 Agent 分别是 `settings`、`store`、`chat` 服务。
4. **事件扩展**：`chat.request`、`chat.step`、`message.received`、`message.completed`、`tool.call`、`tool.result` 可用于观察或改写流程。
5. **可逆副作用**：每次注册都有 disposer；停用插件时按相反顺序撤销工具、模型和事件订阅。

目录：

```text
DeepSeekAndroid-beta/
├── main.py                    # V0.0.2 启动入口
├── deepseek_mobile.py         # 保留的 V0.0.1 单文件版本
├── profile.json               # 默认插件组合
├── termux_chat/
│   ├── context.py             # 上下文、事件、模型与工具注册表
│   ├── plugin_manager.py      # 发现、依赖、启停、卸载
│   └── server.py              # HTTP、JSON API、SSE
├── plugins/
│   ├── settings/              # 设置服务
│   ├── json_store/            # JSON 会话存储
│   ├── deepseek/              # DeepSeek 模型适配器
│   ├── demo/                  # 离线演示模型
│   ├── agent/                 # Agent 循环
│   ├── clock/                 # 本地 function 工具示例
│   ├── web_search/            # 服务端搜索工具
│   └── url_reader/            # 服务端链接阅读工具
├── web/                       # 无构建步骤的 HTML / CSS / JS
└── tests/                     # 标准库 unittest
```

### 新增工具插件

创建 `plugins/my_tool/plugin.json`：

```json
{
  "id": "my_tool",
  "name": "我的工具",
  "version": "0.1.0",
  "description": "一个最小工具插件",
  "requires": [],
  "provides": ["tools:my_tool"]
}
```

创建 `plugins/my_tool/plugin.py`：

```python
def my_tool(arguments: dict) -> str:
    return "工具收到：" + str(arguments)


def apply(ctx):
    schema = {
        "type": "function",
        "function": {
            "name": "my_tool",
            "description": "演示自定义工具",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "输入文本"}
                },
                "required": ["text"]
            }
        }
    }
    ctx.effect(ctx.tools.register(schema, my_tool, plugin_id=ctx.plugin_id))
```

重启服务后，插件会出现在管理页，默认停用；启用后立即可供 Agent 使用，启停状态保存在 `data/plugin_states.json`。若希望它随 profile 默认加载，再把 `my_tool` 加入 `profile.json` 的 `plugins` 数组。

**修改已有插件的 Python 代码后需要重启服务；界面上的启停不会重载源码。**

### 注册模型适配器

模型对象实现：

```python
def stream(request: dict, cancel_event):
    yield {"type": "reasoning", "text": "可选推理片段"}
    yield {"type": "content", "text": "正文片段"}
    # 本地工具调用时：
    # yield {"type": "tool_call", "index": 0, "id": "call_1", "name": "my_tool", "arguments": "{}"}
    yield {"type": "usage", "usage": {"total_tokens": 0}}
```

插件注册与撤销：

```python
def apply(ctx):
    provider = MyProvider()
    ctx.effect(ctx.models.register("my_provider", provider))
```

`request` 包含 `model`、`messages`、`thinking`、`reasoning_effort` 和 `tools`。Agent 会根据当前设置解析提供方，不需要修改主程序。

### 监听或改写请求

```python
def apply(ctx):
    def add_instruction(request):
        request["messages"].insert(0, {
            "role": "system",
            "content": "请使用简洁中文回答。"
        })
        return request

    ctx.on("chat.request", add_instruction)
```

`chat.request` 和 `tool.call` 是 waterfall 事件：返回对象会传给下一个监听器；返回 `None` 表示不修改。其余已公开事件用于同步观察。

## 数据与安全

V0.0.2 及 V0.0.3 数据位置：

- 会话：`data/conversations/*.json`。
- 设置：`data/settings.json`。
- 插件启停：`data/plugin_states.json`。

注意事项：

- 删除整个 `data/` 目录会清除 V0.0.2 的所有本地会话与配置，请先备份。
- 插件是与主程序同权限的 Python 代码，**只安装你信任的插件**。
- 不要把真实 Key 写进插件、前端文件或分享出去的配置文件。
- 本地 API 会校验 Host、Origin、Fetch Metadata 与 JSON 请求类型，降低 DNS rebinding 和跨站请求风险；这不是用户身份认证。非回环监听必须显式使用 `--allow-remote`，仍不建议在公共网络上使用；如需公网访问，应另行增加认证与 TLS。
- 消息内容会按对话需要发送给 DeepSeek API，本工程不接入其他云端存储。

## 验证

```bash
python -m unittest discover -s tests -v
```

测试覆盖插件注册撤销、密钥回显保护、演示对话持久化、模型—工具—模型闭环、历史重建、服务端工具调用签名往返、本地 API 来源校验和取消。

本版本已检查 375／768／1024／1440px 布局、手机与桌面交互、深色主题和插件热启停，并完成禁用第三方站点包后的冷启动验证。测试未使用真实 API Key；实际回答仍需有效 Key 和网络连接。

参考接口：

- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)
- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)

## 运行保留的 V0.0.1

```bash
python deepseek_mobile.py
```

V0.0.1 的详细启动参数、数据说明和功能边界可通过以下命令查看：

```bash
python deepseek_mobile.py --help
```


## 从项目目录加载主体配置（V0.0.4）

可以不在网页设置中粘贴系统提示词或 API Key。启动时设置插件会优先读取项目根目录下的本地文件：

```text
DeepSeekAndroid-beta/
├── 001.key                 # DeepSeek API Key，纯文本，一行即可
└── 灵魂架构/
    └── 灵魂架构.md          # 系统提示词，支持 Markdown
```

在项目根目录执行（已有文件时不要覆盖）：

```bash
mkdir -p "灵魂架构"
touch "001.key"
touch "灵魂架构/灵魂架构.md"
```

用文本编辑器填写 `001.key` 和 `灵魂架构/灵魂架构.md`。提示词文件存在且非空时，它将覆盖网页设置中的系统提示词；提示词正文只在后端读取，不会通过本地网页 API 回传。Key 文件存在且非空时优先使用，网页设置中的 Key 不会覆盖它。环境变量 `DEEPSEEK_API_KEY` 优先级最高。

也可以通过环境变量指定其他文件路径：

```bash
DEEPSEEK_API_KEY_FILE="/绝对路径/your.key" \
DEEPSEEK_SYSTEM_PROMPT_FILE="/绝对路径/identity.md" \
python main.py --open
```

文件内容修改后，重启服务即可重新加载。项目根目录的 `001.key` 与 `灵魂架构/` 已加入 `.gitignore`，不会被 Git 提交；分享或发布项目时请确认没有把个人提示词或 Key 打包进去。

**安全提醒：** 若项目位于 Android 共享存储 `/storage/emulated/0/`，Key 文件是明文，其他拥有共享存储访问权限的应用可能读取。更安全的做法是将 Key 放在 Termux 私有目录（例如 `$HOME/.config/deepseek/001.key`），并通过 `DEEPSEEK_API_KEY_FILE` 指向它。共享存储上的文件权限不能可靠地限制其他应用读取。
