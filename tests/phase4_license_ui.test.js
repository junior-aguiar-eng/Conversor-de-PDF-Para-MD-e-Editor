"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { presentation, warningLevel } = require("../web/license-ui.js");

function license(state, daysRemaining = 120, extra = {}) {
  return {
    state,
    can_use_protected_features: ["valid", "expiring"].includes(state),
    license_format: "act4",
    validation_mode: "offline",
    expires_at: "2027-01-15T12:00:00Z",
    days_remaining: daysRemaining,
    ...extra,
  };
}

test("cada estado possui linguagem específica e bloqueio coerente", () => {
  const expected = {
    check_failed: "Verificação indisponível",
    unlicensed: "Ativação necessária",
    valid: "Licença válida",
    expiring: "Licença próxima da expiração",
    online_check_required: "Prazo offline encerrado",
    expired: "Licença expirada",
    revoked: "Licença revogada",
    suspended: "Licença suspensa",
    clock_tampered: "Data ou hora inconsistente",
    machine_mismatch: "Licença vinculada a outra máquina",
    invalid: "Licença inválida",
  };
  for (const [state, headline] of Object.entries(expected)) {
    const view = presentation(license(state, state === "expiring" ? 30 : 0));
    assert.equal(view.headline, headline);
    assert.equal(view.blocking, !["valid", "expiring"].includes(state));
  }
});

test("marcos de expiração seguem 30, 15, 7, 3 e 1 dia", () => {
  assert.equal(warningLevel(license("expiring", 30)), "discrete");
  assert.equal(warningLevel(license("expiring", 15)), "persistent");
  assert.equal(warningLevel(license("expiring", 7)), "highlighted");
  assert.equal(warningLevel(license("expiring", 3)), "opening");
  assert.equal(warningLevel(license("expiring", 1)), "critical");
  assert.equal(presentation(license("expiring", 3)).openOnLaunch, true);
  assert.equal(presentation(license("expiring", 7)).openOnLaunch, false);
});

test("prazo offline não é confundido com expiração comercial", () => {
  const view = presentation(license("online_check_required", 80, {
    validation_mode: "hybrid",
    offline_until: "2026-08-30T12:00:00Z",
    offline_seconds_remaining: 0,
  }));
  assert.match(view.message, /licença comercial não está necessariamente expirada/i);
  assert.match(view.offlineRemaining, /^Encerrado em /);
  assert.equal(view.expiration, "15/01/2027, 09:00");
});

test("modo totalmente offline não depende de rede", () => {
  const source = require("node:fs").readFileSync(require("node:path").resolve(__dirname, "../web/license-ui.js"), "utf8");
  const view = presentation(license("valid"));
  assert.equal(view.offlineRemaining, "Não exigida (modo offline)");
  assert.doesNotMatch(source, /\bfetch\s*\(|XMLHttpRequest|WebSocket/);
});
