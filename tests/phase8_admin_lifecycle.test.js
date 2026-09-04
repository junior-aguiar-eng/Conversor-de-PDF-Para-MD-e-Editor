"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const script = fs.readFileSync(path.resolve(__dirname, "../admin_web/app.js"), "utf8");

test("renovação mostra vencimento, confirma e permite reexportar", () => {
  assert.match(script, /projectedRenewalDate\(ui\.selectedLicense\.expires_at, term\)/);
  assert.match(script, /Novo vencimento previsto/);
  assert.match(script, /bridge\("renew_license", licenseId, term, password\)/);
  assert.match(script, /Reexportar agora a licença renovada/);
});

test("ações repetidas ficam bloqueadas durante a operação", () => {
  assert.match(script, /ui\.pendingOperations\.has\(operationKey\)/);
  assert.match(script, /ui\.pendingOperations\.delete\(operationKey\)/);
});
