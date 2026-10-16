"use strict";
// Auditoria local dos formatos publicados; não carrega recursos externos.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { JSDOM, VirtualConsole } = require("jsdom");

const directory = process.argv[2];
if (!directory) throw new Error("Uso: node scripts/verify-structured-exports.js <diretório>");
const reports = [];
for (const filename of fs.readdirSync(directory).filter((name) => name.endsWith(".md"))) {
  const base = path.join(directory, filename.slice(0, -3));
  const markdown = fs.readFileSync(`${base}.md`, "utf8");
  const data = JSON.parse(fs.readFileSync(`${base}.json`, "utf8"));
  const errors = [];
  const console = new VirtualConsole();
  console.on("jsdomError", (error) => errors.push(error.message));
  const dom = new JSDOM(fs.readFileSync(`${base}.html`, "utf8"), {
    runScripts: "dangerously", virtualConsole: console,
    url: "file:///audit/document.html",
  });
  const document = dom.window.document;
  const payload = JSON.parse(document.getElementById("document-data").textContent);
  assert.equal(payload.markdown, markdown, filename);
  delete payload.markdown;
  assert.deepEqual(payload, data, filename);
  assert.equal(data.markdown_sha256, createHash("sha256").update(markdown).digest("hex"));
  assert.deepEqual(errors, [], filename);
  const headings = [...document.querySelectorAll("#content h1,#content h2,#content h3,#content h4,#content h5,#content h6")];
  assert.equal(headings.length, data.sections.length, `${filename}: headings`);
  for (const section of data.sections) assert.ok(document.getElementById(section.id), `${filename}: ${section.id}`);
  const anchors = [...document.querySelectorAll("#toc a")];
  assert.equal(anchors.length, data.sections.length, filename);
  for (const anchor of anchors) {
    assert.ok(document.getElementById(anchor.hash.slice(1)), `${filename}: broken anchor`);
  }
  assert.ok(!document.querySelector('script[src],iframe,object,embed'));
  assert.ok(![...document.images].some((image) => /^https?:/i.test(image.getAttribute("src") || "")));
  reports.push({ file: filename, headings: headings.length, anchors: anchors.length, errors });
  dom.window.close();
}
assert.ok(reports.length, "Nenhum Markdown encontrado");
process.stdout.write(JSON.stringify(reports, null, 2) + "\n");
