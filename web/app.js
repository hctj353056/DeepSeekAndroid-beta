"use strict";

const $ = (selector) => document.querySelector(selector);
const elements = {
  shell: $("#app"), sidebar: $("#sidebar"), backdrop: $("#sidebarBackdrop"),
  conversationList: $("#conversationList"), conversationTitle: $("#conversationTitle"),
  welcome: $("#welcome"), welcomeHint: $("#welcomeHint"), messageList: $("#messageList"), chatScroll: $("#chatScroll"),
  composer: $("#composer"), input: $("#messageInput"), send: $("#sendButton"), stop: $("#stopButton"),
  thinking: $("#thinkingToggle"), search: $("#searchToggle"), read: $("#readToggle"),
  runtimeStatus: $("#runtimeStatus"), pluginCount: $("#pluginCount"),
  settingsDialog: $("#settingsDialog"), settingsForm: $("#settingsForm"),
  provider: $("#providerInput"), model: $("#modelInput"), baseUrl: $("#baseUrlInput"),
  apiKey: $("#apiKeyInput"), apiKeyHint: $("#apiKeyHint"), clearKey: $("#clearKeyButton"),
  effort: $("#effortInput"), systemPrompt: $("#systemPromptInput"), systemPromptHint: $("#systemPromptHint"), theme: $("#themeInput"),
  pluginsDialog: $("#pluginsDialog"), pluginList: $("#pluginList"),
  runtimeDialog: $("#runtimeDialog"), runtimeBadge: $("#runtimeBadge"),
  activeVersion: $("#activeVersion"), activeSlot: $("#activeSlot"), activeRuntimeDetail: $("#activeRuntimeDetail"),
  candidateStatus: $("#candidateStatus"), prepareCandidate: $("#prepareCandidate"),
  activateCandidate: $("#activateCandidate"), rollbackRuntime: $("#rollbackRuntime"), versionList: $("#versionList"),
  toast: $("#toast"),
};

const state = {
  config: null,
  conversations: [],
  current: null,
  plugins: [],
  streaming: false,
  controller: null,
  requestId: null,
  clearKey: false,
  dsa: null,
  dsaBusy: false,
  options: { thinking: true, web_search: false, read_url: false },
};

let toastTimer = 0;
function toast(message) {
  clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.add("visible");
  toastTimer = window.setTimeout(() => elements.toast.classList.remove("visible"), 2600);
}

async function api(path, options = {}) {
  const request = { ...options, headers: { ...(options.headers || {}) } };
  if (request.body && typeof request.body !== "string") {
    request.headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(request.body);
  }
  const response = await fetch(path, request);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
  return data;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function icon(name) {
  return `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

function applyTheme(preference) {
  const resolved = preference === "auto"
    ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
    : preference;
  document.body.dataset.theme = resolved;
  document.querySelector('meta[name="theme-color"]').content = resolved === "dark" ? "#151517" : "#ffffff";
}

function openSidebar() { elements.shell.classList.add("sidebar-open"); }
function closeSidebar() { elements.shell.classList.remove("sidebar-open"); }

function setToggle(button, selected) {
  button.classList.toggle("selected", selected);
  button.setAttribute("aria-pressed", String(selected));
}

function syncOptions() {
  setToggle(elements.thinking, state.options.thinking);
  setToggle(elements.search, state.options.web_search);
  setToggle(elements.read, state.options.read_url);
}

function pluginEnabled(id) {
  return Boolean(state.plugins.find((plugin) => plugin.id === id)?.enabled);
}

function syncPluginControls() {
  const active = state.plugins.filter((plugin) => plugin.enabled).length;
  elements.pluginCount.textContent = `${active}/${state.plugins.length}`;
  elements.search.disabled = !pluginEnabled("web_search");
  elements.read.disabled = !pluginEnabled("url_reader");
  elements.search.title = elements.search.disabled ? "请先在插件页启用联网搜索" : "由 DeepSeek 服务端搜索";
  elements.read.title = elements.read.disabled ? "请先在插件页启用链接阅读" : "阅读消息中的链接";
  if (elements.search.disabled) state.options.web_search = false;
  if (elements.read.disabled) state.options.read_url = false;
  syncOptions();
}

function syncRuntimeStatus() {
  if (!state.config) return;
  const demo = state.config.provider === "demo";
  elements.runtimeStatus.textContent = demo ? "本地演示模型" : `${state.config.model} · 回环服务`;
  elements.welcomeHint.textContent = demo ? "离线演示已就绪 · 会话保存在本机" : "本地运行，插件按需协作";
}

function resizeInput() {
  elements.input.style.height = "auto";
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, 180)}px`;
  elements.send.disabled = state.streaming || !elements.input.value.trim();
}

function setGenerating(active) {
  state.streaming = active;
  elements.send.hidden = active;
  elements.stop.hidden = !active;
  elements.input.readOnly = active;
  resizeInput();
}

function nearBottom() {
  const node = elements.chatScroll;
  return node.scrollHeight - node.scrollTop - node.clientHeight < 180;
}

function scrollBottom(force = false) {
  if (force || nearBottom()) {
    requestAnimationFrame(() => { elements.chatScroll.scrollTop = elements.chatScroll.scrollHeight; });
  }
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  }
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function renderConversations() {
  if (!state.conversations.length) {
    elements.conversationList.innerHTML = '<div class="list-placeholder">还没有历史对话</div>';
    return;
  }
  elements.conversationList.innerHTML = state.conversations.map((conversation) => `
    <div class="conversation-item ${state.current?.id === conversation.id ? "active" : ""}" data-id="${conversation.id}">
      <button class="conversation-select" data-action="select" aria-label="打开对话：${escapeHtml(conversation.title)}">
        <span>${escapeHtml(conversation.title)}</span>
        <small>${conversation.message_count} 条消息 · ${formatDate(conversation.updated_at)}</small>
      </button>
      <div class="conversation-tools">
        <button data-action="rename" aria-label="重命名对话" title="重命名">${icon("edit")}</button>
        <button data-action="delete" data-delete aria-label="删除对话" title="删除">${icon("trash")}</button>
      </div>
    </div>`).join("");
}

async function refreshConversations() {
  const data = await api("/api/conversations");
  state.conversations = data.conversations;
  renderConversations();
}

function newChat() {
  if (state.streaming) {
    toast("请先停止当前生成");
    return;
  }
  state.current = null;
  elements.conversationTitle.textContent = "新对话";
  elements.messageList.innerHTML = "";
  elements.welcome.hidden = false;
  renderConversations();
  closeSidebar();
  elements.input.focus();
}

async function loadConversation(conversationId) {
  if (state.streaming) {
    toast("请先停止当前生成");
    return;
  }
  const conversation = await api(`/api/conversations/${conversationId}`);
  state.current = conversation;
  elements.conversationTitle.textContent = conversation.title;
  elements.welcome.hidden = true;
  renderMessages(conversation.messages);
  renderConversations();
  closeSidebar();
  scrollBottom(true);
}

async function renameConversation(conversation) {
  const title = window.prompt("重命名对话", conversation.title);
  if (title === null || !title.trim()) return;
  const updated = await api(`/api/conversations/${conversation.id}`, { method: "PATCH", body: { title } });
  if (state.current?.id === conversation.id) {
    state.current.title = updated.title;
    elements.conversationTitle.textContent = updated.title;
  }
  await refreshConversations();
  toast("已重命名");
}

async function deleteConversation(conversation) {
  if (state.streaming && state.current?.id === conversation.id) {
    toast("请先停止当前生成");
    return;
  }
  if (!window.confirm(`确定删除“${conversation.title}”吗？此操作不可撤销。`)) return;
  await api(`/api/conversations/${conversation.id}`, { method: "DELETE" });
  if (state.current?.id === conversation.id) newChat();
  await refreshConversations();
  toast("会话已删除");
}

function renderMarkdown(source) {
  return window.DSAResponseRenderer.renderMarkdown(source);
}

function createUserMessage(message) {
  const element = document.createElement("article");
  element.className = "message message-user";
  const bubble = document.createElement("div");
  bubble.className = "user-bubble";
  bubble.textContent = message.content;
  element.appendChild(bubble);
  return element;
}

function createAssistantMessage(message = {}, streaming = false) {
  const element = document.createElement("article");
  element.className = "message message-assistant";
  element.innerHTML = `<div class="assistant-avatar">${icon("logo")}</div><div class="assistant-content"><div class="reasoning-slot"></div><div class="tool-slot"></div><div class="markdown"></div><div class="message-actions"><button type="button" class="message-action copy-message">${icon("copy")}<span>复制</span></button></div></div>`;
  const content = element.querySelector(".assistant-content");
  const markdown = content.querySelector(".markdown");
  const actions = content.querySelector(".message-actions");
  actions.hidden = streaming || !message.content;
  const nodes = { element, content, markdown, actions, reasoning: null, toolSlot: content.querySelector(".tool-slot") };
  updateReasoning(nodes, message.reasoning || "", streaming);
  markdown.innerHTML = renderMarkdown(message.content || "");
  markdown.classList.toggle("typing-cursor", streaming && !message.content);
  for (const call of message.tool_calls || []) {
    const name = call.function?.name || "工具";
    addToolEvent(nodes, name, "已完成工具调用");
  }
  return nodes;
}

function updateReasoning(nodes, text, streaming = false) {
  if (!text) {
    nodes.content.querySelector(".reasoning-slot").innerHTML = "";
    nodes.reasoning = null;
    return;
  }
  if (!nodes.reasoning) {
    const details = document.createElement("details");
    details.className = "reasoning-box";
    details.open = streaming;
    details.innerHTML = `<summary>${streaming ? "正在思考…" : "查看思考过程"}</summary><div class="reasoning-text"></div>`;
    nodes.content.querySelector(".reasoning-slot").replaceChildren(details);
    nodes.reasoning = details;
  }
  nodes.reasoning.open = streaming;
  nodes.reasoning.querySelector("summary").textContent = streaming ? "正在思考…" : "查看思考过程";
  nodes.reasoning.querySelector(".reasoning-text").textContent = text;
}

function addToolEvent(nodes, name, detail) {
  const event = document.createElement("div");
  event.className = "tool-event";
  event.dataset.toolName = name;
  event.innerHTML = `${icon("puzzle")}<span><strong>${escapeHtml(name)}</strong> · ${escapeHtml(detail)}</span>`;
  nodes.toolSlot.appendChild(event);
  return event;
}

function renderMessages(messages) {
  elements.messageList.innerHTML = "";
  for (const message of messages) {
    if (message.role === "user") {
      elements.messageList.appendChild(createUserMessage(message));
    } else if (message.role === "assistant") {
      elements.messageList.appendChild(createAssistantMessage(message).element);
    }
  }
  elements.welcome.hidden = messages.length > 0;
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }
  if (button) {
    const label = button.querySelector("span");
    if (label) {
      label.textContent = "已复制";
      setTimeout(() => { label.textContent = "复制"; }, 1300);
    } else {
      const original = button.innerHTML;
      button.innerHTML = `${icon("check")}已复制`;
      setTimeout(() => { button.innerHTML = original; }, 1300);
    }
  }
}

function parseSSEBlock(block) {
  const data = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  return data.length ? JSON.parse(data.join("\n")) : null;
}

async function sendMessage() {
  const text = elements.input.value.trim();
  if (!text || state.streaming) return;
  if (state.config.provider === "deepseek" && !state.config.api_key_set) {
    openSettings();
    toast("请先填写 DeepSeek API Key，或切换到本地演示");
    return;
  }

  if (!state.current) {
    state.current = await api("/api/conversations", { method: "POST", body: {} });
    state.current.messages = state.current.messages || [];
  }
  const conversationId = state.current.id;
  const userMessage = { role: "user", content: text };
  state.current.messages.push(userMessage);
  elements.welcome.hidden = true;
  elements.messageList.appendChild(createUserMessage(userMessage));
  elements.input.value = "";
  resizeInput();

  const nodes = createAssistantMessage({}, true);
  elements.messageList.appendChild(nodes.element);
  const accumulated = { content: "", reasoning: "" };
  let renderQueued = false;
  let completed = false;
  let aborted = false;
  let previewNode = null;
  const streamRenderer = window.DSAResponseRenderer.createStreamingRenderer({
    renderStable(source) {
      previewNode?.remove();
      previewNode = null;
      nodes.markdown.insertAdjacentHTML("beforeend", renderMarkdown(source));
    },
    renderPreview(text, append) {
      if (!append && !text) {
        previewNode?.remove();
        previewNode = null;
        return;
      }
      if (!previewNode) {
        previewNode = document.createElement("div");
        previewNode.className = "streaming-preview";
        nodes.markdown.appendChild(previewNode);
      }
      if (append) previewNode.appendChild(document.createTextNode(text));
      else previewNode.textContent = text;
    },
    onFlush() {
      nodes.markdown.classList.toggle("typing-cursor", !accumulated.content);
      scrollBottom();
    },
  });
  const scheduleRender = () => {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(() => {
      renderQueued = false;
      updateReasoning(nodes, accumulated.reasoning, true);
      scrollBottom();
    });
  };

  state.requestId = globalThis.crypto?.randomUUID?.() || `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  state.controller = new AbortController();
  setGenerating(true);
  scrollBottom(true);

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: conversationId,
        content: text,
        request_id: state.requestId,
        options: { ...state.options },
      }),
      signal: state.controller.signal,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `请求失败（${response.status}）`);
    }
    if (!response.body) throw new Error("当前浏览器不支持流式响应");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || "";
      for (const block of blocks) {
        if (!block.trim() || block.startsWith(":")) continue;
        const event = parseSSEBlock(block);
        if (!event) continue;
        if (event.type === "delta") {
          accumulated.content += event.text || "";
          streamRenderer.append(event.text || "");
        } else if (event.type === "reasoning") {
          accumulated.reasoning += event.text || "";
          scheduleRender();
        } else if (event.type === "tool_start") {
          addToolEvent(nodes, event.name, "正在执行");
          scrollBottom();
        } else if (event.type === "tool_end") {
          const toolNodes = [...nodes.toolSlot.querySelectorAll(".tool-event")];
          const target = toolNodes.reverse().find((node) => node.dataset.toolName === event.name);
          if (target) target.querySelector("span").innerHTML = `<strong>${escapeHtml(event.name)}</strong> · 执行完成`;
        } else if (event.type === "server_tool_event") {
          const serverEvent = event.event || {};
          const call = serverEvent.call || {};
          const action = call.action || {};
          const name = action.type === "web_search" ? "联网搜索" : "网页阅读";
          if (serverEvent.type === "call_started") {
            addToolEvent(nodes, name, "DeepSeek 服务端正在执行");
            scrollBottom();
          } else if (serverEvent.type === "call_completed") {
            const toolNodes = [...nodes.toolSlot.querySelectorAll(".tool-event")];
            const target = toolNodes.reverse().find((node) => node.dataset.toolName === name);
            const status = call.status === "success" ? "已完成" : "未完成";
            if (target) target.querySelector("span").innerHTML = `<strong>${escapeHtml(name)}</strong> · ${status}`;
          }
        } else if (event.type === "done") {
          completed = true;
          if (event.cancelled) aborted = true;
        } else if (event.type === "error") {
          throw new Error(event.error || "生成失败");
        }
      }
    }
    streamRenderer.finish();
    updateReasoning(nodes, accumulated.reasoning, false);
    nodes.markdown.classList.remove("typing-cursor");
    nodes.actions.hidden = !accumulated.content;
    if (!completed && !aborted) throw new Error("流式连接在回复完成前中断");
  } catch (error) {
    streamRenderer.finish();
    aborted = aborted || error.name === "AbortError";
    if (!aborted) {
      nodes.markdown.classList.remove("typing-cursor");
      nodes.markdown.innerHTML = `<p><strong>生成失败：</strong>${escapeHtml(error.message)}</p>`;
      updateReasoning(nodes, accumulated.reasoning, false);
      toast(error.message);
    }
  } finally {
    setGenerating(false);
    state.controller = null;
    state.requestId = null;
    if (completed) {
      try {
        const saved = await api(`/api/conversations/${conversationId}`);
        if (state.current?.id === conversationId) {
          state.current = saved;
          elements.conversationTitle.textContent = saved.title;
          renderMessages(saved.messages);
          scrollBottom(true);
        }
      } catch (error) {
        toast(error.message);
      }
    } else if (aborted) {
      updateReasoning(nodes, accumulated.reasoning, false);
      nodes.markdown.classList.remove("typing-cursor");
      nodes.actions.hidden = !accumulated.content;
      toast("已停止生成");
    }
    await refreshConversations().catch(() => {});
  }
}

async function stopGeneration() {
  if (!state.streaming) return;
  const requestId = state.requestId;
  try {
    if (requestId) await api("/api/chat/cancel", { method: "POST", body: { request_id: requestId } });
  } catch {
    // 网络中断时仍中止浏览器读取，服务端会在连接断开后回收请求。
  }
  state.controller?.abort();
}

function syncConfigForm() {
  if (!state.config) return;
  const providers = state.config.providers?.length ? state.config.providers : ["deepseek", "demo"];
  const labels = { deepseek: "DeepSeek API", demo: "本地演示（无需 Key）" };
  elements.provider.innerHTML = providers.map((id) =>
    `<option value="${escapeHtml(id)}">${escapeHtml(labels[id] || id)}</option>`).join("");
  elements.provider.value = state.config.provider;
  for (const option of elements.provider.options) option.disabled = !pluginEnabled(option.value);
  elements.model.value = state.config.model;
  elements.baseUrl.value = state.config.base_url;
  elements.apiKey.value = "";
  elements.apiKey.placeholder = state.config.api_key_from_file
    ? "已由项目目录 Key 文件提供"
    : (state.config.api_key_set ? "已保存；留空则不修改" : "输入 DeepSeek API Key");
  elements.apiKeyHint.textContent = state.config.api_key_from_env
    ? "当前使用环境变量中的 Key"
    : (state.config.api_key_from_file
      ? `优先读取文件：${state.config.api_key_file}（文件为明文）`
      : (state.config.api_key_set ? "已保存于本机设置文件，不会回显" : "Key 可放在项目根目录 001.key"));
  elements.clearKey.disabled = state.config.api_key_from_env || state.config.api_key_from_file;
  elements.clearKey.textContent = state.config.api_key_from_file ? "Key 文件优先" : "清除已存 Key";
  state.clearKey = false;
  elements.effort.value = state.config.reasoning_effort;
  elements.systemPrompt.value = state.config.system_prompt_from_file ? "" : state.config.system_prompt;
  elements.systemPrompt.readOnly = Boolean(state.config.system_prompt_from_file);
  elements.systemPromptHint.textContent = state.config.system_prompt_from_file
    ? `当前使用文件：${state.config.system_prompt_file}；修改此文件后重启服务即可更新`
    : "未找到提示词文件，当前使用本机设置中的提示词";
  let theme = "auto";
  try { theme = localStorage.getItem("deepseek-termux-theme") || "auto"; } catch { /* 本地存储不可用时使用自动主题 */ }
  elements.theme.value = theme;
}

function openSettings() {
  syncConfigForm();
  closeSidebar();
  if (!elements.settingsDialog.open) elements.settingsDialog.showModal();
}

function renderPlugins() {
  if (!state.plugins.length) {
    elements.pluginList.innerHTML = '<div class="list-placeholder">未发现插件</div>';
    return;
  }
  elements.pluginList.innerHTML = state.plugins.map((plugin) => `
    <div class="plugin-item" data-plugin-id="${escapeHtml(plugin.id)}">
      <div class="plugin-info">
        <div class="plugin-name-row"><span class="plugin-name">${escapeHtml(plugin.name)}</span><span class="plugin-version">v${escapeHtml(plugin.version)}</span>${plugin.core ? '<span class="core-badge">核心</span>' : ""}</div>
        <p class="plugin-description">${escapeHtml(plugin.description)}</p>
        ${plugin.error ? `<p class="plugin-error">${escapeHtml(plugin.error)}</p>` : ""}
      </div>
      <button type="button" class="switch" role="switch" aria-checked="${plugin.enabled}" aria-label="${plugin.enabled ? "停用" : "启用"}${escapeHtml(plugin.name)}" ${plugin.core ? 'disabled title="核心服务运行期间保持启用"' : ""}></button>
    </div>`).join("");
  syncPluginControls();
}

async function togglePlugin(button) {
  const item = button.closest(".plugin-item");
  const plugin = state.plugins.find((entry) => entry.id === item.dataset.pluginId);
  if (!plugin || plugin.core) return;
  const enabled = button.getAttribute("aria-checked") !== "true";
  if (!enabled && state.config.provider === plugin.id) {
    toast("请先在设置中切换到另一个模型提供方");
    return;
  }
  button.disabled = true;
  try {
    const data = await api(`/api/plugins/${encodeURIComponent(plugin.id)}`, { method: "POST", body: { enabled } });
    state.plugins = data.plugins;
    state.config = { ...state.config, ...(await api("/api/config")) };
    renderPlugins();
    toast(`${plugin.name}已${enabled ? "启用" : "停用"}`);
  } catch (error) {
    button.disabled = false;
    toast(error.message);
  }
}

function renderRuntime() {
  const dsa = state.dsa;
  elements.prepareCandidate.disabled = !dsa || state.dsaBusy;
  elements.activateCandidate.disabled = true;
  elements.rollbackRuntime.disabled = true;
  if (!dsa) {
    elements.versionList.innerHTML = '<div class="list-placeholder">无法读取主体状态</div>';
    return;
  }
  const active = dsa.slots[dsa.active_slot] || {};
  const activeBusy = Number(active.active_requests || 0) > 0;
  const candidate = dsa.candidate || { status: "empty" };
  elements.runtimeBadge.textContent = dsa.active_version_id || "—";
  elements.activeVersion.textContent = dsa.active_version_id || "—";
  elements.activeSlot.textContent = `槽位 ${dsa.active_slot || "—"}`;
  elements.activeRuntimeDetail.textContent = active.runtime_version
    ? `运行时 v${active.runtime_version} · ${activeBusy ? `${active.active_requests} 个请求正在生成` : "当前没有生成中的回复"}`
    : "活动主体状态未知";

  const messages = [];
  let failed = false;
  if (state.dsaBusy) {
    const busyText = {
      candidate: "正在冻结实验源码，并在备用槽使用隔离数据检查…",
      activate: "正在用正式数据复核候选并执行原子切换…",
      rollback: "正在检查回滚目标并恢复上一版本…",
    };
    messages.push(busyText[state.dsaAction] || "正在处理主体操作…");
  } else if (candidate.status === "ready") {
    messages.push(`${candidate.version_id} 已在槽位 ${candidate.slot} 通过隔离检查，可以激活。`);
  } else if (candidate.status === "failed") {
    failed = true;
    messages.push(`${candidate.version_id} 检查失败，当前生产主体未受影响。${candidate.error || ""}`);
  } else if (candidate.status === "checking") {
    messages.push(`${candidate.version_id} 正在进行隔离检查…`);
  } else {
    messages.push("修改实验源码后，先准备候选；检查通过后再激活。");
  }
  if (activeBusy) messages.push("当前有回复正在生成，完成排空后才能激活或回滚。");
  elements.candidateStatus.classList.toggle("error", failed);
  elements.candidateStatus.textContent = messages.join(" ");
  elements.activateCandidate.disabled = state.dsaBusy || candidate.status !== "ready" || activeBusy;
  elements.rollbackRuntime.disabled = state.dsaBusy || !dsa.previous_version_id || activeBusy;

  if (!dsa.versions.length) {
    elements.versionList.innerHTML = '<div class="list-placeholder">还没有版本快照</div>';
    return;
  }
  elements.versionList.innerHTML = dsa.versions.map((version) => `
    <div class="version-item">
      <div class="version-heading">
        <strong>${escapeHtml(version.id)}</strong>
        ${version.active ? '<span class="status-badge current">当前</span>' : ""}
        ${version.rollback_target ? '<span class="status-badge">回滚目标</span>' : ""}
      </div>
      <div class="version-meta">父版本 ${escapeHtml(version.parent_id || "无")} · ${escapeHtml(formatDate(version.created_at))}</div>
      <code class="version-hash" title="${escapeHtml(version.source_hash)}">${escapeHtml(version.source_hash.slice(0, 12))}</code>
    </div>`).join("");
}

async function loadRuntimeStatus() {
  if (state.dsaBusy) return;
  state.dsa = await api("/api/dsa/status");
  renderRuntime();
}

async function runRuntimeAction(action) {
  if (state.dsaBusy) return;
  state.dsaBusy = true;
  state.dsaAction = action;
  renderRuntime();
  let switching = false;
  try {
    const result = await api(`/api/dsa/${action}`, { method: "POST", body: {} });
    state.dsa = result;
    if (action === "candidate") {
      toast(`${result.active_version_id} 保持运行，${result.candidate.version_id} 已通过隔离检查`);
    } else {
      switching = true;
      toast(action === "activate" ? `已激活 ${result.active_version_id}，正在刷新` : `已回滚至 ${result.active_version_id}，正在刷新`);
      window.setTimeout(() => window.location.reload(), 900);
    }
  } catch (error) {
    await loadRuntimeStatusWithoutLock().catch(() => {});
    toast(error.message || String(error));
  } finally {
    state.dsaBusy = false;
    state.dsaAction = "";
    if (!switching) renderRuntime();
  }
}

async function loadRuntimeStatusWithoutLock() {
  state.dsa = await api("/api/dsa/status");
  renderRuntime();
}

function guarded(handler) {
  return async (event) => {
    try { await handler(event); } catch (error) { toast(error.message || String(error)); }
  };
}

function bindEvents() {
  $("#menuButton").addEventListener("click", openSidebar);
  $("#closeSidebar").addEventListener("click", closeSidebar);
  elements.backdrop.addEventListener("click", closeSidebar);
  $("#newChatSide").addEventListener("click", newChat);
  $("#newChatTop").addEventListener("click", newChat);
  $("#openSettings").addEventListener("click", openSettings);
  $("#openRuntime").addEventListener("click", guarded(async () => {
    closeSidebar();
    if (!elements.runtimeDialog.open) elements.runtimeDialog.showModal();
    await loadRuntimeStatus();
  }));
  $("#openPlugins").addEventListener("click", () => {
    renderPlugins();
    closeSidebar();
    if (!elements.pluginsDialog.open) elements.pluginsDialog.showModal();
  });
  elements.composer.addEventListener("submit", guarded(async (event) => {
    event.preventDefault();
    await sendMessage();
  }));
  elements.stop.addEventListener("click", guarded(stopGeneration));
  elements.input.addEventListener("input", resizeInput);
  elements.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!state.streaming && elements.input.value.trim()) sendMessage().catch((error) => toast(error.message));
    }
  });
  elements.thinking.addEventListener("click", () => {
    state.options.thinking = !state.options.thinking;
    syncOptions();
  });
  elements.search.addEventListener("click", () => {
    state.options.web_search = !state.options.web_search;
    syncOptions();
  });
  elements.read.addEventListener("click", () => {
    state.options.read_url = !state.options.read_url;
    syncOptions();
  });
  elements.conversationList.addEventListener("click", guarded(async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const item = button.closest(".conversation-item");
    const conversation = state.conversations.find((entry) => entry.id === item.dataset.id);
    if (!conversation) return;
    if (button.dataset.action === "select") await loadConversation(conversation.id);
    if (button.dataset.action === "rename") await renameConversation(conversation);
    if (button.dataset.action === "delete") await deleteConversation(conversation);
  }));
  elements.clearKey.addEventListener("click", () => {
    state.clearKey = true;
    elements.apiKey.value = "";
    elements.apiKey.placeholder = "保存时将清除本机文件中的 Key";
    elements.clearKey.textContent = "已标记清除";
  });
  elements.settingsForm.addEventListener("submit", guarded(async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    submit.disabled = true;
    try {
      const updates = {
        provider: elements.provider.value,
        model: elements.model.value,
        base_url: elements.baseUrl.value,
        reasoning_effort: elements.effort.value,
        system_prompt: elements.systemPrompt.value,
      };
      if (state.clearKey) updates.api_key = null;
      else if (elements.apiKey.value.trim()) updates.api_key = elements.apiKey.value.trim();
      state.config = await api("/api/config", { method: "PUT", body: updates });
      const theme = elements.theme.value;
      try { localStorage.setItem("deepseek-termux-theme", theme); } catch { /* 忽略禁用存储的浏览器 */ }
      applyTheme(theme);
      syncRuntimeStatus();
      syncPluginControls();
      elements.settingsDialog.close();
      toast("设置已保存");
    } finally {
      submit.disabled = false;
    }
  }));
  elements.pluginList.addEventListener("click", (event) => {
    const button = event.target.closest(".switch");
    if (button) togglePlugin(button);
  });
  elements.prepareCandidate.addEventListener("click", guarded(() => runRuntimeAction("candidate")));
  elements.activateCandidate.addEventListener("click", guarded(() => runRuntimeAction("activate")));
  elements.rollbackRuntime.addEventListener("click", guarded(() => runRuntimeAction("rollback")));
  document.querySelectorAll("[data-close]").forEach((button) => {
    button.addEventListener("click", () => document.getElementById(button.dataset.close).close());
  });
  for (const dialog of [elements.settingsDialog, elements.pluginsDialog, elements.runtimeDialog]) {
    dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
  }
  document.addEventListener("click", guarded(async (event) => {
    const codeButton = event.target.closest(".code-copy");
    if (codeButton) {
      await copyText(codeButton.closest(".code-block").querySelector("code").textContent, codeButton);
      return;
    }
    const messageButton = event.target.closest(".copy-message");
    if (messageButton) {
      await copyText(messageButton.closest(".assistant-content").querySelector(".markdown").innerText, messageButton);
    }
  }));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSidebar();
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (elements.theme.value === "auto") applyTheme("auto");
  });
}

async function init() {
  bindEvents();
  let theme = "auto";
  try { theme = localStorage.getItem("deepseek-termux-theme") || "auto"; } catch { /* 使用默认主题 */ }
  applyTheme(theme);
  resizeInput();
  try {
    const [config, conversations, plugins, dsa] = await Promise.all([
      api("/api/config"), api("/api/conversations"), api("/api/plugins"), api("/api/dsa/status"),
    ]);
    state.config = config;
    state.conversations = conversations.conversations;
    state.plugins = plugins.plugins;
    state.dsa = dsa;
    state.options.thinking = config.thinking;
    state.options.web_search = config.web_search;
    state.options.read_url = config.read_url;
    syncRuntimeStatus();
    syncOptions();
    renderConversations();
    renderPlugins();
    renderRuntime();
    if (state.conversations.length) await loadConversation(state.conversations[0].id);
  } catch (error) {
    elements.runtimeStatus.textContent = "本地服务连接失败";
    elements.welcomeHint.textContent = error.message;
    toast(error.message);
  }
  window.setInterval(() => {
    if (elements.runtimeDialog.open && !state.dsaBusy) loadRuntimeStatus().catch(() => {});
  }, 2000);
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}

init();
