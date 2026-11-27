(() => {
  "use strict";
  const dialog = document.getElementById("diagnosticsDialog");
  const openButton = document.getElementById("diagnosticsButton");
  const form = document.getElementById("diagnosticsForm");
  const exportButton = document.getElementById("diagnosticsExport");
  const status = document.getElementById("diagnosticsStatus");
  const toast = document.getElementById("toast");

  if (!dialog || !openButton || !form || !exportButton) return;

  async function sendEvent(event, message = "", level = "INFO") {
    try {
      await fetch("/api/diagnostics/client-event", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event, message: String(message).slice(0, 1200), level }),
      });
    } catch {
      // Diagnostics must never interrupt the chat UI.
    }
  }

  openButton.addEventListener("click", () => {
    dialog.showModal();
    sendEvent("panel.opened");
  });

  dialog.querySelectorAll("[data-close='diagnosticsDialog']").forEach((button) => {
    button.addEventListener("click", () => dialog.close());
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form).entries());
    status.textContent = "正在保存测试记录…";
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      const response = await fetch("/api/diagnostics/report", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || "保存失败");
      form.reset();
      status.textContent = "记录已保存。导出诊断包后即可发送给协助分析的人。";
      if (toast) {
        toast.textContent = "测试现象已记录";
        toast.classList.add("visible");
      }
    } catch (error) {
      status.textContent = error.message || "保存失败，请检查本地服务";
    } finally {
      submit.disabled = false;
    }
  });

  exportButton.addEventListener("click", async () => {
    exportButton.disabled = true;
    status.textContent = "正在生成诊断包…";
    try {
      const response = await fetch("/api/diagnostics/export", { cache: "no-store" });
      if (!response.ok) throw new Error("导出失败");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "dsa-diagnostics.zip";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      status.textContent = "诊断包已生成。发送前请确认其中不包含你不希望分享的内容。";
      sendEvent("export.completed");
    } catch (error) {
      status.textContent = error.message || "导出失败";
    } finally {
      exportButton.disabled = false;
    }
  });

  window.addEventListener("error", (event) => {
    sendEvent("javascript.error", `${event.message || "未知错误"} (${event.filename ? event.filename.split("/").pop() : "unknown"}:${event.lineno || 0})`, "ERROR");
  });

  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason && event.reason.message ? event.reason.message : String(event.reason || "未知拒绝");
    sendEvent("javascript.unhandled_rejection", reason, "ERROR");
  });

  sendEvent("client.environment", navigator.userAgent || "unknown");
})();
