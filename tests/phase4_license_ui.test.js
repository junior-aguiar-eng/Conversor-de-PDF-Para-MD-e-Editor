"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { presentation, warningLevel } = require("../web/license-ui.js");

function license(state, daysRemaining = 120) {
  return {
    state,
    can_use_protected_features: ["valid", "expiring"].includes(state),
    expires_at: "2027-01-15T12:00:00Z",
    days_remaining: daysRemaining,
    license_id: "LIC-2026-000001",
    revision: 3,
  };
}

test("estados locais possuem bloqueio coerente", () => {
  for (const state of ["unlicensed", "expired", "clock_tampered", "machine_mismatch", "invalid"]) {
    assert.equal(presentation(license(state, 0)).blocking, true);
  }
  assert.equal(presentation(license("valid")).blocking, false);
  assert.equal(presentation(license("expiring", 7)).blocking, false);
});

test("marcos de expiração seguem 30, 15, 7, 3 e 1 dia", () => {
  assert.equal(warningLevel(license("expiring", 30)), "discrete");
  assert.equal(warningLevel(license("expiring", 15)), "persistent");
  assert.equal(warningLevel(license("expiring", 7)), "highlighted");
  assert.equal(warningLevel(license("expiring", 3)), "opening");
  assert.equal(warningLevel(license("expiring", 1)), "critical");
});

test("cliente depende apenas da importação ACT4 local", () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../web/index.html"), "utf8");
  const app = fs.readFileSync(path.resolve(__dirname, "../web/app.js"), "utf8");
  const source = fs.readFileSync(path.resolve(__dirname, "../web/license-ui.js"), "utf8");
  assert.match(html, /Importar arquivo de licença \(\.nxjlic\)/);
  assert.equal(presentation(license("valid")).licenseId, "LIC-2026-000001");
  assert.equal(presentation(license("valid")).revision, "3");
  assert.match(html, /Identificação da licença/);
  assert.match(html, /Revisão vigente/);
  assert.doesNotMatch(html + app + source, /Uso offline restante|validação online|offlineRemaining|lastOnlineValidation/i);
  assert.doesNotMatch(source, /online_check_required|revoked|suspended|\bfetch\s*\(/);
});
