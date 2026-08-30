"use strict";

const ui = {
  query: "",
  page: 1,
  pageSize: 25,
  selectedLicense: null,
};

const STATUS = {
  active: "Ativa",
  expired: "Expirada",
  suspended: "Suspensa",
  revoked: "Revogada",
};

const ACTION_LABELS = {
  "admin_user.created": "Usuário administrativo criado",
  "customer.created": "Cliente criado",
  "license.issued": "Licença emitida",
  "device.bound": "Dispositivo vinculado",
  "license.renewed": "Licença renovada",
  "license.suspended": "Licença suspensa",
  "license.active": "Licença reativada",
  "license.revoked": "Licença revogada",
  "device.replaced": "Dispositivo substituído",
  "license.exported": "Licença exportada",
  "license.migrated_from_legacy": "Licença legada migrada para ACT4",
  "database.backup_created": "Backup criado",
  "database.backup_restored": "Backup restaurado",
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function formatDate(value) {
  if (!value) return "Não registrada";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Data inválida";
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(date);
}

function projectedRenewalDate(currentExpiration, months) {
  const current = new Date(currentExpiration);
  const base = current.getTime() > Date.now() ? current : new Date();
  const day = base.getUTCDate();
  const projected = new Date(base);
  projected.setUTCDate(1);
  projected.setUTCMonth(projected.getUTCMonth() + months);
  projected.setUTCDate(Math.min(day, new Date(Date.UTC(
    projected.getUTCFullYear(), projected.getUTCMonth() + 1, 0,
  )).getUTCDate()));
  return projected;
}

function statusBadge(status) {
  const normalized = STATUS[status] ? status : "expired";
  return `<span class="status ${normalized}">${STATUS[normalized]}</span>`;
}

function toast(message) {
  const element = document.getElementById("toast");
  element.textContent = message;
  element.classList.remove("hidden");
  window.setTimeout(() => element.classList.add("hidden"), 3200);
}

async function bridge(method, ...args) {
  if (!window.pywebview?.api?.[method]) throw new Error("Bridge administrativa indisponível.");
  const result = await window.pywebview.api[method](...args);
  if (!result?.ok) throw new Error(result?.error || "Operação administrativa recusada.");
  return result;
}

async function loadDashboard() {
  const grid = document.getElementById("dashboardGrid");
  grid.innerHTML = '<div class="empty">Carregando situação atual…</div>';
  try {
    const data = await bridge("dashboard");
    const cards = [
      ["Expirando", data.counts.expiring, "expira:30d", "Até 30 dias"],
      ["Aguardando conexão", data.counts.awaiting_connection, "status:aguardando", "Modo híbrido sem lease ativo"],
      ["Suspensas", data.counts.suspended, "status:suspensa", "Ação administrativa pendente"],
      ["Revogadas", data.counts.revoked, "status:revogada", "Bloqueio definitivo"],
    ];
    grid.innerHTML = cards.map(([label, count, filter, hint]) => `
      <article class="dashboard-card"><button data-dashboard-filter="${filter}">
        <span>${escapeHtml(label)}</span><strong class="number">${count}</strong><small>${escapeHtml(hint)}</small>
      </button></article>`).join("");
    document.getElementById("recentActivities").innerHTML = data.activities.length
      ? data.activities.map(item => `<li><span><strong>${escapeHtml(ACTION_LABELS[item.action] || item.action)}</strong> · ${escapeHtml(item.entity_id)}</span><time>${formatDate(item.occurred_at)}</time></li>`).join("")
      : '<li><span>Nenhuma atividade registrada.</span></li>';
  } catch (error) {
    grid.innerHTML = `<div class="alert error">${escapeHtml(error.message)}</div>`;
  }
}

async function runSearch(query = ui.query, page = 1) {
  const normalized = String(query || "").trim();
  if (!normalized) {
    document.getElementById("resultsSection").classList.add("hidden");
    return;
  }
  ui.query = normalized;
  ui.page = page;
  document.getElementById("searchInput").value = normalized;
  const section = document.getElementById("resultsSection");
  const list = document.getElementById("resultList");
  section.classList.remove("hidden");
  list.innerHTML = '<div class="empty">Buscando…</div>';
  document.getElementById("searchError").classList.add("hidden");
  try {
    const result = await bridge("search_licenses", normalized, page, ui.pageSize);
    document.getElementById("resultCount").textContent = `${result.total} resultado(s)`;
    list.innerHTML = result.items.length ? result.items.map(item => `
      <article class="result-card">
        <button data-license-id="${escapeHtml(item.license_id)}">
          <div class="result-main"><strong>${escapeHtml(item.customer_name)}</strong>${statusBadge(item.effective_status)}<code>${escapeHtml(item.license_id)}</code></div>
          <div class="result-meta"><span>${escapeHtml(item.machine_id || "Sem dispositivo")}</span><span>${escapeHtml(item.email || "Sem e-mail")}</span><span>Validade: ${formatDate(item.expires_at)}</span></div>
        </button><span aria-hidden="true">›</span>
      </article>`).join("") : '<div class="empty">Nenhuma licença corresponde à busca.</div>';
    renderPagination(result);
  } catch (error) {
    list.innerHTML = "";
    const alert = document.getElementById("searchError");
    alert.textContent = error.message;
    alert.classList.remove("hidden");
  }
}

function renderPagination(result) {
  const pages = Math.max(1, Math.ceil(result.total / result.page_size));
  const navigation = document.getElementById("pagination");
  navigation.innerHTML = pages <= 1 ? "" : `
    <button data-page="${result.page - 1}" ${result.page <= 1 ? "disabled" : ""}>Anterior</button>
    <span>Página ${result.page} de ${pages}</span>
    <button data-page="${result.page + 1}" ${result.page >= pages ? "disabled" : ""}>Próxima</button>`;
}

async function openDetail(licenseId) {
  const modal = document.getElementById("detailModal");
  const content = document.getElementById("detailContent");
  modal.classList.remove("hidden");
  content.innerHTML = '<div class="empty">Carregando licença…</div>';
  try {
    const detail = await bridge("get_license_detail", licenseId);
    ui.selectedLicense = detail;
    const customer = detail.customer;
    const device = detail.active_device;
    content.innerHTML = `
      <p class="eyebrow">${escapeHtml(detail.license_id)}</p>
      <div class="section-heading"><div><h2 id="detailTitle">${escapeHtml(customer.name)}</h2><p class="subtitle">${escapeHtml(customer.email || "Sem e-mail")} · ${escapeHtml(customer.phone || "Sem telefone")}</p></div>${statusBadge(detail.effective_status)}</div>
      <dl class="detail-grid">
        <div class="info-card"><dt>Plano</dt><dd>${detail.term_months} meses · ${escapeHtml(detail.validation_mode)}</dd></div>
        <div class="info-card"><dt>Funcionalidades</dt><dd>${detail.features.map(escapeHtml).join(", ")}</dd></div>
        <div class="info-card"><dt>Dispositivo</dt><dd>${escapeHtml(device?.machine_id || "Não vinculado")}</dd></div>
        <div class="info-card"><dt>Validade</dt><dd>${formatDate(detail.expires_at)}</dd></div>
        <div class="info-card"><dt>Última conexão</dt><dd>${formatDate(detail.last_connection)}</dd></div>
        <div class="info-card"><dt>Prazo offline</dt><dd>${detail.validation_mode === "offline" ? "Não exigido" : formatDate(detail.offline_until)}</dd></div>
        <div class="info-card"><dt>Referência comercial</dt><dd>${escapeHtml(detail.commercial_reference || customer.commercial_reference || "Não informada")}</dd></div>
        <div class="info-card"><dt>CPF/CNPJ</dt><dd>${escapeHtml(customer.tax_id || "Não informado")}</dd></div>
      </dl>
      <div class="action-bar">${detail.available_actions.map(actionButton).join("")}</div>
      <h3>Histórico</h3>
      <ol class="history">${detail.history.length ? detail.history.map(item => `<li><strong>${escapeHtml(ACTION_LABELS[item.action] || item.action)}</strong><small>${formatDate(item.occurred_at)} · ${escapeHtml(item.admin_user_id || "sistema")}</small></li>`).join("") : "<li>Nenhum evento.</li>"}</ol>`;
  } catch (error) {
    content.innerHTML = `<div class="alert error">${escapeHtml(error.message)}</div>`;
  }
}

function actionButton(action) {
  const labels = {
    renew: "Renovar", export: "Exportar .nxjlic", suspend: "Suspender", revoke: "Revogar",
    reactivate: "Reativar", bind_device: "Vincular dispositivo", replace_device: "Trocar computador",
  };
  return `<button data-admin-action="${action}" class="${action === "revoke" ? "danger" : ""}">${labels[action]}</button>`;
}

async function performAction(action) {
  const licenseId = ui.selectedLicense?.license_id;
  if (!licenseId) return;
  try {
    if (action === "renew") {
      const months = window.prompt("Prazo da renovação em meses: 3, 6 ou 12", "12");
      if (months === null) return;
      const term = Number(months);
      if (![3, 6, 12].includes(term)) throw new Error("A renovação deve ser de 3, 6 ou 12 meses.");
      const projected = projectedRenewalDate(ui.selectedLicense.expires_at, term);
      const confirmed = window.confirm(
        `Confirmar renovação de ${term} meses?\nNovo vencimento previsto: ${formatDate(projected.toISOString())}`,
      );
      if (!confirmed) return;
      const renewal = await bridge("renew_license", licenseId, term);
      toast(`Licença renovada até ${formatDate(renewal.expires_at)}.`);
      if (ui.selectedLicense.validation_mode === "offline" && window.confirm("Exportar agora a licença offline renovada?")) {
        const password = window.prompt("Senha da chave privada administrativa:");
        if (password !== null) await bridge("export_license", licenseId, password);
      }
    } else if (action === "suspend") {
      const reason = window.prompt("Motivo obrigatório da suspensão:");
      if (reason === null) return;
      await bridge("suspend_license", licenseId, reason);
    } else if (action === "reactivate") {
      const reason = window.prompt("Motivo obrigatório da reativação:");
      if (reason === null) return;
      await bridge("reactivate_license", licenseId, reason);
    } else if (action === "revoke") {
      const reason = window.prompt("Motivo obrigatório da revogação definitiva:");
      if (reason === null) return;
      const confirmation = window.prompt(`Digite REVOGAR:${licenseId} para confirmar:`);
      if (confirmation === null) return;
      await bridge("revoke_license", licenseId, reason, confirmation);
    } else if (action === "bind_device") {
      const machine = window.prompt("Código completo da máquina NXJ2:");
      if (machine === null) return;
      await bridge("bind_device", licenseId, machine);
    } else if (action === "replace_device") {
      const machine = window.prompt("Código completo da nova máquina NXJ2:");
      if (machine === null) return;
      const reason = window.prompt("Motivo obrigatório da troca:");
      if (reason === null) return;
      const confirmation = window.prompt(`Digite TROCAR:${licenseId} para confirmar:`);
      if (confirmation === null) return;
      await bridge("replace_device", licenseId, machine, reason, confirmation);
      if (window.confirm("Exportar agora a licença ACT4 para o novo computador?")) {
        const password = window.prompt("Senha da chave privada administrativa:");
        if (password !== null) await bridge("export_license", licenseId, password);
      }
    } else if (action === "export") {
      const password = window.prompt("Senha da chave privada administrativa:");
      if (password === null) return;
      const result = await bridge("export_license", licenseId, password);
      toast(`Licença exportada: ${result.file_name}`);
      return;
    }
    toast("Operação registrada com sucesso.");
    await openDetail(licenseId);
    await loadDashboard();
    if (ui.query) await runSearch(ui.query, ui.page);
  } catch (error) {
    toast(error.message);
  }
}

async function submitNewLicense(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const errorBox = document.getElementById("newLicenseError");
  errorBox.classList.add("hidden");
  try {
    const issued = await bridge("create_customer_license", {
      name: data.get("name"), email: data.get("email"), phone: data.get("phone"),
      tax_id: data.get("tax_id"), commercial_reference: data.get("commercial_reference"),
      term_months: Number(data.get("term_months")),
      validation_mode: data.get("validation_mode"),
      max_offline_days: Number(data.get("max_offline_days")),
      machine_id: data.get("machine_id"),
      commercial_reference: data.get("commercial_reference"),
      features: data.getAll("features"),
    });
    form.reset();
    document.getElementById("offlineDays").disabled = true;
    document.getElementById("newLicenseModal").classList.add("hidden");
    toast(`Licença emitida: ${issued.license_id}`);
    await loadDashboard();
    await runSearch(issued.license_id, 1);
    await openDetail(issued.license_id);
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove("hidden");
  }
}

async function submitMigration(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const errorBox = document.getElementById("migrationError");
  errorBox.classList.add("hidden");
  try {
    const migrated = await bridge("migrate_legacy_license", {
      name: data.get("name"),
      email: data.get("email"),
      commercial_reference: data.get("commercial_reference"),
      machine_id: data.get("machine_id"),
      activation_key: data.get("activation_key"),
      confirmation: data.get("confirmation"),
      term_months: Number(data.get("term_months")),
      validation_mode: data.get("validation_mode"),
      max_offline_days: Number(data.get("max_offline_days")),
      features: data.getAll("features"),
    });
    form.reset();
    document.getElementById("migrationOfflineDays").disabled = true;
    document.getElementById("migrationModal").classList.add("hidden");
    toast(`Migração registrada: ${migrated.license_id}. A licença antiga não foi desativada.`);
    if (window.confirm("Exportar agora o arquivo ACT4 para o cliente?")) {
      const password = window.prompt("Senha da chave privada administrativa:");
      if (password !== null) await bridge("export_license", migrated.license_id, password);
    }
    await loadDashboard();
    await runSearch(migrated.license_id, 1);
    await openDetail(migrated.license_id);
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove("hidden");
  }
}

function bindEvents() {
  document.getElementById("searchForm").addEventListener("submit", event => {
    event.preventDefault();
    void runSearch(document.getElementById("searchInput").value, 1);
  });
  document.getElementById("refreshDashboard").addEventListener("click", () => void loadDashboard());
  document.getElementById("newLicenseButton").addEventListener("click", () => {
    document.getElementById("newLicenseModal").classList.remove("hidden");
    document.querySelector('#newLicenseForm input[name="name"]').focus();
  });
  document.getElementById("migrateLicenseButton").addEventListener("click", () => {
    document.getElementById("migrationModal").classList.remove("hidden");
    document.querySelector('#migrationForm input[name="name"]').focus();
  });
  document.getElementById("newLicenseForm").addEventListener("submit", submitNewLicense);
  document.getElementById("migrationForm").addEventListener("submit", submitMigration);
  document.getElementById("validationMode").addEventListener("change", event => {
    const input = document.getElementById("offlineDays");
    input.disabled = event.target.value !== "hybrid";
    input.value = event.target.value === "hybrid" ? "7" : "0";
  });
  document.getElementById("migrationValidationMode").addEventListener("change", event => {
    const input = document.getElementById("migrationOfflineDays");
    input.disabled = event.target.value !== "hybrid";
    input.value = event.target.value === "hybrid" ? "7" : "0";
  });
  document.addEventListener("click", event => {
    const close = event.target.closest("[data-close]");
    if (close) document.getElementById(close.dataset.close).classList.add("hidden");
    const filter = event.target.closest("[data-filter], [data-dashboard-filter]");
    if (filter) void runSearch(filter.dataset.filter || filter.dataset.dashboardFilter, 1);
    const result = event.target.closest("[data-license-id]");
    if (result) void openDetail(result.dataset.licenseId);
    const page = event.target.closest("[data-page]");
    if (page && !page.disabled) void runSearch(ui.query, Number(page.dataset.page));
    const action = event.target.closest("[data-admin-action]");
    if (action) void performAction(action.dataset.adminAction);
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") document.querySelectorAll(".modal").forEach(modal => modal.classList.add("hidden"));
    if (event.key === "/" && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) {
      event.preventDefault();
      document.getElementById("searchInput").focus();
    }
  });
}

async function initialize() {
  bindEvents();
  await loadDashboard();
  document.getElementById("searchInput").focus();
}

window.addEventListener("pywebviewready", () => void initialize(), { once: true });
if (window.pywebview?.api) void initialize();
