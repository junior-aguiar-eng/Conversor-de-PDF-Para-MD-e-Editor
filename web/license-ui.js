(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.NexoLicenseUI = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const STATE_COPY = Object.freeze({
    check_failed: ["Falha na verificação", "Verificação indisponível", "Não foi possível consultar a licença local."],
    unlicensed: ["Não ativada", "Ativação necessária", "Importe uma licença emitida para este computador."],
    valid: ["Válida", "Licença válida", "As funções licenciadas estão disponíveis."],
    expiring: ["Expira em breve", "Licença próxima da expiração", "Renove a licença antes da data de expiração."],
    expired: ["Expirada", "Licença expirada", "O prazo terminou e as funções protegidas estão bloqueadas."],
    clock_tampered: ["Relógio alterado", "Data ou hora inconsistente", "Foi detectado retrocesso relevante do relógio local."],
    machine_mismatch: ["Outro computador", "Licença vinculada a outra máquina", "Importe uma licença emitida para o código deste computador."],
    invalid: ["Inválida", "Licença inválida", "Importe novamente um arquivo de licença íntegro."],
  });

  function formatDate(value) {
    if (!value) return "Não informada";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "Não informada";
    return new Intl.DateTimeFormat("pt-BR", {
      dateStyle: "short", timeStyle: "short", timeZone: "America/Fortaleza",
    }).format(parsed);
  }

  function warningLevel(info) {
    const state = info.state || "invalid";
    if (!["valid", "expiring"].includes(state)) return "critical";
    if (state === "valid") return "normal";
    const days = Number(info.days_remaining);
    if (days <= 1) return "critical";
    if (days <= 3) return "opening";
    if (days <= 7) return "highlighted";
    if (days <= 15) return "persistent";
    return "discrete";
  }

  function presentation(info) {
    const state = info && info.state ? info.state : "invalid";
    const copy = STATE_COPY[state] || STATE_COPY.invalid;
    const level = warningLevel(info || {});
    const tone = level === "normal" ? "success" : (level === "discrete" || level === "persistent" ? "caution" : "danger");
    return {
      state,
      badge: copy[0],
      headline: copy[1],
      message: (info && info.message) || copy[2],
      warningLevel: level,
      tone,
      blocking: !info || !info.can_use_protected_features,
      openOnLaunch: !info || !info.can_use_protected_features || (state === "expiring" && Number(info.days_remaining) <= 3),
      expiration: info && info.expires_at ? formatDate(info.expires_at) : "Não informada",
      daysRemaining: Number.isInteger(info && info.days_remaining) ? `${info.days_remaining} dia(s)` : "Não informado",
      licenseId: info && info.license_id ? String(info.license_id) : "Não emitida",
      revision: Number.isInteger(info && info.revision) ? String(info.revision) : "Não informada",
    };
  }

  return { formatDate, presentation, warningLevel };
});
