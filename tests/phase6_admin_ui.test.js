"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const html = fs.readFileSync(path.resolve(__dirname, "../admin_web/index.html"), "utf8");
const script = fs.readFileSync(path.resolve(__dirname, "../admin_web/app.js"), "utf8");

test("admin expõe apenas as quatro operações comerciais essenciais", () => {
  for (const label of ["Emitir licença", "Renovar", "Trocar computador", "Reexportar arquivo"]) {
    assert.match(html, new RegExp(label));
  }
  assert.doesNotMatch(html + script, /Revogar|Suspender|Migrar ACT2|Aguardando conexão/);
});

test("interface é local, acessível e sem handlers inline", () => {
  assert.match(html, /Content-Security-Policy/);
  assert.match(html, /role="search"/);
  assert.match(html, /aria-modal="true"/);
  assert.doesNotMatch(html, /\son[a-z]+\s*=/i);
  assert.doesNotMatch(html, /https?:\/\//i);
});

test("troca exige frase vinculada à licença e formulário bloqueia duplicidade", () => {
  assert.match(script, /TROCAR:\$\{licenseId\}/);
  assert.match(script, /bridge\("replace_device", licenseId, machine, reason, confirmation, password\)/);
  assert.match(script, /if \(!beginFormSubmission\(form\)\) return/);
});
