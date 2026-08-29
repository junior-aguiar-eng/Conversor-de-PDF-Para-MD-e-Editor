const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { JSDOM } = require("jsdom");
const { marked } = require("marked");
const createDOMPurify = require("dompurify");

function extractFunction(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `função ausente: ${name}`);
  const open = source.indexOf("{", start);
  let depth = 0;
  let quote = null;
  let escaped = false;
  for (let index = open; index < source.length; index += 1) {
    const char = source[index];
    if (quote) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === quote) quote = null;
      continue;
    }
    if (["'", '"', "`"].includes(char)) {
      quote = char;
      continue;
    }
    if (char === "{") depth += 1;
    if (char === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, index + 1);
    }
  }
  throw new Error(`função incompleta: ${name}`);
}

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "web", "app.js"), "utf8");
const dom = new JSDOM("<!doctype html><body></body>", { url: "file:///web/index.html" });
dom.window.marked = marked;
dom.window.DOMPurify = createDOMPurify(dom.window);

const context = vm.createContext({
  window: dom.window,
  URL,
  console,
  markdownSanitizerConfigured: false,
});
for (const name of ["isSafeHttpUrl", "isSafeRelativeAssetRef", "configureMarkdownSanitizer", "renderMarkdownToHtml"]) {
  vm.runInContext(extractFunction(source, name), context);
}

const legitimate = vm.runInContext(`renderMarkdownToHtml(${JSON.stringify(`# Título

- item
- outro

1. primeiro
2. segundo

| A | B |
|---|---|
| 1 | 2 |

> citação

\`código\` e **negrito** com *itálico*.

\`\`\`js
const seguro = true;
\`\`\`

![figura](assets/página%201.png)

[fonte](https://example.com/lei?q=1)`)});`, context);
const legitimateDom = new JSDOM(legitimate).window.document;
assert.equal(legitimateDom.querySelector("h1")?.textContent, "Título");
assert.equal(legitimateDom.querySelectorAll("ul li").length, 2);
assert.equal(legitimateDom.querySelectorAll("ol li").length, 2);
assert.equal(legitimateDom.querySelector("table td")?.textContent, "1");
assert.equal(legitimateDom.querySelector("blockquote")?.textContent.trim(), "citação");
assert.ok(legitimateDom.querySelector("pre code"));
assert.ok(legitimateDom.querySelector("strong"));
assert.ok(legitimateDom.querySelector("em"));
assert.equal(legitimateDom.querySelector("img")?.getAttribute("src"), "assets/p%C3%A1gina%201.png");
assert.equal(legitimateDom.querySelector("a")?.getAttribute("href"), "https://example.com/lei?q=1");
assert.equal(legitimateDom.querySelector("a")?.getAttribute("target"), "_blank");

const malicious = vm.runInContext(`renderMarkdownToHtml(${JSON.stringify(`<script>window.PWNED=1</script>
<svg onload="window.PWNED=2"><script>window.PWNED=3</script></svg>
<iframe srcdoc="<script>window.PWNED=4</script>"></iframe>
<p onclick="window.PWNED=5" style="background:url(javascript:alert(1))" data-x="x">texto</p>
[js](javascript:alert(1))
[credencial](https://user:pass@example.com/)
![js](javascript:alert(2))
![absoluta](C:/segredo.png)
![unc](\\\\servidor\\foto.png)
![travessia](../segredo.png)
![remota](https://example.com/foto.png)
![injeção](imagem.png" onerror="window.PWNED=6)`)});`, context);
const maliciousDom = new JSDOM(malicious).window.document;
for (const selector of ["script", "svg", "iframe", "object", "embed", "form", "style"]) {
  assert.equal(maliciousDom.querySelector(selector), null, `${selector} sobreviveu`);
}
assert.equal(maliciousDom.querySelector("[onclick], [onload], [onerror], [style], [data-x]"), null);
assert.equal(maliciousDom.querySelector("a[href]"), null);
assert.equal(maliciousDom.querySelector("img[src]"), null);
assert.equal([...maliciousDom.querySelectorAll("a, img")].some((node) => /javascript:/i.test(node.getAttribute("href") || node.getAttribute("src") || "")), false);

process.stdout.write("phase2_renderer_ok\n");
