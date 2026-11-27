"use strict";

(function exposeResponseRenderer(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.DSAResponseRenderer = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createResponseRenderer() {
  const COPY_ICON = '<svg aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>';
  const TOKEN_START = "\uE000";
  const TOKEN_END = "\uE001";

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[character]);
  }

  function safeHttpUrl(value) {
    const raw = String(value ?? "").trim();
    if (!raw || /[\u0000-\u001f\u007f]/.test(raw)) return "";
    try {
      const url = new URL(raw);
      return url.protocol === "http:" || url.protocol === "https:" ? url.href : "";
    } catch {
      return "";
    }
  }

  function inlineMarkdown(source) {
    const tokens = [];
    const stash = (html) => `${TOKEN_START}${tokens.push(html) - 1}${TOKEN_END}`;
    let text = String(source ?? "")
      .replace(/\uE000/g, "&#xE000;")
      .replace(/\uE001/g, "&#xE001;")
      .replace(/\\([\\`*_[\]|])/g, (_, character) => stash(escapeHtml(character)));

    // 先保护行内代码，后续强调与链接规则不得进入代码内容。
    let protectedText = "";
    let cursor = 0;
    while (cursor < text.length) {
      const opening = text.indexOf("`", cursor);
      if (opening < 0) {
        protectedText += text.slice(cursor);
        break;
      }
      const delimiter = text.slice(opening).match(/^`+/)[0];
      const closing = text.indexOf(delimiter, opening + delimiter.length);
      if (closing < 0) {
        protectedText += text.slice(cursor, opening + delimiter.length);
        cursor = opening + delimiter.length;
        continue;
      }
      protectedText += text.slice(cursor, opening);
      let code = text.slice(opening + delimiter.length, closing);
      if (code.length > 2 && code.startsWith(" ") && code.endsWith(" ") && code.trim()) code = code.slice(1, -1);
      protectedText += stash(`<code>${escapeHtml(code)}</code>`);
      cursor = closing + delimiter.length;
    }
    text = protectedText;

    text = text.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (match, label, target) => {
      const safe = safeHttpUrl(target);
      if (!safe) return match;
      return stash(`<a href="${escapeHtml(safe)}" target="_blank" rel="noopener noreferrer nofollow">${inlineMarkdown(label)}</a>`);
    });

    text = escapeHtml(text);
    text = text
      .replace(/\*\*\*([^*\n]+)\*\*\*/g, (_, content) => stash(`<strong><em>${content}</em></strong>`))
      .replace(/\*\*([^*\n]+)\*\*/g, (_, content) => stash(`<strong>${content}</strong>`))
      .replace(/__([^_\n]+)__/g, (_, content) => stash(`<strong>${content}</strong>`))
      .replace(/\*([^*\n]+)\*/g, (_, content) => stash(`<em>${content}</em>`))
      .replace(/(^|[^\w])_([^_\n]+)_(?![\w])/g, (_, prefix, content) => prefix + stash(`<em>${content}</em>`));

    return text.replace(/\uE000(\d+)\uE001/g, (_, index) => tokens[Number(index)]);
  }

  function renderCodeBlock(code, language = "") {
    const label = String(language || "code").trim().slice(0, 40) || "code";
    return `<div class="code-block"><div class="code-header"><span>${escapeHtml(label)}</span><button type="button" class="code-copy">${COPY_ICON}复制</button></div><pre><code>${escapeHtml(code)}</code></pre></div>`;
  }

  function splitTableRow(line) {
    const source = line.trim();
    const cells = [];
    let current = "";
    let codeDelimiter = 0;
    for (let index = 0; index < source.length; index += 1) {
      const character = source[index];
      if (character === "\\" && index + 1 < source.length) {
        current += character + source[index + 1];
        index += 1;
        continue;
      }
      if (character === "`") {
        let end = index + 1;
        while (source[end] === "`") end += 1;
        const count = end - index;
        if (codeDelimiter === 0) codeDelimiter = count;
        else if (codeDelimiter === count) codeDelimiter = 0;
        current += source.slice(index, end);
        index = end - 1;
        continue;
      }
      if (character === "|" && codeDelimiter === 0) {
        cells.push(current.trim());
        current = "";
      } else {
        current += character;
      }
    }
    cells.push(current.trim());
    if (source.startsWith("|")) cells.shift();
    if (source.endsWith("|")) cells.pop();
    return cells;
  }

  function tableAlignment(cell) {
    const left = cell.startsWith(":");
    const right = cell.endsWith(":");
    if (left && right) return "center";
    if (left) return "left";
    if (right) return "right";
    return "";
  }

  function isTable(lines, index) {
    if (index + 1 >= lines.length || !lines[index].includes("|")) return false;
    const header = splitTableRow(lines[index]);
    const delimiter = splitTableRow(lines[index + 1]);
    return header.length > 0 && header.length === delimiter.length
      && delimiter.every((cell) => /^:?-{3,}:?$/.test(cell));
  }

  function isBlockStart(lines, index) {
    const line = lines[index];
    return /^\s*```/.test(line)
      || /^#{1,6}\s+/.test(line)
      || /^\s*>\s?/.test(line)
      || /^\s*[-*+]\s+/.test(line)
      || /^\s*\d+[.)]\s+/.test(line)
      || /^\s*((-\s*){3,}|(\*\s*){3,}|(_\s*){3,})$/.test(line)
      || isTable(lines, index);
  }

  function renderTable(lines, start) {
    const header = splitTableRow(lines[start]);
    const delimiters = splitTableRow(lines[start + 1]);
    const aligns = delimiters.map(tableAlignment);
    const alignAttribute = (index) => aligns[index] ? ` class="align-${aligns[index]}"` : "";
    let html = '<div class="table-scroll" tabindex="0" role="region" aria-label="表格，可横向滚动"><table><thead><tr>';
    header.forEach((cell, index) => { html += `<th${alignAttribute(index)}>${inlineMarkdown(cell)}</th>`; });
    html += "</tr></thead><tbody>";
    let index = start + 2;
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines, index)) {
      const cells = splitTableRow(lines[index]);
      html += "<tr>";
      for (let column = 0; column < header.length; column += 1) {
        html += `<td${alignAttribute(column)}>${inlineMarkdown(cells[column] || "")}</td>`;
      }
      html += "</tr>";
      index += 1;
    }
    return { html: `${html}</tbody></table></div>`, next: index };
  }

  function renderMarkdown(source) {
    const lines = String(source ?? "").replace(/\r\n?/g, "\n").split("\n");
    const output = [];
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) { index += 1; continue; }

      const fence = line.match(/^\s*```([^`\n]*)\s*$/);
      if (fence) {
        const language = fence[1].trim();
        const code = [];
        index += 1;
        while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
          code.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) index += 1;
        output.push(renderCodeBlock(code.join("\n"), language));
        continue;
      }
      if (isTable(lines, index)) {
        const table = renderTable(lines, index);
        output.push(table.html);
        index = table.next;
        continue;
      }
      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      if (heading) {
        const level = heading[1].length;
        output.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
        index += 1;
        continue;
      }
      if (/^\s*((-\s*){3,}|(\*\s*){3,}|(_\s*){3,})$/.test(line)) {
        output.push("<hr>");
        index += 1;
        continue;
      }
      if (/^\s*>\s?/.test(line)) {
        const quote = [];
        while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
          quote.push(lines[index].replace(/^\s*>\s?/, ""));
          index += 1;
        }
        output.push(`<blockquote><p>${quote.map(inlineMarkdown).join("<br>")}</p></blockquote>`);
        continue;
      }
      const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
      const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
      if (unordered || ordered) {
        const type = unordered ? "ul" : "ol";
        const pattern = unordered ? /^\s*[-*+]\s+(.+)$/ : /^\s*\d+[.)]\s+(.+)$/;
        const items = [];
        while (index < lines.length) {
          const item = lines[index].match(pattern);
          if (!item) break;
          items.push(`<li>${inlineMarkdown(item[1])}</li>`);
          index += 1;
        }
        output.push(`<${type}>${items.join("")}</${type}>`);
        continue;
      }

      const paragraph = [];
      while (index < lines.length && lines[index].trim() && !isBlockStart(lines, index)) {
        paragraph.push(lines[index]);
        index += 1;
      }
      if (paragraph.length) output.push(`<p>${paragraph.map(inlineMarkdown).join("<br>")}</p>`);
    }
    return output.join("");
  }

  function normalizeBlocks(input) {
    if (typeof input === "string" || input == null) return [{ type: "markdown", text: String(input ?? "") }];
    const source = Array.isArray(input) ? input : [input];
    return source.map((block) => {
      if (typeof block === "string") return { type: "markdown", text: block };
      if (!block || typeof block !== "object") throw new TypeError("ContentBlock 必须是对象或字符串");
      const type = block.type || "markdown";
      if (type === "markdown" || type === "text") return { type, text: String(block.text ?? "") };
      if (type === "code") return { type, code: String(block.code ?? block.text ?? ""), language: String(block.language || "code") };
      throw new TypeError(`暂不支持的内容块类型：${type}`);
    });
  }

  function renderContent(input) {
    return normalizeBlocks(input).map((block) => {
      if (block.type === "markdown") return renderMarkdown(block.text);
      if (block.type === "text") return `<p>${escapeHtml(block.text).replace(/\n/g, "<br>")}</p>`;
      return renderCodeBlock(block.code, block.language);
    }).join("");
  }

  return Object.freeze({
    escapeHtml,
    safeHttpUrl,
    inlineMarkdown,
    renderMarkdown,
    renderCodeBlock,
    normalizeBlocks,
    renderContent,
  });
});
