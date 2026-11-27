# DeepSeekAndroid-beta

当前版本：**DeepSeekAndroid-betaV0.0.1**

一个放在 Termux 里运行的 DeepSeek 手机聊天客户端。Python 后端与前端资源都在 `deepseek_mobile.py` 中，**只需要 Python 标准库，不需要 pip、Node.js 或编译前端**。

> 这是非官方客户端，不是原生 APK。启动后用安卓浏览器访问本机回环地址即可使用。

## 已实现

- DeepSeek 风格移动端界面，兼顾桌面浏览器；浅色、深色和跟随系统。
- 流式正文、深度思考过程、停止生成、重新生成。
- DeepSeek 服务端联网搜索、链接阅读、搜索进度和参考来源。
- 多轮上下文、自动标题、历史会话、重命名、删除和全部清空。
- Markdown、代码块、表格、列表、引用与复制。
- 网页内配置 API Key、模型、API 地址和系统提示词。
- 会话与设置保存在 Termux 本机；停止、断流或服务重启后保留已生成内容。

当前版本聚焦纯文本对话，未实现附件、语音、消息分支或 DSH 插件系统。

## 在 Termux 中启动

### 1. 安装 Python

```bash
pkg install python
```

建议 Python 3.10 或更新版本。

### 2. 运行主程序

把 `deepseek_mobile.py` 放到 Termux 的主目录，然后执行：

```bash
python deepseek_mobile.py
```

终端会显示：

```text
请在浏览器打开：http://127.0.0.1:8080
```

程序会尝试自动打开浏览器；没有自动打开时，手动访问这个地址即可。

### 3. 配置 API Key

首次使用点击页面中的“尚未配置 API Key，点击设置”，或打开左侧栏的“设置”，填入 DeepSeek API Key 并保存。

也可以在启动前通过环境变量提供：

```bash
export DEEPSEEK_API_KEY='你的 API Key'
python deepseek_mobile.py
```

网页中保存的 Key 优先于环境变量。网页只显示 Key 的后四位，完整 Key 不会返回前端。

## 常用命令

换端口：

```bash
python deepseek_mobile.py --port 8081
```

不自动打开浏览器：

```bash
python deepseek_mobile.py --no-open
```

指定数据目录：

```bash
python deepseek_mobile.py --data-dir ~/my-deepseek-data
```

查看全部参数：

```bash
python deepseek_mobile.py --help
```

停止服务：在 Termux 中按 `Ctrl+C`。

## 可选环境变量

```bash
export DEEPSEEK_API_KEY='你的 API Key'
export DEEPSEEK_API_BASE='https://api.deepseek.com'
export DEEPSEEK_MODEL='deepseek-flash'
```

模型可在网页设置中切换为 `deepseek-flash` 或 `deepseek-v4-pro`。程序使用 DeepSeek 的 Anthropic 兼容 Messages 流式接口；联网搜索和链接阅读由 DeepSeek 服务端工具执行，并非本地爬虫。接口说明：

- https://api-docs.deepseek.com/api/create-message/
- https://api-docs.deepseek.com/guides/web_tools/

## 本地数据与安全

默认保存位置：

```text
~/.deepseek-termux/config.json
~/.deepseek-termux/chats.json
```

- 配置文件包含 API Key，程序会尽量将数据和配置文件权限设为仅当前用户可读写。
- 消息内容会按对话需要发送给 DeepSeek API；本程序不接入其他云端存储。
- 默认只监听 `127.0.0.1`。浏览器和 Termux 必须运行在同一台手机上。
- 本程序没有访问认证，不建议监听 `0.0.0.0` 或暴露到局域网、公网。
- 删除程序文件不会自动删除上述数据目录。

## 验证情况

已通过模拟 DeepSeek 上游完成流式正文、思考签名、多轮上下文、搜索引用、停止生成和持久化测试；并在 Chromium 中检查手机、桌面、深色模式及 375／768／1024／1440px 布局。测试未使用真实 API Key；实际回答仍需你的有效 Key 和网络连接。
