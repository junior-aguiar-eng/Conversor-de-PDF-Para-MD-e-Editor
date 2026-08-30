"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const html = fs.readFileSync(path.resolve(__dirname, "../admin_web/index.html"), "utf8");
const script = fs.readFileSync(path.resolve(__dirname, "../admin_web/app.js"), "utf8");

test("migração ACT2 e ACT3 é uma ação administrativa explícita", () => {
  assert.match(html, /id="migrateLicenseButton"/);
  assert.match(html, /MIGRAÇÃO EXPLÍCITA/);
  assert.match(html, /A licença antiga continuará válida/);
  assert.match(html, /MIGRAR:NXJ2-0000-0000-0000-0000/);
  assert.match(script, /bridge\("migrate_legacy_license"/);
});

test("migração emite e oferece o arquivo ACT4 sem desativação silenciosa", () => {
  assert.match(script, /A licença antiga não foi desativada/);
  assert.match(script, /Exportar agora o arquivo ACT4 para o cliente/);
  assert.match(script, /bridge\("export_license", migrated\.license_id, password\)/);
});
