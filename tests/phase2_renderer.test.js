const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { JSDOM } = require("jsdom");
const { marked } = require("marked");
const createDOMPurify = require("dompurify");

function extractFunction(source, name) {
  const functionStart = source.indexOf(`function ${name}(`);
  assert.notEqual(functionStart, -1, `função ausente: ${name}`);
  const start = source.slice(Math.max(0, functionStart - 6), functionStart) === "async "
    ? functionStart - 6
    : functionStart;
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
const htmlSource = fs.readFileSync(path.join(root, "web", "index.html"), "utf8");
assert.equal((source.match(/async function togglePause\(/g) || []).length, 1);
assert.equal((source.match(/async function requestStop\(/g) || []).length, 1);
assert.equal((source.match(/function updateControlsState\(/g) || []).length, 1);
assert.equal((source.match(/function startTimer\(/g) || []).length, 1);
const pointerMoveStart = source.indexOf("  onPointerMove(e) {");
const pointerMoveEnd = source.indexOf("  async onPointerUp(e) {", pointerMoveStart);
const pointerMoveSource = source.slice(pointerMoveStart, pointerMoveEnd);
assert.match(pointerMoveSource, /pdfTransientCanvas/);
assert.doesNotMatch(pointerMoveSource, /redrawAnnotations/);
assert.match(source, /IntersectionObserver/);
assert.doesNotMatch(source, /pageData\.image_base64/);
const thumbnailStart = source.indexOf("  updateActiveThumbnail() {");
const thumbnailEnd = source.indexOf("  async renderCurrentPage", thumbnailStart);
assert.doesNotMatch(source.slice(thumbnailStart, thumbnailEnd), /scrollIntoView/);
assert.match(source, /card-drag-handle/);
assert.match(source, /e\.key === "Enter" && !e\.shiftKey/);
assert.match(source, /eraseAnnotationAt\(x, y\)/);
assert.match(source, /pendingRotations/);
assert.match(source, /restore_pdf_backup/);
assert.ok(htmlSource.indexOf("btnToolSaveCopy") < htmlSource.indexOf("btnToolApplyOriginal"));
assert.match(htmlSource, /id="tool-eraser"/);
const dom = new JSDOM("<!doctype html><body></body>", { url: "file:///web/index.html" });
dom.window.marked = marked;
dom.window.DOMPurify = createDOMPurify(dom.window);

const context = vm.createContext({
  window: dom.window,
  URL,
  console,
  markdownSanitizerConfigured: false,
});
for (const name of ["isSafeHttpUrl", "isSafeRelativeAssetRef", "configureMarkdownSanitizer", "renderMarkdownToHtml", "hydrateMarkdownAssets"]) {
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

async function verifyLazyHydration() {
  const container = dom.window.document.createElement("div");
  container.innerHTML = '<img src="assets/um.png"><img src="assets/dois.png">';
  const observed = [];
  let intersectionCallback = null;
  let assetReads = 0;
  dom.window.IntersectionObserver = class {
    constructor(callback) { intersectionCallback = callback; }
    observe(image) { observed.push(image); }
    unobserve() {}
  };
  dom.window.pywebview = {
    api: {
      read_markdown_asset: async (_markdownId, relativeRef) => {
        assetReads += 1;
        return { ok: true, data_uri: `data:image/png;base64,${relativeRef}` };
      },
    },
  };
  context.lazyContainer = container;
  await vm.runInContext('hydrateMarkdownAssets(lazyContainer, "md-test")', context);
  assert.equal(assetReads, 0);
  assert.equal(observed.length, 2);
  intersectionCallback([{ isIntersecting: true, target: observed[0] }]);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(assetReads, 1);
  assert.match(observed[0].src, /^data:image\/png;base64,/);
}

verifyLazyHydration()
  .then(() => process.stdout.write("phase2_renderer_ok\n"))
  .catch((error) => { console.error(error); process.exit(1); });
