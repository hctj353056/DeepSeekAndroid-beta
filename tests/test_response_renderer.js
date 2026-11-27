"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const renderer = require("../web/response-renderer.js");


test("renders headings, emphasis, lists, quotes and horizontal rules", () => {
  const html = renderer.renderMarkdown(
    "# 标题\n\n**粗体** *斜体* ***粗斜体***\n\n- 第一项\n- 第二项\n\n> 引用\n\n---\n\n1. 有序项",
  );
  assert.match(html, /<h1>标题<\/h1>/);
  assert.match(html, /<strong>粗体<\/strong>/);
  assert.match(html, /<em>斜体<\/em>/);
  assert.match(html, /<strong><em>粗斜体<\/em><\/strong>/);
  assert.match(html, /<ul><li>第一项<\/li><li>第二项<\/li><\/ul>/);
  assert.match(html, /<blockquote><p>引用<\/p><\/blockquote>/);
  assert.match(html, /<hr>/);
  assert.match(html, /<ol><li>有序项<\/li><\/ol>/);
});


test("escapes code fences and preserves language and copy action", () => {
  const html = renderer.renderMarkdown("```html\n<script>alert(1)</script>\n```");
  assert.match(html, /<span>html<\/span>/);
  assert.match(html, /class="code-copy"/);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>alert/);
});


test("inline code is not rewritten by emphasis or links", () => {
  const html = renderer.inlineMarkdown("`**粗体** [链接](https://example.com)`");
  assert.match(html, /^<code>\*\*粗体\*\* \[链接\]\(https:\/\/example.com\)<\/code>$/);
});


test("only http and https links are allowed with safe rel", () => {
  const safe = renderer.inlineMarkdown("[示例](https://example.com/?a=1&b=2)");
  assert.match(safe, /<a href="https:\/\/example.com\/\?a=1&amp;b=2" target="_blank" rel="noopener noreferrer nofollow">示例<\/a>/);
  for (const target of ["javascript:alert(1)", "data:text/html,<b>x</b>", "file:///etc/passwd"]) {
    const html = renderer.inlineMarkdown(`[危险](${target})`);
    assert.doesNotMatch(html, /<a\s/);
    assert.doesNotMatch(html, /rel="noopener/);
  }
});


test("raw HTML and event attributes remain inert text", () => {
  const html = renderer.renderMarkdown("<img src=x onerror=alert(1)>\n\n<script>alert(2)</script>");
  assert.doesNotMatch(html, /<img\b/);
  assert.doesNotMatch(html, /<script\b/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html, /&lt;script&gt;alert\(2\)&lt;\/script&gt;/);
});


test("renders GFM tables with alignment, escaped pipes and code pipes", () => {
  const html = renderer.renderMarkdown(
    "| 名称 | 数值 | 备注 |\n| :--- | ---: | :---: |\n| A\\|B | 42 | `x|y` |\n| 长文本 | **加粗** | 说明 |",
  );
  assert.match(html, /class="table-scroll"/);
  assert.match(html, /<th class="align-left">名称<\/th>/);
  assert.match(html, /<th class="align-right">数值<\/th>/);
  assert.match(html, /<th class="align-center">备注<\/th>/);
  assert.match(html, /A\|B/);
  assert.match(html, /<code>x\|y<\/code>/);
  assert.match(html, /<strong>加粗<\/strong>/);
  assert.equal((html.match(/<tr>/g) || []).length, 3);
});


test("invalid table delimiter falls back to paragraphs", () => {
  const html = renderer.renderMarkdown("A | B\n-- | --");
  assert.doesNotMatch(html, /<table>/);
  assert.match(html, /<p>A \| B<br>-- \| --<\/p>/);
});


test("unclosed fence is safely rendered as an accumulating code block", () => {
  const html = renderer.renderMarkdown("```js\nconst x = 1;");
  assert.match(html, /<span>js<\/span>/);
  assert.match(html, /const x = 1;/);
  assert.doesNotMatch(html, /<p>const x/);
});


test("ContentBlock extension point supports markdown, text and code", () => {
  const html = renderer.renderContent([
    { type: "markdown", text: "## 小节" },
    { type: "text", text: "第一行\n第二行" },
    { type: "code", language: "python", code: "print('<x>')" },
  ]);
  assert.match(html, /<h2>小节<\/h2>/);
  assert.match(html, /第一行<br>第二行/);
  assert.match(html, /print\(&#39;&lt;x&gt;&#39;\)/);
  assert.throws(() => renderer.renderContent({ type: "chart", data: [] }), /暂不支持/);
});


test("escaped punctuation and private token text cannot collide with renderer internals", () => {
  const html = renderer.inlineMarkdown("\\*不是斜体\\* \uE0000\uE001");
  assert.doesNotMatch(html, /<em>/);
  assert.match(html, /\*不是斜体\*/);
  assert.doesNotMatch(html, /undefined/);
});
