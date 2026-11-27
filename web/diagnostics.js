(() => {
  "use strict";
  const dialog = document.getElementById("diagnosticsDialog");
  const openButton = document.getElementById("diagnosticsButton");
  const startButton = document.getElementById("issueStart");
  const finishButton = document.getElementById("issueFinish");
  const exportButton = document.getElementById("diagnosticsExport");
  const noteInput = document.getElementById("issueNote");
  const status = document.getElementById("diagnosticsStatus");
  const count = document.getElementById("issueEventCount");
  const toast = document.getElementById("toast");
  if (!dialog || !openButton || !startButton || !finishButton || !exportButton) return;

  const MAX_EVENTS = 300;
  const WINDOW_MS = 3 * 60 * 1000;
  const events = [];
  let recording = false;
  let activeIssueId = "";

  function redact(text) {
    return String(text || "")
      .replace(/(api.?key|authorization|password|secret|token|cookie)\s*[:=]\s*[^\s,;]+/ig, "$1=[REDACTED]")
      .replace(/Bearer\s+[A-Za-z0-9._~+/-]+=*/ig, "Bearer [REDACTED]")
      .replace(/\bsk-[A-Za-z0-9_-]{12,}\b/g, "[REDACTED]");
  }

  function pushEvent(type, details = {}) {
    const now = Date.now();
    const item = { ts: new Date(now).toISOString(), type: String(type).slice(0, 80), details };
    events.push(item);
    while (events.length > MAX_EVENTS || (events.length && now - Date.parse(events[0].ts) > WINDOW_MS)) events.shift();
    if (recording && count) count.textContent = String(events.length);
  }

  function elementInfo(target) {
    const element = target instanceof Element ? target.closest("button, [role='button'], input, textarea, select, form, a") : null;
    if (!element) return {};
    return {
      tag: element.tagName.toLowerCase(),
      id: String(element.id || "").slice(0, 80),
      role: String(element.getAttribute("role") || "").slice(0, 40),
      label: String(element.getAttribute("aria-label") || element.getAttribute("title") || "").slice(0, 120),
      name: String(element.getAttribute("name") || "").slice(0, 80)
    };
  }

  async function postIncident(payload) {
    const response = await window.fetch("/api/diagnostics/incident", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "问题现场保存失败");
    return result;
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  openButton.addEventListener("click", () => {
    dialog.showModal();
    pushEvent("diagnostics.panel_opened");
    status.textContent = recording ? "正在记录问题现场。你可以继续复现，结束后保存。" : "点击「标记问题」后，系统会保存此前约 3 分钟的操作与程序事件。";
  });
  dialog.querySelectorAll("[data-close='diagnosticsDialog']").forEach(button => button.addEventListener("click", () => dialog.close()));

  startButton.addEventListener("click", () => {
    if (recording) return;
    recording = true;
    const cutoff = Date.now() - WINDOW_MS;
    while (events.length && Date.parse(events[0].ts) < cutoff) events.shift();
    pushEvent("issue.capture_started");
    startButton.hidden = true;
    finishButton.hidden = false;
    activeIssueId = "";
    status.textContent = "正在记录。请继续复现问题；记录只保存在本机。";
    if (count) count.textContent = String(events.length);
  });

  finishButton.addEventListener("click", async () => {
    finishButton.disabled = true;
    pushEvent("issue.capture_finished");
    status.textContent = "正在整理问题现场…";
    const note = redact(noteInput ? noteInput.value.trim() : "").slice(0, 2000);
    const payload = {
      title: note.slice(0, 80) || "用户标记的问题",
      note,
      app_version: document.querySelector('meta[name="app-version"]')?.content || "unknown",
      user_agent: navigator.userAgent.slice(0, 300),
      events: events.slice(-MAX_EVENTS)
    };
    try {
      const result = await postIncident(payload);
      activeIssueId = result.id || "";
      if (noteInput) noteInput.value = "";
      pushEvent("issue.saved", { issue_id: activeIssueId });
      recording = false;
      startButton.hidden = false;
      finishButton.hidden = true;
      status.textContent = `已保存 ${result.id}。现在可以导出问题报告并交给 AI 分析。`;
      if (toast) { toast.textContent = "问题现场已保存"; toast.classList.add("visible"); }
    } catch (error) {
      status.textContent = error.message || "保存失败，请检查本地服务";
    } finally {
      finishButton.disabled = false;
    }
  });

  exportButton.addEventListener("click", async () => {
    exportButton.disabled = true;
    status.textContent = activeIssueId ? "正在生成问题报告…" : "正在导出基础诊断日志…";
    try {
      const path = activeIssueId ? `/api/diagnostics/export?issue_id=${encodeURIComponent(activeIssueId)}` : "/api/diagnostics/export";
      const response = await window.fetch(path, { cache: "no-store" });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || "导出失败");
      }
      downloadBlob(await response.blob(), activeIssueId ? `${activeIssueId}.zip` : "dsa-diagnostics.zip");
      status.textContent = "报告已导出。发送前请检查其中的用户备注和环境信息。";
    } catch (error) {
      status.textContent = error.message || "导出失败";
    } finally {
      exportButton.disabled = false;
    }
  });

  document.addEventListener("click", event => pushEvent("ui.click", elementInfo(event.target)), true);
  document.addEventListener("submit", event => pushEvent("ui.form_submit", elementInfo(event.target)), true);
  document.addEventListener("input", event => {
    const target = event.target;
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) {
      pushEvent("ui.input", { tag: target.tagName.toLowerCase(), id: String(target.id || "").slice(0, 80), name: String(target.name || "").slice(0, 80), type: String(target.type || "").slice(0, 40) });
    }
  }, true);
  document.addEventListener("visibilitychange", () => pushEvent("page.visibility", { state: document.visibilityState }));

  const nativeFetch = window.fetch.bind(window);
  window.fetch = async function(input, init = {}) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    const method = String(init.method || (input && input.method) || "GET").toUpperCase();
    const pathname = (() => { try { return new URL(url, location.href).pathname; } catch { return "/"; } })();
    if (pathname.startsWith("/api/diagnostics/")) return nativeFetch(input, init);
    const started = performance.now();
    pushEvent("network.started", { method, path: pathname.slice(0, 160) });
    try {
      const response = await nativeFetch(input, init);
      pushEvent("network.completed", { method, path: pathname.slice(0, 160), status: response.status, headers_ms: Math.round(performance.now() - started) });
      return response;
    } catch (error) {
      pushEvent("network.failed", { method, path: pathname.slice(0, 160), error: redact(error && error.message).slice(0, 300), duration_ms: Math.round(performance.now() - started) });
      throw error;
    }
  };

  window.addEventListener("error", event => pushEvent("javascript.error", {
    message: redact(event.message).slice(0, 300),
    file: String(event.filename || "").split("/").pop().slice(0, 100),
    line: Number(event.lineno) || 0
  }));
  window.addEventListener("unhandledrejection", event => pushEvent("javascript.unhandled_rejection", {
    message: redact(event.reason && event.reason.message || "Unhandled rejection").slice(0, 300)
  }));
  pushEvent("client.started", { user_agent: navigator.userAgent.slice(0, 300) });
})();
