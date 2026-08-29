"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function makeClassList() {
  const values = new Set();
  return {
    add: (...items) => items.forEach((item) => values.add(item)),
    remove: (...items) => items.forEach((item) => values.delete(item)),
    contains: (item) => values.has(item),
    toggle: (item, force) => {
      if (force === true) values.add(item);
      else if (force === false) values.delete(item);
      else if (values.has(item)) values.delete(item);
      else values.add(item);
    },
  };
}

function makeElement() {
  const element = {
    classList: makeClassList(),
    children: [],
    dataset: {},
    style: {},
    value: "1.0",
    innerHTML: "",
    innerText: "",
    textContent: "",
    disabled: false,
    width: 100,
    height: 100,
    clientWidth: 900,
    clientHeight: 700,
    scrollHeight: 0,
    scrollTop: 0,
    appendChild(child) { this.children.push(child); return child; },
    append(...children) { this.children.push(...children); },
    addEventListener() {},
    setAttribute(name, value) { this[name] = value; },
    getAttribute(name) { return this[name]; },
    querySelector() { return null; },
    closest() { return null; },
    focus() {},
    blur() {},
    scrollIntoView() {},
    getContext() {
      return {
        clearRect() {}, drawImage() {}, fillRect() {}, beginPath() {}, moveTo() {}, lineTo() {},
        stroke() {}, save() {}, restore() {}, bezierCurveTo() {},
      };
    },
  };
  return element;
}

const elements = new Map();
const documentListeners = new Map();
const windowListeners = new Map();
const document = {
  activeElement: null,
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, makeElement());
    return elements.get(id);
  },
  createElement: () => makeElement(),
  createRange: () => ({ selectNodeContents() {}, collapse() {} }),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: (name, callback) => documentListeners.set(name, callback),
};

class ImmediateImage {
  set src(value) {
    this._src = value;
    if (this.onload) this.onload();
  }
}

const storage = new Map();
const windowObject = {
  addEventListener: (name, callback) => windowListeners.set(name, callback),
  getSelection: () => ({ removeAllRanges() {}, addRange() {} }),
  AudioContext: null,
};

const context = {
  console,
  document,
  window: windowObject,
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
  },
  navigator: { clipboard: { writeText: async () => {} } },
  performance: { now: () => 0 },
  Image: ImmediateImage,
  Audio: class { addEventListener() {} pause() {} play() { return Promise.resolve(); } },
  setTimeout: () => 1,
  clearTimeout: () => {},
  setInterval: () => 1,
  clearInterval: () => {},
  requestAnimationFrame: () => 1,
  cancelAnimationFrame: () => {},
  fetch: async () => ({ blob: async () => ({ type: "image/png" }) }),
  ClipboardItem: class {},
  Map,
  Set,
  Promise,
};
context.globalThis = context;
vm.createContext(context);

const appPath = path.resolve(__dirname, "..", "web", "app.js");
const source = fs.readFileSync(appPath, "utf8") + `
globalThis.__phase1 = {
  escapeHtml, safeSearchSnippetHtml, renderFileList, appendLog, onBridgeReady,
  initializeAfterTerms, state, SuperPdfController, GlobalSearchController, appLicense,
  appLibrary, appSearch, parseDeclarativeArgument, resolveDeclarativeAction,
};`;
vm.runInContext(source, context, { filename: appPath });

async function run() {
  const api = context.__phase1;

  assert.equal(typeof api.resolveDeclarativeAction("appLibrary.removeDocument"), "function");
  assert.equal(api.parseDeclarativeArgument("true", makeElement(), {}), true);

  let libraryRefreshes = 0;
  windowObject.pywebview = { api: { remove_library_document: async () => ({ ok: true }) } };
  windowObject.appSearch = { loadRecentLibrary: () => { libraryRefreshes += 1; } };
  api.appSearch.loadRecentLibrary = windowObject.appSearch.loadRecentLibrary;
  await api.appLibrary.removeDocument("entry-id");
  assert.equal(libraryRefreshes, 1);

  assert.equal(
    api.escapeHtml(`<img src=x onerror="alert(1)">'&`),
    "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&#39;&amp;",
  );

  api.state.files = [{
    file_id: "queue-id",
    path: `C:/<fila>.pdf`, name: `<img src=x onerror=alert(1)>`, size_formatted: `1 & 2`,
    status: "error", error_message: `"><script>alert(1)</script>`,
  }];
  api.renderFileList();
  const queueHtml = document.getElementById("fileListContainer").innerHTML;
  assert.ok(queueHtml.includes("&lt;img src=x onerror=alert(1)&gt;"));
  assert.ok(!queueHtml.includes("<script>"));

  const reader = new api.SuperPdfController();
  let initEffects = 0;
  reader.bindCanvasEvents = () => { initEffects += 1; };
  reader.bindWindowEvents = () => { initEffects += 1; };
  reader.bindFlyoutEvents = () => { initEffects += 1; };
  reader.buildColorPalettes = () => { initEffects += 1; };
  reader.init();
  reader.init();
  assert.equal(initEffects, 4);
  reader.currentFilePath = "C:/atual.pdf";
  reader.bookmarks = [{ id: 1, page_number: 0, title: `<svg onload=alert(1)>` }];
  reader.renderBookmarksList();
  assert.ok(document.getElementById("pdfBookmarksContainer").innerHTML.includes("&lt;svg onload=alert(1)&gt;"));

  api.state.files = [{ file_id: `id-\"-atributo`, path: `C:/\" atributo.pdf`, name: `<option>injetado</option>` }];
  reader.updateDocumentDropdown();
  const dropdownHtml = document.getElementById("pdfViewerSelect").innerHTML;
  assert.ok(dropdownHtml.includes("id-&quot;-atributo"));
  assert.ok(dropdownHtml.includes("&lt;option&gt;injetado&lt;/option&gt;"));

  const search = new api.GlobalSearchController();
  search.resultsContainer = document.getElementById("searchResults");
  search.metricsLabel = document.getElementById("searchMetrics");
  search.currentFilter = "all";
  search.allResults = [{
    content_type: "pdf_page", page_number: 0, file_path: `C:/<busca>.pdf`,
    file_name: `<img src=x onerror=alert(1)>`,
    snippet: `<img src=x onerror=alert(2)><mark class="bg-amber-200 text-amber-900 font-bold px-0.5 rounded">termo</mark>`,
  }];
  search.renderResults();
  const searchHtml = search.resultsContainer.innerHTML;
  assert.ok(!searchHtml.includes("<img src=x"));
  assert.ok(searchHtml.includes("&lt;img src=x onerror=alert(2)&gt;"));
  assert.ok(searchHtml.includes("<mark class="));

  const logConsole = document.getElementById("logConsole");
  logConsole.children = [];
  api.appendLog("ERRO", `<img src=x onerror=alert(1)>`);
  const messageElement = logConsole.children[0].children[2];
  assert.equal(messageElement.textContent, `<img src=x onerror=alert(1)>`);
  assert.equal(logConsole.children[0].innerHTML, "");

  const docA = "C:/a.pdf";
  const docB = "C:/b.pdf";
  const idA = "pdf-a";
  const idB = "pdf-b";
  windowObject.pywebview = { api: {
    get_pdf_info: async (fileId) => ({
      ok: true, file_id: fileId, file_path: fileId === idA ? docA : docB, file_name: fileId,
      page_count: 2, pages: [], metadata: {},
      is_encrypted: false, bookmarks: [], session_state: { found: false },
    }),
    get_pdf_page_range: async () => ({ ok: true, pages: [] }),
  } };
  const drafts = new api.SuperPdfController();
  drafts.renderCurrentPage = async () => {};
  drafts.currentFileId = idA;
  drafts.currentFilePath = docA;
  drafts.activateDocumentState(idA, docA);
  drafts.activeDocumentState.opened = true;
  drafts.annotations.set(0, [{ type: "highlight" }]);
  drafts.undoStack.push({ page: 0 });
  drafts.currentPassword = "senha-a";
  drafts.currentPage = 1;
  drafts.pageCache.set(`${idA}:1`, { image: "cached-a" });
  document.getElementById("pdfZoomSelect").value = "1.5";
  drafts.documentSwitchDecisionProvider = () => "draft";
  assert.equal(await drafts.loadDocument(idB, null, docB), true);
  assert.equal(drafts.annotations.size, 0);
  drafts.annotations.set(1, [{ type: "text" }]);
  assert.equal(await drafts.loadDocument(idA, null, docA), true);
  assert.equal(drafts.annotations.get(0).length, 1);
  assert.equal(drafts.currentPassword, "senha-a");
  assert.equal(drafts.currentPage, 1);
  assert.equal(document.getElementById("pdfZoomSelect").value, "1.5");
  assert.equal(drafts.pageCache.get(`${idA}:1`).image, "cached-a");
  assert.equal(drafts.undoStack.length, 1);
  assert.equal(drafts.documentStates.size, 2);

  const cancelReader = new api.SuperPdfController();
  cancelReader.renderCurrentPage = async () => {};
  cancelReader.currentFileId = idA;
  cancelReader.currentFilePath = docA;
  cancelReader.activateDocumentState(idA, docA);
  cancelReader.annotations.set(0, [{ type: "ink" }]);
  cancelReader.documentSwitchDecisionProvider = () => "cancel";
  assert.equal(await cancelReader.loadDocument(idB, null, docB), false);
  assert.equal(cancelReader.currentFilePath, docA);

  const discardReader = new api.SuperPdfController();
  discardReader.renderCurrentPage = async () => {};
  discardReader.currentFileId = idA;
  discardReader.currentFilePath = docA;
  discardReader.activateDocumentState(idA, docA);
  discardReader.annotations.set(0, [{ type: "ink" }]);
  discardReader.documentSwitchDecisionProvider = () => "discard";
  assert.equal(await discardReader.loadDocument(idB, null, docB), true);
  assert.equal(discardReader.getOrCreateDocumentState(idA, docA).annotations.size, 0);

  const saveReader = new api.SuperPdfController();
  saveReader.renderCurrentPage = async () => {};
  saveReader.currentFileId = idA;
  saveReader.currentFilePath = docA;
  saveReader.activateDocumentState(idA, docA);
  saveReader.annotations.set(0, [{ type: "ink" }]);
  saveReader.documentSwitchDecisionProvider = () => "save";
  let saves = 0;
  saveReader.saveAnnotations = async () => { saves += 1; saveReader.annotations.clear(); return true; };
  assert.equal(await saveReader.loadDocument(idB, null, docB), true);
  assert.equal(saves, 1);

  const pendingLoads = new Map();
  windowObject.pywebview.api.get_pdf_info = (filePath) => new Promise((resolve) => pendingLoads.set(filePath, resolve));
  const loadRace = new api.SuperPdfController();
  loadRace.renderCurrentPage = async () => {};
  const firstLoad = loadRace.loadDocument(idA, null, docA);
  await Promise.resolve();
  const secondLoad = loadRace.loadDocument(idB, null, docB);
  await Promise.resolve();
  pendingLoads.get(idB)({ ok: true, file_id: idB, file_path: docB, file_name: "B", page_count: 1, pages: [], metadata: {}, bookmarks: [], session_state: { found: false } });
  assert.equal(await secondLoad, true);
  pendingLoads.get(idA)({ ok: true, file_id: idA, file_path: docA, file_name: "A", page_count: 1, pages: [], metadata: {}, bookmarks: [], session_state: { found: false } });
  assert.equal(await firstLoad, false);
  assert.equal(loadRace.currentFilePath, docB);
  assert.equal(loadRace.currentFileName, "B");

  const pendingPages = new Map();
  windowObject.pywebview.api.render_page_hq = (_filePath, pageNumber) => new Promise((resolve) => pendingPages.set(pageNumber, resolve));
  const renderRace = new api.SuperPdfController();
  renderRace.ensurePageRange = async () => {};
  renderRace.currentFileId = idB;
  renderRace.currentFilePath = docB;
  const docStateB = renderRace.activateDocumentState(idB, docB);
  docStateB.pages = [{ page_number: 0, width: 111, height: 222 }, { page_number: 1, width: 222, height: 333 }];
  renderRace.totalPages = 2;
  renderRace.currentPage = 0;
  const firstRender = renderRace.renderCurrentPage();
  renderRace.currentPage = 1;
  const secondRender = renderRace.renderCurrentPage();
  await new Promise((r) => setTimeout(r, 10));
  pendingPages.get(1)({ ok: true, width: 222, height: 333, rotation: 90, pixel_width: 20, pixel_height: 30, image: "page-1" });
  await secondRender;
  pendingPages.get(0)({ ok: true, width: 111, height: 222, rotation: 0, pixel_width: 10, pixel_height: 20, image: "page-0" });
  await firstRender;
  assert.equal(renderRace.pageWidth, 222);
  assert.equal(renderRace.pageRotation, 90);
  assert.equal(renderRace.pageCache.has(`${idB}:0`), false);

  let termsChecks = 0;
  let licenseChecks = 0;
  let infoChecks = 0;
  let environmentChecks = 0;
  windowObject.pywebview.api = {
    get_terms_acceptance_status: async () => { termsChecks += 1; return { accepted: true }; },
    get_license_info: async () => { licenseChecks += 1; return { is_activated: true, machine_id: "NXJ-TEST" }; },
    get_app_info: async () => { infoChecks += 1; return { default_output_dir: "C:/saida", default_chunk_limit: 60000 }; },
    validate_environment: async () => { environmentChecks += 1; return { ok: true }; },
  };
  await Promise.all([api.onBridgeReady(), api.onBridgeReady()]);
  assert.equal(termsChecks, 1);
  assert.equal(licenseChecks, 1);
  assert.equal(infoChecks, 1);
  assert.equal(environmentChecks, 1);

  process.stdout.write("phase1_frontend_ok\n");
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
