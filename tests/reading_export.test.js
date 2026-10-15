"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM, VirtualConsole } = require("jsdom");
const root = path.resolve(__dirname, "..", "web");
const template = fs.readFileSync(path.join(root, "reading-export.html"), "utf8");
const scripts = {
  __MARKED__: fs.readFileSync(path.join(root, "vendor/marked.umd.js"), "utf8"),
  __PURIFY__: fs.readFileSync(path.join(root, "vendor/purify.min.js"), "utf8"),
  __READER__: fs.readFileSync(path.join(root, "reading-export.js"), "utf8"),
  __LICENSES__: "test fixture",
};

function render(data) {
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (error) => errors.push(error.message));
  const replacements = { ...scripts, __PAYLOAD__: JSON.stringify(data).replaceAll("<", "\\u003c") };
  const html = template.replace(/__MARKED__|__PURIFY__|__READER__|__LICENSES__|__PAYLOAD__/g, (key) => replacements[key]);
  const dom = new JSDOM(html, { runScripts: "dangerously", url: "file:///exports/doc.html", virtualConsole });
  assert.deepEqual(errors, []);
  return dom;
}

const dom = render({
  source_file: "Apostila <teste>.pdf", markdown_file: "Apostila.md", heading_profile: "curso",
  sections: [{ id: "section-0001" }, { id: "section-0002" }],
  page_coverage: [{ page_number: 2, status: "failed", warning: "sem texto" }],
  editorial_issues: ["conferir nota"],
  markdown: `# Capítulo **principal**

## Detalhes

| A | B |
|---|---|
| palavra<br>continuação | **negrito** |

> [!NOTE]
> Atenção à leitura.

![local](images/doc/figura.png)
![remota](https://example.com/track.png)
![fora](images/%2e%2e/segredo.png)
<img src="file:///C:/secret.png" onerror="window.INJECTED=true">
<script>window.INJECTED=true</script>
<a href="javascript:window.INJECTED=true">inseguro</a>
<a href="file:///C:/secret.txt">local externo</a>
<a href="https://example.com/fonte">fonte</a>
<iframe src="https://example.com"></iframe>
<style>body{display:none}</style>
<h3 id="document-data">HTML extraído</h3>
`,
});
const document = dom.window.document;
assert.equal(document.querySelectorAll("#toc a").length, 3);
assert.equal(document.querySelector("#toc a").textContent, "Capítulo principal");
for (const anchor of document.querySelectorAll("#toc a")) {
  assert.ok(document.getElementById(anchor.hash.slice(1)));
}
assert.equal(document.querySelectorAll("table").length, 1);
assert.equal(document.querySelector("td strong").textContent, "negrito");
assert.ok(!document.querySelector("blockquote").textContent.includes("[!NOTE]"));
assert.equal(document.querySelector('img[alt="local"]').getAttribute("src"), "images/doc/figura.png");
for (const img of document.querySelectorAll('img:not([alt="local"])')) assert.equal(img.getAttribute("src"), null);
assert.equal(dom.window.INJECTED, undefined);
assert.equal(document.querySelectorAll("#content script,#content style,#content iframe,#content [onerror]").length, 0);
assert.equal(document.querySelectorAll('#content a[href]').length, 1);
assert.equal(document.querySelectorAll("#document-data").length, 1);
assert.equal(document.getElementById("document-title").textContent, "Apostila <teste>.pdf");
assert.equal(document.getElementById("document-warning").hidden, false);
assert.match(document.getElementById("document-warning").textContent, /Página 2: sem texto/);
assert.match(document.getElementById("document-warning").textContent, /conferir nota/);
let printed = false;
dom.window.print = () => { printed = true; };
document.getElementById("print").click();
assert.equal(printed, true);
dom.window.close();

const empty = render({ source_file: "simples.pdf", markdown_file: "simples.md", markdown: "texto", sections: [] });
assert.match(empty.window.document.getElementById("toc").textContent, /não possui títulos/);
assert.equal(empty.window.document.getElementById("document-warning").hidden, true);
empty.window.close();
console.log("READING_EXPORT_OK: offline rendering, navigation, tables, warnings, print and sanitization");
