(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.NexoLicenseUI = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const STATE_COPY = Object.freeze({
    check_failed: ["Falha na verificação", "Verificação indisponível", "Não foi possível consultar a licença local. Tente novamente ou encerre o aplicativo."],
    unlicensed: ["Não ativada", "Ativação necessária", "Importe uma licença ou informe uma chave válida para este computador."],
    valid: ["Válida", "Licença válida", "As funções licenciadas estão disponíveis."],
    expiring: ["Expira em breve", "Licença próxima da expiração", "Renove a licença antes da data de expiração para evitar bloqueio das funções protegidas."],
    online_check_required: ["Conexão necessária", "Prazo offline encerrado", "Conecte-se à internet para renovar o prazo offline. A licença comercial não está necessariamente expirada."],
    expired: ["Expirada", "Licença expirada", "O prazo comercial terminou e as funções protegidas estão bloqueadas."],
    revoked: ["Revogada", "Licença revogada", "Esta licença foi revogada administrativamente. As funções protegidas estão bloqueadas."],
    suspended: ["Suspensa", "Licença suspensa", "Esta licença está suspensa. As funções protegidas estão bloqueadas."],
    clock_tampered: ["Relógio alterado", "Data ou hora inconsistente", "Foi detectado retrocesso relevante do relógio. Conecte-se para validar a licença com uma referência confiável."],
    machine_mismatch: ["Outro computador", "Licença vinculada a outra máquina", "Importe uma licença emitida para o código deste computador."],
    invalid: ["Inválida", "Licença inválida", "A licença armazenada não pôde ser validada. Importe novamente um arquivo íntegro."],
  });

  function parseDate(value) {
    if (!value) return null;
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  function formatDate(value) {
    const parsed = parseDate(value);
    if (!parsed) return "Não informada";
    return new Intl.DateTimeFormat("pt-BR", {
      dateStyle: "short",
      timeStyle: "short",
      timeZone: "America/Fortaleza",
    }).format(parsed);
  }

  function formatOffline(info) {
    if (info.validation_mode === "offline" || info.license_format === "legacy") {
      return "Não exigida (modo offline)";
    }
    if (!info.offline_until) return "Aguardando validação online";
    const seconds = Math.max(0, Number(info.offline_seconds_remaining) || 0);
    if (seconds === 0) return `Encerrado em ${formatDate(info.offline_until)}`;
    const days = Math.floor(seconds / 86400);
    const hours = Math.ceil((seconds % 86400) / 3600);
    const remaining = days > 0 ? `${days} dia(s) e ${hours} hora(s)` : `${hours} hora(s)`;
    return `${remaining} — até ${formatDate(info.offline_until)}`;
  }

  function warningLevel(info) {
    const state = info.state || "invalid";
    if (["check_failed", "unlicensed", "expired", "revoked", "suspended", "clock_tampered", "machine_mismatch", "invalid"].includes(state)) return "critical";
    if (state === "online_check_required") return "offline";
    if (state !== "expiring") return "normal";
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
    const tone = level === "normal" ? "success" : (level === "discrete" || level === "persistent" ? "caution" : (level === "offline" ? "info" : "danger"));
    return {
      state,
      badge: copy[0],
      headline: copy[1],
      message: (info && info.message) || copy[2],
      warningLevel: level,
      tone,
      blocking: !info || !info.can_use_protected_features,
      openOnLaunch: !info || !info.can_use_protected_features || (state === "expiring" && Number(info.days_remaining) <= 3),
      expiration: info && info.expires_at ? formatDate(info.expires_at) : (info && info.license_format === "legacy" ? "Sem expiração definida" : "Não informada"),
      daysRemaining: Number.isInteger(info && info.days_remaining) ? `${info.days_remaining} dia(s)` : "Não informado",
      offlineRemaining: formatOffline(info || {}),
      lastOnlineValidation: info && info.last_online_validation ? formatDate(info.last_online_validation) : "Ainda não realizada",
    };
  }

  return { formatDate, formatOffline, presentation, warningLevel };
});
