"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const script = fs.readFileSync(path.resolve(__dirname, "../admin_web/app.js"), "utf8");

test("renovação mostra novo vencimento e exige confirmação", () => {
  assert.match(script, /projectedRenewalDate\(ui\.selectedLicense\.expires_at, term\)/);
  assert.match(script, /window\.confirm\([\s\S]+Novo vencimento previsto/);
  assert.match(script, /bridge\("renew_license", licenseId, term\)/);
  assert.match(script, /Licença renovada até/);
});

test("renovação offline oferece exportação imediata", () => {
  assert.match(script, /validation_mode === "offline"/);
  assert.match(script, /Exportar agora a licença offline renovada/);
  assert.match(script, /bridge\("export_license", licenseId, password\)/);
});

test("ações repetidas ficam bloqueadas enquanto a primeira está em andamento", () => {
  assert.match(script, /if \(ui\.pendingOperations\.has\(operationKey\)\) return/);
  assert.match(script, /ui\.pendingOperations\.add\(operationKey\)/);
  assert.match(script, /ui\.pendingOperations\.delete\(operationKey\)/);
  assert.match(script, /setActionButtonsPending\(true\)/);
  assert.match(script, /setActionButtonsPending\(false\)/);
});

test("reativação, revogação e troca permanecem disponíveis", () => {
  assert.match(script, /bridge\("reactivate_license", licenseId, reason\)/);
  assert.match(script, /REVOGAR:\$\{licenseId\}/);
  assert.match(script, /TROCAR:\$\{licenseId\}/);
  assert.match(script, /Exportar agora a licença ACT4 para o novo computador/);
});
