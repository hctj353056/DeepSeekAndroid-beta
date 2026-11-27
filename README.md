# DeepSeekAndroid-beta

当前版本：**DeepSeekAndroid-betaV0.0.6**

面向 Android / Termux 的非官方 DeepSeek 文本聊天客户端。Python 稳定宿主监听本机回环地址，手机浏览器渲染竖屏界面；V0.0.5 建立 A／B 主体热切换，V0.0.6 新增独立、安全的回答渲染模块，完善 Markdown、GFM 表格、移动端展示与增量流式渲染。

> 这不是原生 APK。运行时零第三方 Python 依赖，不需要 pip、Node.js 或前端构建，建议 Python 3.10+。

## 版本说明

- **V0.0.6**：新增独立回答渲染模块，支持安全 Markdown、GFM 表格、代码复制、窄屏容器滚动与按动画帧批量更新的增量流式呈现；预留 ContentBlock 扩展点。
- **V0.0.5**：引入稳定宿主、A／B 独立智能体主体、不可变历史版本、隔离候选检查、激活与回滚；静态资源改为网络优先，避免旧 Service Worker 缓存遮蔽修复。
- **V0.0.4**：修复手机插件弹窗滚动；首次启动自动创建 API Key 文件和默认灵魂架构；支持从本地文件加载主体提示词；对 TLS EOF 连接错误提供重试与诊断。
- **V0.0.3**：加强插件依赖校验、依赖循环检测与生命周期测试；继续使用 `main.py` 启动。
- **V0.0.2**：引入插件化实现、服务端工具调用历史保存、本地 API 来源校验和自动化测试。
- **V0.0.1**：保留的 `deepseek_mobile.py` 单文件版本，便于回溯和对比。

V0.0.2 及后续版本共用项目下的 `data/`；保留的 V0.0.1 使用 `~/.deepseek-termux/`，两者数据不共享。V0.0.5 的 DSA 版本快照也位于 `data/dsa/`。

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

## 在 Termux 启动 V0.0.6

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

首次启动时，程序会自动在项目根目录创建 `001.key`，并创建 `灵魂架构/灵魂架构.md`，其中包含默认主体架构。你只需用文本编辑器把 API Key 填入 `001.key`；默认系统提示词会自动读取灵魂架构文件并注入模型请求。默认接口为 `https://api.deepseek.com`，默认模型为 `deepseek-flash`。也可以先切换为 **本地演示（无需 Key）** 验证完整流程。

使用环境变量提供 Key：

```bash
DEEPSEEK_API_KEY="你的 Key" python main.py
```

环境变量优先于 `001.key`。网页设置中输入的 Key 也会写入项目根目录的 `001.key`；`data/settings.json` 不再保存 API Key。`GET /api/config` 不会返回 Key。

## 回答渲染（V0.0.6）

`web/response-renderer.js` 是独立的回答渲染模块，由 `web/app.js` 的消息入口调用，不改变模型适配器、Agent、插件或 A/B 主体协议。运行时无第三方 Python 依赖、无外部 CDN、无前端构建步骤。代码高亮使用本地 vendored highlight.js 11.12.0（BSD-3-Clause），相关许可证随仓库提供。

当前实现：1—6 级标题、段落、无序与有序列表、嵌套列表、嵌套引用、任务清单（禁用复选框）、GFM 删除线、HTTP／HTTPS 链接、粗体、斜体、粗斜体、行内代码、代码围栏、水平分隔线及 GFM 风格表格。代码保留语言标签与复制按钮；常见语言使用本地 highlight.js 进行语法高亮，未知语言安全回退为纯代码文本。代码块和表格分别在自身容器内横向滚动。未闭合代码围栏按仍在累积的代码处理，不支持或无法识别的语法回退为安全文本。

安全边界：所有模型文本默认转义，不执行原始 HTML、脚本或事件属性；链接仅允许 HTTP／HTTPS，新窗口链接带 `noopener noreferrer nofollow`。行内代码不会被强调或链接规则二次改写。

预留的 `renderContent()` 目前仅接受 `markdown`、`text`、`code` 三类 ContentBlock，其他类型明确报错。流式阶段会将未完成的 Markdown 块暂以安全纯文本显示；块边界确定后再渲染并固定到 DOM，避免每个 token 重建整条回答。**本阶段不包含图表、流程图、交互卡片或完整 ResponseSchema**；这些保留给后续阶段。

## DSA A／B 主体热切换（V0.0.5）

`main.py` 是尽量保持稳定的宿主，负责对外端口、请求代理、进程管理、候选检查、活动指针与回滚。真正的 Agent、模型、插件和存储运行在独立的 A／B worker 进程中。首次启动会把当前运行源码冻结为 `v1`，在槽位 A 启动；宿主重启后也会恢复上次激活的版本，而不是直接运行被修改的实验源码。

一次更新流程：

1. **修改实验源码**：编辑项目根目录中的 `termux_chat/`、`plugins/`、`web/` 或 `profile.json`。正在运行的主体使用独立快照，不会随文件保存立即改变。
2. **准备候选**：打开「侧边栏 → 主体 → 从实验源码准备候选」。宿主创建 `v2`、`v3` 等不可变快照，并在另一个槽位使用隔离数据启动检查。
3. **检查结果**：失败时只报告错误，当前生产主体继续运行；修复源码后可再次准备，失败快照也保留在历史版本中。
4. **激活**：候选通过隔离检查后，点击「激活候选」。宿主先确认当前没有生成中的回复，再让候选接入正式数据并重新检查，最后原子切换活动指针。
5. **回滚**：激活后的旧主体保留为回滚目标。点击「回滚上一版本」可恢复；若旧进程已不在，宿主会从对应不可变快照重新启动。

```text
v1 / A 生产 → 在 B 准备 v2 → 激活 v2 / B → v1 / A 备选
                                                   ↓
                                      下一轮在 A 准备 v3，如此交替
```

版本源码位于 `data/dsa/versions/v*/source/`，每个版本都有父版本、创建时间和源码 SHA-256。版本快照只包含运行源码，不复制会话、插件启停状态、Key 或灵魂架构文件。候选的隔离检查验证进程与插件能否启动，**不等于完整业务测试，也不会使用真实 API Key 发模型请求**。

`main.py` 与稳定宿主自身不在热切换范围内；修改宿主结构后仍需正常重启。正在流式生成的回复不会被强制迁移，激活和回滚会等待用户先让当前请求完成。

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
├── main.py                    # 稳定宿主启动入口
├── deepseek_mobile.py         # 保留的 V0.0.1 单文件版本
├── profile.json               # 默认插件组合
├── termux_chat/
│   ├── context.py             # 上下文、事件、模型与工具注册表
│   ├── plugin_manager.py      # 发现、依赖、启停、卸载
│   ├── server.py              # 主体内 HTTP、JSON API、SSE
│   ├── worker.py              # A／B 独立主体进程入口
│   ├── supervisor.py          # 槽位、候选、激活、回滚与进程管理
│   ├── supervisor_server.py   # 稳定宿主控制 API 与请求代理
│   └── versions.py            # 不可变源码快照与活动版本指针
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
│   └── response-renderer.js   # 独立安全回答渲染模块
└── tests/                     # Python unittest 与 Node.js 渲染测试
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

**界面上的插件启停只撤销或恢复注册，不会重载源码。**修改已有插件代码后，在「主体」页准备候选并激活，即可在不重启稳定宿主的情况下运行新代码。

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

V0.0.2 及后续版本的数据位置：

- 会话：`data/conversations/*.json`。
- 设置：`data/settings.json`。
- 插件启停：`data/plugin_states.json`。
- DSA 不可变版本：`data/dsa/versions/v*/`。
- 活动与回滚指针：`data/dsa/state.json`。
- 候选隔离数据：`data/dsa/slot_data/`。
- 主体日志：`data/dsa/logs/`。

注意事项：

- 删除整个 `data/` 目录会清除本地会话、配置和全部 DSA 历史版本，请先备份。
- 插件是与主程序同权限的 Python 代码，**只安装你信任的插件**。
- 不要把真实 Key 写进插件、前端文件或分享出去的配置文件。
- 本地 API 会校验 Host、Origin、Fetch Metadata 与 JSON 请求类型，降低 DNS rebinding 和跨站请求风险；这不是用户身份认证。非回环监听必须显式使用 `--allow-remote`，仍不建议在公共网络上使用；如需公网访问，应另行增加认证与 TLS。
- 消息内容会按对话需要发送给 DeepSeek API，本工程不接入其他云端存储。

## 验证

```bash
python -m unittest discover -s tests -v
node --test tests/test_response_renderer.js
```

Python 测试共 22 项，覆盖插件、存储、DeepSeek 适配器、本地 API、安全和 DSA A/B 主体生命周期。渲染模块现有 16 项 Node.js 测试，覆盖基础语法、GFM 表格、代码转义、链接协议、原始 HTML 安全边界、未闭合代码围栏、ContentBlock 回退、流式增量渲染、删除线、任务清单及嵌套结构。GitHub Actions 会在 Python 3.10／3.12 上执行 22 项 Python 单元测试，并使用 Node.js 20 执行 16 项回答渲染测试；PR 检查支持 main、release/**、feature/** 与 fix/** 分支。

本版本已在 Linux／Chromium 中通过真实 SSE 检查长回答、表格、长代码、代码复制、深浅主题及 375／768／1024px 布局；表格与代码只在各自容器内横向滚动，用户上翻后不会被流式输出强制拉回，控制台无错误。测试未使用真实 API Key，尚未在真实 Termux 设备上执行；实际回答仍需有效 Key 和网络连接。

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


## 项目目录中的主体配置（V0.0.4）

首次启动时自动生成以下文件，无需手动创建：

```text
DeepSeekAndroid-beta/
├── 001.key                 # API Key 文件，初始为空
└── 灵魂架构/
    └── 灵魂架构.md          # 自动写入默认主体架构，可自行修改
```

默认灵魂架构会自动作为系统提示词注入模型请求。文件内容只在后端读取，不会通过本地网页 API 回传。修改文件并重启服务即可生效。网页设置中输入的 Key 也会写入 `001.key`；环境变量 `DEEPSEEK_API_KEY` 优先级最高。



也可以通过环境变量指定其他文件路径：

```bash
DEEPSEEK_API_KEY_FILE="/绝对路径/your.key" \
DEEPSEEK_SYSTEM_PROMPT_FILE="/绝对路径/identity.md" \
python main.py --open
```

文件内容修改后，重启服务即可重新加载。项目根目录的 `001.key` 与 `灵魂架构/` 已加入 `.gitignore`，不会被 Git 提交；分享或发布项目时请确认没有把个人提示词或 Key 打包进去。

**安全提醒：** 若项目位于 Android 共享存储 `/storage/emulated/0/`，Key 文件是明文，其他拥有共享存储访问权限的应用可能读取。更安全的做法是将 Key 放在 Termux 私有目录（例如 `$HOME/.config/deepseek/001.key`），并通过 `DEEPSEEK_API_KEY_FILE` 指向它。共享存储上的文件权限不能可靠地限制其他应用读取。


## TLS EOF 连接错误排查

如果出现 `UNEXPECTED_EOF_WHILE_READING`，表示 TLS 连接在读取期间被提前关闭。它通常是网络、VPN、代理或 API 地址连接问题，不等同于 Key 错误。V0.0.4 会在建立连接时对短暂中断重试，并显示更明确的提示。

可先检查设置中的 Base URL（默认 `https://api.deepseek.com`），再尝试切换网络或 VPN。若持续发生，可在 Termux 中执行：

```bash
curl -Iv https://api.deepseek.com
```

此命令只检查 HTTPS/TLS 连接，不会验证模型请求或 API Key。请不要把真实 API Key 贴到公开日志或截图中。
