"use strict";

const ui = {
  query: "",
  page: 1,
  pageSize: 25,
  selectedLicense: null,
  pendingOperations: new Set(),
};

const STATUS = {
  valid: "Válida",
  expiring: "Expirando",
  expired: "Expirada",
};

const ACTION_LABELS = {
  "admin_user.created": "Usuário administrativo criado",
  "customer.created": "Cliente criado",
  "license.issued": "Licença emitida",
  "license.renewed": "Licença renovada",
  "license.device_replaced": "Computador substituído",
  "license.exported": "Licença exportada",
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

function setActionButtonsPending(pending) {
  document.querySelectorAll("[data-admin-action]").forEach(button => {
    button.disabled = pending;
  });
}

function beginFormSubmission(form) {
  if (form.dataset.submitting === "true") return false;
  form.dataset.submitting = "true";
  form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(button => {
    button.disabled = true;
  });
  return true;
}

function endFormSubmission(form) {
  delete form.dataset.submitting;
  form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(button => {
    button.disabled = false;
  });
}

function showIssueStep(step) {
  const form = document.getElementById("newLicenseForm");
  form.dataset.step = String(step);
  document.querySelectorAll("[data-wizard-panel]").forEach(panel => {
    panel.classList.toggle("hidden", Number(panel.dataset.wizardPanel) !== step);
  });
  document.querySelectorAll("[data-wizard-marker]").forEach(marker => {
    marker.classList.toggle("active", Number(marker.dataset.wizardMarker) <= step);
  });
  document.getElementById("issuePrevious").classList.toggle("hidden", step === 1);
  document.getElementById("issueNext").classList.toggle("hidden", step === 3);
  document.getElementById("issueSubmit").classList.toggle("hidden", step !== 3);
  if (step === 3) {
    const data = new FormData(form);
    document.getElementById("issueSummary").innerHTML = `
      <div class="info-card"><dt>Cliente</dt><dd>${escapeHtml(data.get("name"))}</dd></div>
      <div class="info-card"><dt>Computador</dt><dd>${escapeHtml(data.get("machine_id"))}</dd></div>
      <div class="info-card"><dt>Prazo</dt><dd>${escapeHtml(data.get("term_months"))} meses</dd></div>
      <div class="info-card"><dt>Funcionalidades</dt><dd>${data.getAll("features").map(escapeHtml).join(", ")}</dd></div>`;
  }
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
      ["Válidas", data.counts.valid, "status:valida", "Mais de 30 dias"],
      ["Expirando", data.counts.expiring, "expira:30d", "Até 30 dias"],
      ["Expiradas", data.counts.expired, "status:expirada", "Prazo encerrado"],
    ];
    grid.innerHTML = cards.map(([label, count, filter, hint]) => `
      <article class="dashboard-card"><button data-dashboard-filter="${filter}">
        <span>${escapeHtml(label)}</span><strong class="number">${count}</strong><small>${escapeHtml(hint)}</small>
      </button></article>`).join("");
    document.getElementById("recentActivities").innerHTML = data.recent_activity.length
      ? data.recent_activity.map(item => `<li><span><strong>${escapeHtml(ACTION_LABELS[item.action] || item.action)}</strong> · ${escapeHtml(item.entity_id)}</span><time>${formatDate(item.occurred_at)}</time></li>`).join("")
      : '<li><span>Nenhuma atividade registrada.</span></li>';
  } catch (error) {
    grid.innerHTML = `<div class="alert error">${escapeHtml(error.message)}</div>`;
  }
}

async function loadPrivateKeyStatus() {
  const status = document.getElementById("privateKeyStatus");
  try {
    const result = await bridge("private_key_status");
    status.textContent = result.configured ? "Chave configurada" : "Chave não configurada";
    status.classList.toggle("ready", result.configured);
    status.classList.toggle("missing", !result.configured);
  } catch (error) {
    status.textContent = error.message;
    status.classList.add("missing");
  }
}

async function configurePrivateKey() {
  const password = window.prompt("Senha da chave privada criptografada:");
  if (password === null) return;
  const button = document.getElementById("configurePrivateKeyButton");
  button.disabled = true;
  try {
    const result = await bridge("configure_private_key", password);
    toast(`Chave configurada: ${result.file_name}.`);
    await loadPrivateKeyStatus();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
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
          <div class="result-meta"><span>${escapeHtml(item.machine_id)}</span><span>Revisão ${item.current_revision}</span><span>Validade: ${formatDate(item.expires_at)}</span></div>
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
        <div class="info-card"><dt>Prazo da última operação</dt><dd>${detail.term_months} meses</dd></div>
        <div class="info-card"><dt>Funcionalidades</dt><dd>${detail.features.map(escapeHtml).join(", ")}</dd></div>
        <div class="info-card"><dt>Dispositivo</dt><dd>${escapeHtml(device?.machine_id || "Não vinculado")}</dd></div>
        <div class="info-card"><dt>Validade</dt><dd>${formatDate(detail.expires_at)}</dd></div>
        <div class="info-card"><dt>Revisão atual</dt><dd>${detail.current_revision}</dd></div>
        <div class="info-card"><dt>Operação</dt><dd>Licença totalmente offline</dd></div>
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
    renew: "Renovar", export: "Reexportar .nxjlic", replace_device: "Trocar computador",
  };
  return `<button data-admin-action="${action}">${labels[action]}</button>`;
}

async function performAction(action) {
  const licenseId = ui.selectedLicense?.license_id;
  if (!licenseId) return;
  const operationKey = `license:${licenseId}`;
  if (ui.pendingOperations.has(operationKey)) return;
  ui.pendingOperations.add(operationKey);
  setActionButtonsPending(true);
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
      const password = window.prompt("Senha da chave privada administrativa:");
      if (password === null) return;
      const renewal = await bridge("renew_license", licenseId, term, password);
      toast(`Licença renovada até ${formatDate(renewal.expires_at)}.`);
      if (window.confirm("Reexportar agora a licença renovada?")) {
        const password = window.prompt("Senha da chave privada administrativa:");
        if (password !== null) await bridge("export_license", licenseId, password);
      }
    } else if (action === "replace_device") {
      const machine = window.prompt("Código completo da nova máquina NXJ2:");
      if (machine === null) return;
      const reason = window.prompt("Motivo obrigatório da troca:");
      if (reason === null) return;
      const confirmation = window.prompt(`Digite TROCAR:${licenseId} para confirmar:`);
      if (confirmation === null) return;
      const password = window.prompt("Senha da chave privada administrativa:");
      if (password === null) return;
      await bridge("replace_device", licenseId, machine, reason, confirmation, password);
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
  } finally {
    ui.pendingOperations.delete(operationKey);
    if (ui.selectedLicense?.license_id === licenseId) setActionButtonsPending(false);
  }
}

async function submitNewLicense(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!beginFormSubmission(form)) return;
  const errorBox = document.getElementById("newLicenseError");
  errorBox.classList.add("hidden");
  try {
    const data = new FormData(form);
    const password = window.prompt("Senha da chave privada administrativa:");
    if (password === null) return;
    const issued = await bridge("create_customer_license", {
      name: data.get("name"), email: data.get("email"), phone: data.get("phone"),
      tax_id: data.get("tax_id"), commercial_reference: data.get("commercial_reference"),
      term_months: Number(data.get("term_months")),
      machine_id: data.get("machine_id"),
      commercial_reference: data.get("commercial_reference"),
      features: data.getAll("features"),
      private_key_password: password,
    });
    form.reset();
    showIssueStep(1);
    document.getElementById("newLicenseModal").classList.add("hidden");
    toast(`Licença emitida: ${issued.license_id}`);
    await loadDashboard();
    await runSearch(issued.license_id, 1);
    await openDetail(issued.license_id);
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove("hidden");
  } finally {
    endFormSubmission(form);
  }
}

function bindEvents() {
  document.getElementById("searchForm").addEventListener("submit", event => {
    event.preventDefault();
    void runSearch(document.getElementById("searchInput").value, 1);
  });
  document.getElementById("refreshDashboard").addEventListener("click", () => void loadDashboard());
  document.getElementById("configurePrivateKeyButton").addEventListener("click", () => void configurePrivateKey());
  document.getElementById("newLicenseButton").addEventListener("click", () => {
    document.getElementById("newLicenseModal").classList.remove("hidden");
    document.querySelector('#newLicenseForm input[name="name"]').focus();
  });
  document.getElementById("quickIssue").addEventListener("click", () => {
    document.getElementById("newLicenseModal").classList.remove("hidden");
    document.querySelector('#newLicenseForm input[name="name"]').focus();
  });
  document.querySelectorAll("[data-focus-search]").forEach(button => button.addEventListener("click", () => {
    ui.pendingAction = button.dataset.focusSearch;
    document.getElementById("searchInput").focus();
    toast("Busque e abra a licença para concluir a operação.");
  }));
  document.getElementById("newLicenseForm").addEventListener("submit", submitNewLicense);
  document.getElementById("issueNext").addEventListener("click", () => {
    const form = document.getElementById("newLicenseForm");
    const step = Number(form.dataset.step || 1);
    const fields = [...form.querySelectorAll(`[data-wizard-panel="${step}"] input, [data-wizard-panel="${step}"] select`)];
    if (!fields.every(field => field.reportValidity())) return;
    if (step === 2 && !new FormData(form).getAll("features").length) {
      toast("Selecione ao menos uma funcionalidade.");
      return;
    }
    showIssueStep(Math.min(3, step + 1));
  });
  document.getElementById("issuePrevious").addEventListener("click", () => {
    const step = Number(document.getElementById("newLicenseForm").dataset.step || 1);
    showIssueStep(Math.max(1, step - 1));
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
  await loadPrivateKeyStatus();
  await loadDashboard();
  document.getElementById("searchInput").focus();
}

window.addEventListener("pywebviewready", () => void initialize(), { once: true });
if (window.pywebview?.api) void initialize();
