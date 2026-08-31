"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const html = fs.readFileSync(path.resolve(__dirname, "../admin_web/index.html"), "utf8");
const script = fs.readFileSync(path.resolve(__dirname, "../admin_web/app.js"), "utf8");

test("tela inicial é search-first e não carrega tabela geral", () => {
  assert.match(html, /id="searchInput"[^>]+type="search"/);
  assert.match(html, /Atividades recentes/);
  assert.match(html, /Aguardando conexão/);
  assert.doesNotMatch(html, /<table\b/i);
  assert.match(script, /if \(!normalized\)[\s\S]+resultsSection[\s\S]+classList\.add\("hidden"\)/);
});

test("filtros rápidos e paginação estão disponíveis", () => {
  for (const filter of ["status:expirada", "status:revogada", "expira:7d", "offline:7d", "status:aguardando"]) {
    assert.ok(html.includes(`data-filter="${filter}"`));
  }
  assert.match(script, /data-page=/);
  assert.match(script, /page_size/);
});

test("operações críticas exigem frases vinculadas à licença", () => {
  assert.match(script, /REVOGAR:\$\{licenseId\}/);
  assert.match(script, /TROCAR:\$\{licenseId\}/);
  assert.match(script, /bridge\("revoke_license", licenseId, reason, confirmation\)/);
  assert.match(script, /bridge\("replace_device", licenseId, machine, reason, confirmation\)/);
});

test("interface é local, acessível e sem handlers inline", () => {
  assert.match(html, /Content-Security-Policy/);
  assert.match(html, /role="search"/);
  assert.match(html, /aria-modal="true"/);
  assert.match(html, /aria-live="polite"/);
  assert.doesNotMatch(html, /\son[a-z]+\s*=/i);
  assert.doesNotMatch(html, /https?:\/\//i);
});

test("emissão e migração administrativas são exclusivamente offline", () => {
  assert.doesNotMatch(html, /value="hybrid"/);
  assert.doesNotMatch(html, /Prazo offline \(dias\)/);
  assert.equal((html.match(/Totalmente offline/g) || []).length, 2);
  assert.equal((script.match(/validation_mode: "offline"/g) || []).length, 2);
  assert.equal((script.match(/max_offline_days: 0/g) || []).length, 2);
  assert.doesNotMatch(script, /event\.target\.value === "hybrid"/);
});

test("formulários administrativos ignoram submissão duplicada", () => {
  assert.match(script, /if \(!beginFormSubmission\(form\)\) return/);
  assert.equal((script.match(/if \(!beginFormSubmission\(form\)\) return/g) || []).length, 2);
  assert.equal((script.match(/endFormSubmission\(form\)/g) || []).length, 3);
  assert.match(script, /form\.dataset\.submitting === "true"/);
});

test("chave privada pode ser configurada pela interface administrativa", () => {
  assert.match(html, /id="configurePrivateKeyButton"/);
  assert.match(html, /id="privateKeyStatus"/);
  assert.match(script, /bridge\("configure_private_key", password\)/);
  assert.match(script, /bridge\("private_key_status"\)/);
});
