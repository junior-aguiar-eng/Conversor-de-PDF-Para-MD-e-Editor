/**
 * Lógica do Cliente Frontend da Interface Chromium
 * NexoJuris - Conversor
 */

// Estado Global da Aplicação
const state = {
  files: [],               // Caminhos são apenas de exibição; operações usam file_id/markdown_id.
  outputDir: "",
  outputDirId: "",
  defaultOutputDir: "",
  defaultOutputDirId: "",
  selectedProfile: "jurisprudencia", // "jurisprudencia" | "curso"
  splitOutput: false,
  splitMode: "semantic",
  maxChunkCharacters: 60000,
  isConverting: false,
  isPaused: false,
  soundEnabled: true,
  convertedResults: [],   // Array de { name, path, markdown_path, asset_count }
  currentPreviewPath: null,
  currentPreviewId: null,
  startTime: 0,
  timerInterval: null
};

let bridgeInitializationPromise = null;
let applicationInitializationPromise = null;
let declarativeEventsInitialized = false;

const ALLOWED_DECLARATIVE_ACTIONS = new Set([
  "appLicense.copyMachineId", "appLicense.submitActivation",
  "appLibrary.relocateDocument", "appLibrary.removeDocument",
  "appManual.close", "appManual.filterContent", "appManual.open", "appManual.printManual",
  "appManual.scrollToChapter", "appManual.toggleViewMode",
  "appSearch.clearInput", "appSearch.closeModal", "appSearch.onSearchInput", "appSearch.openModal",
  "appSearch.openResult", "appSearch.setFilter", "appSelection.copySelectedText",
  "appTerms.confirmAcceptance", "appTranslator.closeModal", "appTranslator.copyResult",
  "appTranslator.retranslate", "appTranslator.translateSelectedText", "appTranslator.translateSnippetText",
  "appTts.playSelectedText", "appTts.playSnippetText", "appTts.playText", "appTts.seek",
  "appTts.setSpeed", "appTts.setVoice", "appTts.stopAndHide", "appTts.togglePlay",
  "appWelcome.close", "appWelcome.open", "appWelcome.switchTab",
  "clearActivityLogs", "clearAllFiles", "copyCurrentPreviewContent", "handlePreviewDocChange",
  "openCurrentOutputFolder", "openCurrentPreviewFile", "openLastMarkdownResult", "openQueuedMarkdown",
  "openQueuedPdf", "previewQueuedMarkdown", "removeFile", "requestStop", "resetOutputDirToDefault", "retryFailedPages",
  "selectProfile", "startConversion", "switchView", "toggleAdvancedOptions", "toggleAudioFeedback",
  "togglePause", "triggerFileSelect", "triggerSelectOutputDir",
  "superPdf.addBookmark", "superPdf.cancelIndexing", "superPdf.chooseDocumentSwitchAction", "superPdf.clearPageAnnotations",
  "superPdf.closeProtectModal", "superPdf.closeSnippetModal", "superPdf.closeUnlockModal",
  "superPdf.copySnippetImage", "superPdf.copySnippetText", "superPdf.deleteBookmark",
  "superPdf.goToPage", "superPdf.handleToolMainClick", "superPdf.nextPage", "superPdf.onHlWidthChange",
  "superPdf.onPenWidthChange", "superPdf.onSelectDocument", "superPdf.onTextSizeChange",
  "superPdf.openFileDialog", "superPdf.openProtectModal", "superPdf.pauseIndexing", "superPdf.prevPage", "superPdf.printDocument",
  "superPdf.rotatePage", "superPdf.saveAnnotations", "superPdf.setHighlightColor",
  "superPdf.setHighlightMode", "superPdf.setPenColor", "superPdf.setSidebarTab", "superPdf.setTextBoxStyle",
  "superPdf.setTextColor", "superPdf.setTool", "superPdf.setZoom", "superPdf.startFullIndexing", "superPdf.submitProtectPdf",
  "superPdf.submitUnlockPdf", "superPdf.submitUnprotectPdf", "superPdf.toggleBookmark",
  "superPdf.toggleFlyout", "superPdf.toggleTextBold", "superPdf.undoAnnotation", "superPdf.zoomIn",
  "superPdf.zoomOut",
]);

function parseDeclarativeArgument(raw, element, event) {
  const value = raw.trim();
  if (value === "event") return event;
  if (value === "this.value") return element.value;
  if (value === "true") return true;
  if (value === "false") return false;
  if (value === "parseInt(this.value) - 1") return parseInt(element.value, 10) - 1;
  const inputMatch = value.match(/^document\.getElementById\('([A-Za-z][\w-]*)'\)\.value$/);
  if (inputMatch) return document.getElementById(inputMatch[1])?.value || "";
  if (/^-?\d+(?:\.\d+)?$/.test(value)) return Number(value);
  const stringMatch = value.match(/^'([^'\\]*)'$/);
  if (stringMatch) return stringMatch[1];
  throw new Error("Argumento declarativo não permitido.");
}

function resolveDeclarativeAction(actionName) {
  if (!ALLOWED_DECLARATIVE_ACTIONS.has(actionName)) return null;
  const parts = actionName.split(".");
  if (parts.length === 1) {
    const actions = {
      clearActivityLogs, clearAllFiles, copyCurrentPreviewContent, handlePreviewDocChange,
      openCurrentOutputFolder, openCurrentPreviewFile, openLastMarkdownResult, openQueuedMarkdown,
      openQueuedPdf, previewQueuedMarkdown, removeFile, requestStop, resetOutputDirToDefault,
      retryFailedPages, selectProfile, startConversion, switchView, toggleAdvancedOptions, toggleAudioFeedback,
      togglePause, triggerFileSelect, triggerSelectOutputDir,
    };
    return actions[actionName] || null;
  }
  const roots = { appLicense, appLibrary, appManual, appSearch, appSelection, appTerms, appTranslator, appTts, appWelcome, superPdf };
  const owner = roots[parts[0]];
  const method = owner?.[parts[1]];
  return typeof method === "function" ? method.bind(owner) : null;
}

function invokeDeclarativeAction(expression, element, event) {
  const match = String(expression || "").match(/^([A-Za-z]\w*(?:\.[A-Za-z]\w*)?)\((.*)\)$/);
  if (!match) return;
  const action = resolveDeclarativeAction(match[1]);
  if (!action) return;
  const rawArgs = match[2].trim();
  const args = rawArgs ? rawArgs.split(/,(?=(?:[^']*'[^']*')*[^']*$)/).map((arg) => parseDeclarativeArgument(arg, element, event)) : [];
  action(...args);
}

function setupDeclarativeEvents() {
  if (declarativeEventsInitialized) return;
  declarativeEventsInitialized = true;
  document.addEventListener("click", (event) => {
    const element = event.target.closest?.("[data-action]");
    if (element) invokeDeclarativeAction(element.dataset.action, element, event);
  });
  document.addEventListener("change", (event) => {
    const element = event.target.closest?.("[data-change]");
    if (element) invokeDeclarativeAction(element.dataset.change, element, event);
  });
  document.addEventListener("input", (event) => {
    const element = event.target.closest?.("[data-input]");
    if (element) invokeDeclarativeAction(element.dataset.input, element, event);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const element = event.target.closest?.("[data-enter-action]");
    if (element) invokeDeclarativeAction(element.dataset.enterAction, element, event);
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeSearchSnippetHtml(value) {
  const allowedOpen = '<mark class="bg-amber-200 text-amber-900 font-bold px-0.5 rounded">';
  return String(value ?? "")
    .split(new RegExp(`(${allowedOpen.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}|</mark>)`, "g"))
    .map((part) => (part === allowedOpen || part === "</mark>" ? part : escapeHtml(part)))
    .join("");
}

// Inicialização ao carregar a página
document.addEventListener("DOMContentLoaded", () => {
  setupDeclarativeEvents();
  setupMarkdownPreviewInteractions();
  initAudioPreference();
  setupDragAndDrop();
  superPdf.init();
  appSelection.init();
  appTts.init();
  appTranslator.init();
  appSearch.init();
  waitForPyWebViewReady();
});

function waitForPyWebViewReady() {
  if (window.pywebview && window.pywebview.api) {
    void onBridgeReady();
  } else {
    window.addEventListener("pywebviewready", onBridgeReady, { once: true });
    // Fallback polling de segurança
    setTimeout(() => {
      if (window.pywebview && window.pywebview.api) {
        void onBridgeReady();
      }
    }, 500);
  }
}

function onBridgeReady() {
  if (!bridgeInitializationPromise) {
    bridgeInitializationPromise = (async () => {
      try {
        const termsStatus = await window.pywebview.api.get_terms_acceptance_status();
        if (termsStatus && termsStatus.accepted && !termsStatus.needs_reacceptance) {
          await initializeAfterTerms();
        } else {
          const modal = document.getElementById("termsModal");
          if (modal) {
            modal.classList.remove("hidden");
            appTerms.init();
          }
        }
      } catch (error) {
        console.error("Erro ao verificar termos/inicializar Bridge API:", error);
      }
    })();
  }
  return bridgeInitializationPromise;
}

function initializeAfterTerms() {
  if (!applicationInitializationPromise) {
    applicationInitializationPromise = (async () => {
      try {
        // 1. Verificação de Licenciamento por Hardware (Node-Locking)
        await appLicense.checkActivation();

        const info = await window.pywebview.api.get_app_info();
        state.defaultOutputDir = info.default_output_dir;
        state.defaultOutputDirId = info.default_output_dir_id;
        state.outputDir = info.default_output_dir;
        state.outputDirId = info.default_output_dir_id;
        state.maxChunkCharacters = info.default_chunk_limit;

        if (info.app_version) {
          const badge = document.getElementById("appVersionBadge");
          if (badge) {
            badge.innerText = `MOTOR NATIVO LOCAL • v${info.app_version} • OCR ONNX INTEGRADO • SEM TESSERACT EXTERNO`;
          }
        }

        document.getElementById("inputOutputDir").value = state.outputDir;
        document.getElementById("inputMaxChars").value = state.maxChunkCharacters;

        superPdf.updateDocumentDropdown();

        // Validação de runtime
        const envCheck = await window.pywebview.api.validate_environment();
        if (!envCheck.ok) {
          showToast(envCheck.error, "error");
          appendLog("ERRO", envCheck.error);
        }
        if (appLicense.isActivated) await offerInterruptedConversion();
      } catch (error) {
        console.error("Erro ao inicializar após os termos:", error);
      }
    })();
  }
  return applicationInitializationPromise;
}

// --------------------------------------------------------------------------
// Feedback Sonoro com Web Audio API
// --------------------------------------------------------------------------
let feedbackAudioContext = null;
let feedbackAudioSuspendTimer = null;
let feedbackAudioSuspendPromise = null;
const FEEDBACK_AUDIO_IDLE_MS = 1500;

function suspendFeedbackAudio() {
  if (feedbackAudioSuspendTimer) {
    clearTimeout(feedbackAudioSuspendTimer);
    feedbackAudioSuspendTimer = null;
  }
  if (feedbackAudioContext?.state === "running") {
    if (!feedbackAudioSuspendPromise) {
      const suspension = Promise.resolve(feedbackAudioContext.suspend())
        .catch(() => {})
      const tracked = suspension.finally(() => {
        if (feedbackAudioSuspendPromise === tracked) feedbackAudioSuspendPromise = null;
      });
      feedbackAudioSuspendPromise = tracked;
    }
  }
}

function scheduleFeedbackAudioSuspend() {
  if (feedbackAudioSuspendTimer) clearTimeout(feedbackAudioSuspendTimer);
  feedbackAudioSuspendTimer = setTimeout(() => {
    feedbackAudioSuspendTimer = null;
    suspendFeedbackAudio();
  }, FEEDBACK_AUDIO_IDLE_MS);
}

function playBeep(type = "click") {
  if (!state.soundEnabled) return;
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;
    if (!feedbackAudioContext || feedbackAudioContext.state === "closed") {
      feedbackAudioContext = new AudioContext();
    }
    const ctx = feedbackAudioContext;
    const resumeIfNeeded = () => {
      if (feedbackAudioContext === ctx && ctx.state === "suspended") {
        return ctx.resume().catch(() => {});
      }
      return null;
    };
    if (feedbackAudioSuspendPromise) feedbackAudioSuspendPromise.finally(resumeIfNeeded).catch(() => {});
    else resumeIfNeeded();
    if (feedbackAudioSuspendTimer) {
      clearTimeout(feedbackAudioSuspendTimer);
      feedbackAudioSuspendTimer = null;
    }
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.onended = () => {
      osc.disconnect();
      gain.disconnect();
      scheduleFeedbackAudioSuspend();
    };

    if (type === "click") {
      osc.frequency.setValueAtTime(520, ctx.currentTime);
      gain.gain.setValueAtTime(0.04, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.08);
      osc.start();
      osc.stop(ctx.currentTime + 0.08);
    } else if (type === "success") {
      osc.frequency.setValueAtTime(580, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(880, ctx.currentTime + 0.15);
      gain.gain.setValueAtTime(0.06, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.2);
      osc.start();
      osc.stop(ctx.currentTime + 0.2);
    } else if (type === "error") {
      osc.frequency.setValueAtTime(260, ctx.currentTime);
      osc.frequency.setValueAtTime(180, ctx.currentTime + 0.08);
      gain.gain.setValueAtTime(0.08, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
      osc.start();
      osc.stop(ctx.currentTime + 0.25);
    }
  } catch (e) {
    // Silencioso se bloqueado
  }
}

function initAudioPreference() {
  const saved = localStorage.getItem("nexojuris_sound_enabled") ?? localStorage.getItem("boni_sound_enabled");
  if (saved !== null) {
    state.soundEnabled = saved === "true";
  }
  updateAudioButtonUI();
}

function toggleAudioFeedback() {
  state.soundEnabled = !state.soundEnabled;
  localStorage.setItem("nexojuris_sound_enabled", state.soundEnabled);
  updateAudioButtonUI();
  if (state.soundEnabled) playBeep("success");
  else suspendFeedbackAudio();
}

function updateAudioButtonUI() {
  const label = document.getElementById("audioLabel");
  const icon = document.getElementById("audioIcon");
  if (state.soundEnabled) {
    label.innerText = "Som Ativo";
    icon.classList.remove("text-slate-400");
    icon.classList.add("text-sky-600");
  } else {
    label.innerText = "Mudo";
    icon.classList.remove("text-sky-600");
    icon.classList.add("text-slate-400");
  }
}

// --------------------------------------------------------------------------
// Notificações Toast
// --------------------------------------------------------------------------
function showToast(message, type = "info") {
  const toast = document.getElementById("toastNotification");
  const msgEl = document.getElementById("toastMsg");
  const iconEl = document.getElementById("toastIcon");
  if (!toast || !msgEl) return;

  msgEl.innerText = message;
  
  if (type === "error") {
    iconEl.className = "w-6 h-6 rounded-full bg-rose-100 text-rose-600 flex items-center justify-center flex-shrink-0";
    iconEl.innerHTML = `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12"></path></svg>`;
  } else if (type === "success") {
    iconEl.className = "w-6 h-6 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center flex-shrink-0";
    iconEl.innerHTML = `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"></path></svg>`;
  } else {
    iconEl.className = "w-6 h-6 rounded-full bg-brand-100 text-brand-600 flex items-center justify-center flex-shrink-0";
    iconEl.innerHTML = `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>`;
  }

  toast.classList.remove("opacity-0", "translate-y-20", "pointer-events-none");
  setTimeout(() => {
    toast.classList.add("opacity-0", "translate-y-20", "pointer-events-none");
  }, 3200);
}

// --------------------------------------------------------------------------
// Navegação de Abas
// --------------------------------------------------------------------------
function switchView(viewName) {
  playBeep("click");
  const views = ["converter", "superpdf", "preview", "logs"];
  views.forEach((v) => {
    const el = document.getElementById(`view-${v}`);
    const tab = document.getElementById(`tab-${v}`);
    if (v === viewName) {
      if (el) el.classList.remove("hidden");
      if (tab) {
        tab.classList.add("active", "bg-white", "text-sky-900", "shadow-sm", "font-semibold");
        tab.classList.remove("text-slate-600");
      }
    } else {
      if (el) el.classList.add("hidden");
      if (tab) {
        tab.classList.remove("active", "bg-white", "text-sky-900", "shadow-sm", "font-semibold");
        tab.classList.add("text-slate-600");
      }
    }
  });

  if (viewName === "superpdf" && window.superPdf) {
    if (!superPdf.currentFilePath && state.files.length > 0) {
      superPdf.loadDocument(state.files[0].file_id, null, state.files[0].path);
    }
  }
}

// --------------------------------------------------------------------------
// Drag & Drop
// --------------------------------------------------------------------------
function setupDragAndDrop() {
  const dropZone = document.getElementById("dropZone");

  ["dragenter", "dragover"].forEach((eventName) => {
    window.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropZone.classList.add("dragover");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    window.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropZone.classList.remove("dragover");
    });
  });

  window.addEventListener("drop", async (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropZone.classList.remove("dragover");

    const files = e.dataTransfer ? e.dataTransfer.files : [];
    if (!files || files.length === 0) return;

    const filePaths = [];
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      if (file.path) {
        filePaths.push(file.path);
      }
    }

    if (filePaths.length > 0 && window.pywebview && window.pywebview.api) {
      const processed = await window.pywebview.api.register_dropped_files(filePaths);
      addProcessedFiles(processed);
    }
  });
}

// --------------------------------------------------------------------------
// Gerenciamento de Arquivos
// --------------------------------------------------------------------------
async function triggerFileSelect() {
  if (state.isConverting) return;
  playBeep("click");
  if (!window.pywebview || !window.pywebview.api) {
    showToast("Aguardando carregamento da interface...", "info");
    return;
  }
  const selected = await window.pywebview.api.choose_files();
  addProcessedFiles(selected);
}

function addProcessedFiles(newFiles) {
  if (!newFiles || newFiles.length === 0) return;
  const existingIds = new Set(state.files.map((f) => f.file_id));
  let addedCount = 0;

  for (const item of newFiles) {
    if (item.file_id && !existingIds.has(item.file_id)) {
      state.files.push({
        file_id: item.file_id,
        path: item.path,
        name: item.name,
        size: item.size,
        size_formatted: item.size_formatted,
        status: "pending", // "pending" | "converting" | "success" | "error"
        markdown_path: "",
        markdown_id: "",
        asset_count: 0,
        chunk_count: 0,
        failed_pages: [],
        warning_pages: [],
        duration: "",
        error_message: ""
      });
      existingIds.add(item.file_id);
      addedCount++;
    }
  }

  if (addedCount > 0) {
    playBeep("success");
    showToast(`${addedCount} PDF(s) adicionado(s) à fila.`, "success");
    renderFileList();
    updateMetrics();
    if (window.superPdf) {
      superPdf.updateDocumentDropdown();
    }
  }
}

function openInSuperPdf(fileId, displayPath = "") {
  if (window.superPdf) {
    superPdf.loadDocument(fileId, null, displayPath);
    switchView("superpdf");
  }
}

function openQueuedPdf(index) {
  const file = state.files[index];
  if (file) openInSuperPdf(file.file_id, file.path);
}

function previewQueuedMarkdown(index) {
  const file = state.files[index];
  if (file?.markdown_id) previewSpecificMarkdown(file.markdown_id, file.markdown_path);
}

function openQueuedMarkdown(index) {
  const file = state.files[index];
  if (file?.markdown_id) openMarkdownDirectly(file.markdown_id);
}

function removeFile(index) {
  if (state.isConverting) return;
  playBeep("click");
  state.files.splice(index, 1);
  renderFileList();
  updateMetrics();
  if (window.superPdf) {
    superPdf.updateDocumentDropdown();
  }
}

function clearAllFiles() {
  if (state.isConverting) return;
  playBeep("click");
  state.files = [];
  renderFileList();
  updateMetrics();
  if (window.superPdf) {
    superPdf.updateDocumentDropdown();
  }
}

function renderFileList() {
  const container = document.getElementById("fileListContainer");
  const countBadge = document.getElementById("fileBadgeCount");
  const btnClear = document.getElementById("btnClearFiles");

  countBadge.innerText = state.files.length;
  btnClear.disabled = state.files.length === 0;

  if (state.files.length === 0) {
    container.innerHTML = `
      <div id="fileListEmpty" class="py-10 text-center space-y-2 text-slate-400">
        <svg class="w-10 h-10 mx-auto text-sky-200" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
        <p class="text-xs font-medium">Nenhum arquivo adicionado ainda.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = state.files.map((file, idx) => {
    let statusBadge = `<span class="px-2.5 py-0.5 rounded-lg text-[10.5px] font-semibold bg-slate-100 text-slate-600">Pendente</span>`;
    let actionButtons = `
      <button data-action="openQueuedPdf(${idx})" class="p-1.5 rounded-lg text-sky-600 hover:bg-sky-50 transition" title="Abrir no Super PDF">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"></path></svg>
      </button>
      <button data-action="removeFile(${idx})" class="p-1.5 rounded-lg text-slate-400 hover:text-rose-600 hover:bg-rose-50 transition" title="Remover da lista">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
      </button>
    `;

    if (file.status === "converting") {
      statusBadge = `<span class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-lg text-[10.5px] font-semibold bg-sky-100 text-sky-800 animate-pulse"><span class="w-1.5 h-1.5 rounded-full bg-sky-600"></span>Convertendo...</span>`;
      actionButtons = ``;
    } else if (file.status === "success") {
      const problemCount = file.failed_pages?.length || 0;
      statusBadge = problemCount
        ? `<span class="px-2.5 py-0.5 rounded-lg text-[10.5px] font-semibold bg-amber-100 text-amber-800" title="Páginas: ${file.failed_pages.join(", ")}">Concluído com ${problemCount} falha(s)</span>`
        : `<span class="px-2.5 py-0.5 rounded-lg text-[10.5px] font-semibold bg-emerald-100 text-emerald-800">Concluído (${file.duration})</span>`;
      actionButtons = `
        <button data-action="openQueuedPdf(${idx})" class="p-1.5 rounded-lg text-sky-600 hover:bg-sky-50 transition" title="Abrir no Super PDF">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"></path></svg>
        </button>
        <button data-action="previewQueuedMarkdown(${idx})" class="p-1.5 rounded-lg text-sky-600 hover:bg-sky-50 transition" title="Visualizar Markdown">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"></path></svg>
        </button>
        <button data-action="openQueuedMarkdown(${idx})" class="p-1.5 rounded-lg text-sky-600 hover:bg-sky-50 transition" title="Abrir no Editor do Windows">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
        </button>
        ${problemCount ? `<button data-action="retryFailedPages(${idx})" class="px-2 py-1 rounded-lg text-amber-800 bg-amber-100 hover:bg-amber-200 transition font-semibold" title="Reprocessar somente as páginas falhas">Reprocessar</button>` : ""}
      `;
    } else if (file.status === "error") {
      statusBadge = `<span class="px-2.5 py-0.5 rounded-lg text-[10.5px] font-semibold bg-rose-100 text-rose-800" title="${escapeHtml(file.error_message)}">Falhou</span>`;
    }

    return `
      <div class="glass-card rounded-2xl p-3.5 border-white/90 flex items-center justify-between gap-3 text-xs">
        <div class="flex items-center gap-3 min-w-0 flex-1">
          <div class="w-8 h-8 rounded-xl bg-sky-100 text-sky-700 flex items-center justify-center flex-shrink-0 font-bold font-mono">
            PDF
          </div>
          <div class="min-w-0 flex-1">
            <h4 class="font-bold text-slate-800 truncate" title="${escapeHtml(file.path)}">${escapeHtml(file.name)}</h4>
            <span class="text-[11px] font-mono text-slate-400">${escapeHtml(file.size_formatted)}</span>
          </div>
        </div>
        
        <div class="flex items-center gap-2 flex-shrink-0">
          ${statusBadge}
          ${actionButtons}
        </div>
      </div>
    `;
  }).join("");
}

function updateMetrics() {
  document.getElementById("statQueueCount").innerText = state.files.length;
  const successCount = state.files.filter((f) => f.status === "success").length;
  document.getElementById("statConvertedCount").innerText = successCount;
}

// --------------------------------------------------------------------------
// Configurações e Pasta de Saída
// --------------------------------------------------------------------------
async function triggerSelectOutputDir() {
  if (state.isConverting) return;
  playBeep("click");
  if (!window.pywebview || !window.pywebview.api) return;
  const selected = await window.pywebview.api.choose_output_directory();
  if (selected) {
    state.outputDir = selected.path;
    state.outputDirId = selected.directory_id;
    document.getElementById("inputOutputDir").value = selected.path;
    showToast("Pasta de destino alterada.", "info");
  }
}

function resetOutputDirToDefault() {
  if (state.isConverting) return;
  playBeep("click");
  state.outputDir = state.defaultOutputDir;
  state.outputDirId = state.defaultOutputDirId;
  document.getElementById("inputOutputDir").value = state.outputDir;
  showToast("Pasta de destino restaurada para o padrão.", "info");
}

function toggleAdvancedOptions() {
  playBeep("click");
  const body = document.getElementById("advancedOptionsBody");
  const chevron = document.getElementById("btnAdvancedChevron");
  if (body.classList.contains("hidden")) {
    body.classList.remove("hidden");
    chevron.classList.add("rotate-180");
  } else {
    body.classList.add("hidden");
    chevron.classList.remove("rotate-180");
  }
}

function selectProfile(profile) {
  if (state.isConverting) return;
  playBeep("click");
  state.selectedProfile = profile;

  const cardJur = document.getElementById("profileCardJurisprudencia");
  const cardCur = document.getElementById("profileCardCurso");
  const dotJur = document.getElementById("dotJurisprudencia");
  const dotCur = document.getElementById("dotCurso");

  if (profile === "jurisprudencia") {
    cardJur.className = "p-3 rounded-2xl border cursor-pointer transition-all bg-sky-50/80 border-sky-400 shadow-sm space-y-1";
    dotJur.className = "w-2 h-2 rounded-full bg-sky-600";
    cardCur.className = "p-3 rounded-2xl border cursor-pointer transition-all bg-white/70 border-sky-100 hover:bg-white space-y-1";
    dotCur.className = "w-2 h-2 rounded-full bg-slate-300";
  } else {
    cardCur.className = "p-3 rounded-2xl border cursor-pointer transition-all bg-sky-50/80 border-sky-400 shadow-sm space-y-1";
    dotCur.className = "w-2 h-2 rounded-full bg-sky-600";
    cardJur.className = "p-3 rounded-2xl border cursor-pointer transition-all bg-white/70 border-sky-100 hover:bg-white space-y-1";
    dotJur.className = "w-2 h-2 rounded-full bg-slate-300";
  }
}

// --------------------------------------------------------------------------
// Controle da Conversão
// --------------------------------------------------------------------------
async function offerInterruptedConversion() {
  if (typeof window.pywebview?.api?.get_interrupted_conversion !== "function") return;
  const recovery = await window.pywebview.api.get_interrupted_conversion();
  if (!recovery?.available) return;
  if (!recovery.can_resume) {
    window.alert(
      `A fila interrompida não pode ser retomada porque os PDFs foram removidos ou alterados: ${recovery.missing_files.join(", ")}. O checkpoint será descartado.`
    );
    await window.pywebview.api.discard_interrupted_conversion(recovery.resume_token);
    return;
  }
  const names = recovery.files.map((file) => `• ${file.name}`).join("\n");
  const missing = recovery.missing_files?.length
    ? `\n\nIndisponíveis e não retomados: ${recovery.missing_files.join(", ")}`
    : "";
  const confirmed = window.confirm(
    `Uma conversão anterior foi interrompida. Deseja retomá-la do último checkpoint salvo?\n\n${names}${missing}`
  );
  if (!confirmed) {
    await window.pywebview.api.discard_interrupted_conversion(recovery.resume_token);
    appendLog("INFO", "Checkpoint da conversão anterior descartado pelo usuário.");
    return;
  }

  state.files = [];
  addProcessedFiles(recovery.files);
  state.outputDir = recovery.output_dir;
  state.outputDirId = recovery.output_directory_id;
  state.splitOutput = !!recovery.split_output;
  state.splitMode = recovery.split_mode || "semantic";
  state.maxChunkCharacters = recovery.max_chunk_characters || 60000;
  state.selectedProfile = recovery.heading_profile || "jurisprudencia";
  document.getElementById("inputOutputDir").value = state.outputDir;
  document.getElementById("chkSplitOutput").checked = state.splitOutput;
  document.getElementById("selectSplitMode").value = state.splitMode;
  document.getElementById("inputMaxChars").value = state.maxChunkCharacters;
  selectProfile(state.selectedProfile);

  const response = await window.pywebview.api.resume_interrupted_conversion(recovery.resume_token);
  if (!response.started) {
    showToast(response.error || "Não foi possível retomar a conversão.", "error");
    return;
  }
  state.isConverting = true;
  state.isPaused = false;
  state.startTime = performance.now();
  progressController.startConversion();
  startTimer();
  updateControlsState();
  appendLog("INFO", `Conversão retomada com ${state.files.length} arquivo(s) pendente(s).`);
}

async function startConversion() {
  if (state.isConverting) return;
  if (state.files.length === 0) {
    playBeep("error");
    showToast("Adicione pelo menos um PDF para converter.", "error");
    return;
  }

  playBeep("click");
  state.splitOutput = document.getElementById("chkSplitOutput").checked;
  state.splitMode = document.getElementById("selectSplitMode").value || "semantic";
  state.maxChunkCharacters = parseInt(document.getElementById("inputMaxChars").value) || 60000;

  // Reset status of files
  state.files.forEach((f) => (f.status = "pending"));
  renderFileList();

  const payload = {
    files: state.files.map((f) => ({ file_id: f.file_id })),
    output_directory_id: state.outputDirId,
    split_output: state.splitOutput,
    split_mode: state.splitMode,
    max_chunk_characters: state.maxChunkCharacters,
    heading_profile: state.selectedProfile
  };

  const response = await window.pywebview.api.start_conversion(payload);
  if (!response.started) {
    playBeep("error");
    showToast(response.error, "error");
    if (response.error_code === "license_required") {
      appLicense.isActivated = false;
      appLicense.machineId = response.machine_id || "";
      appLicense.openModal();
    }
    return;
  }

  state.isConverting = true;
  state.isPaused = false;
  state.startTime = performance.now();
  progressController.startConversion();
  startTimer();
  updateControlsState();
  appendLog("INFO", `Iniciando conversão de ${state.files.length} arquivo(s)...`);
}

async function retryFailedPages(index) {
  if (state.isConverting) return;
  const file = state.files[index];
  if (!file?.file_id || !file.failed_pages?.length) return;
  const response = await window.pywebview.api.retry_failed_pages({
    file_id: file.file_id,
    page_numbers: file.failed_pages,
    output_directory_id: state.outputDirId,
    split_output: state.splitOutput,
    split_mode: state.splitMode,
    max_chunk_characters: state.maxChunkCharacters,
    heading_profile: state.selectedProfile,
  });
  if (!response.started) {
    showToast(response.error || "Não foi possível reprocessar as páginas.", "error");
    return;
  }
  state.isConverting = true;
  state.startTime = performance.now();
  file.status = "converting";
  renderFileList();
  progressController.startConversion();
  startTimer();
  updateControlsState();
  appendLog("INFO", `Reprocessando páginas ${file.failed_pages.join(", ")} de ${file.name}.`);
}

async function togglePause() {
  if (!state.isConverting) return;
  playBeep("click");
  const res = await window.pywebview.api.toggle_pause();
  state.isPaused = res.is_paused;
  document.getElementById("btnPauseConvert").innerText = state.isPaused ? "Retomar" : "Pausar";
}

async function requestStop() {
  if (!state.isConverting) return;
  playBeep("click");
  await window.pywebview.api.request_stop();
  document.getElementById("btnStopConvert").disabled = true;
  document.getElementById("btnPauseConvert").disabled = true;
}

function updateControlsState() {
  document.getElementById("btnStartConvert").disabled = state.isConverting;
  document.getElementById("btnPauseConvert").disabled = !state.isConverting;
  document.getElementById("btnStopConvert").disabled = !state.isConverting;
  document.getElementById("btnPauseConvert").innerText = "Pausar";
}

function startTimer() {
  if (state.timerInterval) clearInterval(state.timerInterval);
  state.timerInterval = setInterval(() => {
    if (!state.isConverting) {
      clearInterval(state.timerInterval);
      return;
    }
    const elapsedSecs = Math.round((performance.now() - state.startTime) / 1000);
    const mins = Math.floor(elapsedSecs / 60);
    const secs = elapsedSecs % 60;
    document.getElementById("statDuration").innerText = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
  }, 1000);
}

// --------------------------------------------------------------------------
// Controlador da Barra de Progresso Deslizante em Tempo Real
// --------------------------------------------------------------------------
class ProgressBarController {
  constructor() {
    this.bar = document.getElementById("progressBar");
    this.label = document.getElementById("progressPercent");
    this.currentVal = 0;
    this.targetVal = 0;
    this.animId = null;
    this.crawlInterval = null;
  }

  reset() {
    this.stopCrawl();
    if (this.animId) cancelAnimationFrame(this.animId);
    this.currentVal = 0;
    this.targetVal = 0;
    if (this.bar) {
      this.bar.className = "h-full rounded-full progress-bar-idle";
      this.bar.style.width = "0%";
    }
    if (this.label) {
      this.label.innerText = "0%";
    }
  }

  startConversion() {
    this.reset();
    if (this.bar) {
      this.bar.className = "h-full rounded-full progress-bar-sliding";
    }
    this.startAnimationLoop();
  }

  onFileStart(index, total) {
    this.stopCrawl();
    if (total <= 0) return;
    const base = ((index - 1) / total) * 100;
    const ceiling = ((index - 0.05) / total) * 100;
    
    if (this.currentVal < base) {
      this.targetVal = base;
    }

    // Efeito deslizante contínuo em tempo real enquanto o PDF é processado
    this.crawlInterval = setInterval(() => {
      if (this.targetVal < ceiling) {
        const remaining = ceiling - this.targetVal;
        const step = Math.max(0.18, remaining * 0.06);
        this.targetVal = Math.min(ceiling, this.targetVal + step);
      }
    }, 100);
  }

  onProgress(completed, total) {
    if (total <= 0) return;
    const actual = (completed / total) * 100;
    this.targetVal = Math.max(this.targetVal, actual);
  }

  onFileDone(completed, total) {
    this.stopCrawl();
    if (total <= 0) return;
    const actual = (completed / total) * 100;
    this.targetVal = Math.max(this.targetVal, actual);
  }

  finishSuccess() {
    this.stopCrawl();
    this.targetVal = 100;
    if (this.bar) {
      this.bar.className = "h-full rounded-full progress-bar-success";
    }
    setTimeout(() => {
      if (!state.isConverting && this.bar) {
        this.bar.className = "h-full rounded-full progress-bar-idle";
      }
    }, 3500);
  }

  finishStopped() {
    this.stopCrawl();
    if (this.bar) {
      this.bar.className = "h-full rounded-full progress-bar-idle";
    }
  }

  stopCrawl() {
    if (this.crawlInterval) {
      clearInterval(this.crawlInterval);
      this.crawlInterval = null;
    }
  }

  startAnimationLoop() {
    if (this.animId) cancelAnimationFrame(this.animId);
    const tick = () => {
      const diff = this.targetVal - this.currentVal;
      if (Math.abs(diff) > 0.02) {
        this.currentVal += diff * 0.16;
      } else {
        this.currentVal = this.targetVal;
      }

      const clamped = Math.min(100, Math.max(0, this.currentVal));
      if (this.bar) {
        this.bar.style.width = `${clamped.toFixed(1)}%`;
      }
      if (this.label) {
        this.label.innerText = `${Math.round(clamped)}%`;
      }

      if (state.isConverting || Math.abs(this.targetVal - this.currentVal) > 0.05) {
        this.animId = requestAnimationFrame(tick);
      } else {
        if (this.bar) this.bar.style.width = `${this.targetVal}%`;
        if (this.label) this.label.innerText = `${Math.round(this.targetVal)}%`;
      }
    };
    this.animId = requestAnimationFrame(tick);
  }
}

const progressController = new ProgressBarController();


async function togglePause() {
  if (!state.isConverting) return;
  playBeep("click");
  const res = await window.pywebview.api.toggle_pause();
  state.isPaused = res.is_paused;
  document.getElementById("btnPauseConvert").innerText = state.isPaused ? "Retomar" : "Pausar";
}

async function requestStop() {
  if (!state.isConverting) return;
  playBeep("click");
  await window.pywebview.api.request_stop();
  document.getElementById("btnStopConvert").disabled = true;
  document.getElementById("btnPauseConvert").disabled = true;
}

function updateControlsState() {
  document.getElementById("btnStartConvert").disabled = state.isConverting;
  document.getElementById("btnPauseConvert").disabled = !state.isConverting;
  document.getElementById("btnStopConvert").disabled = !state.isConverting;
  document.getElementById("btnPauseConvert").innerText = "Pausar";
}

function startTimer() {
  if (state.timerInterval) clearInterval(state.timerInterval);
  state.timerInterval = setInterval(() => {
    if (!state.isConverting) {
      clearInterval(state.timerInterval);
      return;
    }
    const elapsedSecs = Math.round((performance.now() - state.startTime) / 1000);
    const mins = Math.floor(elapsedSecs / 60);
    const secs = elapsedSecs % 60;
    document.getElementById("statDuration").innerText = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
  }, 1000);
}






// --------------------------------------------------------------------------
// Eventos Emitidos pelo Backend Python
// --------------------------------------------------------------------------
window.onBackendEvent = function (eventName, data) {
  if (eventName === "indexing_progress") {
    if (window.superPdf) superPdf.onIndexingProgress(data);
  } else if (eventName === "indexing_completed") {
    if (window.superPdf) superPdf.onIndexingCompleted(data);
  } else if (eventName === "indexing_cancelled") {
    if (window.superPdf) superPdf.onIndexingCancelled(data);
  } else if (eventName === "indexing_error") {
    if (window.superPdf) superPdf.onIndexingError(data);
  } else if (eventName === "status") {
    document.getElementById("statusMessage").innerText = data.message;
  } else if (eventName === "file_start") {
    const file = state.files.find((f) => f.file_id === data.file_id);
    if (file) {
      file.status = "converting";
      renderFileList();
    }
    progressController.onFileStart(data.index || 1, data.total || state.files.length);
  } else if (eventName === "progress") {
    progressController.onProgress(data.completed || 0, data.total || state.files.length);
  } else if (eventName === "file_success") {
    playBeep("success");
    const file = state.files.find((f) => f.file_id === data.source_id);
    if (file) {
      file.status = "success";
      file.markdown_path = data.markdown_path;
      file.markdown_id = data.markdown_id;
      file.duration = data.duration_formatted;
      file.asset_count = data.asset_count;
      file.chunk_count = data.chunk_count;
      file.failed_pages = data.failed_pages || [];
      file.warning_pages = data.warning_pages || [];
    }
    state.convertedResults.push(data);
    updatePreviewDropdown();
    document.getElementById("btnOpenLastMd").disabled = false;
    renderFileList();
    updateMetrics();
    
    const completedCount = state.files.filter((f) => f.status === "success" || f.status === "error").length;
    progressController.onFileDone(completedCount, state.files.length);
    const pageWarning = data.failed_pages?.length ? `; páginas não recuperadas: ${data.failed_pages.join(", ")}` : "";
    appendLog("OK", `${data.name} -> ${data.markdown_path} (${data.asset_count} imgs, ${data.duration_formatted}${pageWarning})`);
  } else if (eventName === "file_error") {
    playBeep("error");
    const file = state.files.find((f) => f.file_id === data.source_id);
    if (file) {
      file.status = "error";
      file.error_message = data.error_message || "Falha não detalhada pelo conversor.";
    }
    renderFileList();
    updateMetrics();
    const completedCount = state.files.filter((f) => f.status === "success" || f.status === "error").length;
    progressController.onFileDone(completedCount, state.files.length);
    appendLog("ERRO", `${data.name}: ${data.error_message || "Falha não detalhada pelo conversor."}`);
  } else if (eventName === "batch_done") {
    playBeep("success");
    state.isConverting = false;
    updateControlsState();
    clearInterval(state.timerInterval);
    progressController.finishSuccess();
    const pageWarning = data.problem_page_count ? `, ${data.problem_page_count} página(s) não recuperada(s)` : "";
    document.getElementById("statusMessage").innerText = `Concluído: ${data.success_count} convertido(s), ${data.failure_count} com erro${pageWarning}.`;
    showToast(`Conversão finalizada em ${data.elapsed_formatted}${pageWarning}!`, data.problem_page_count ? "info" : "success");
    appendLog("INFO", `Lote concluído em ${data.elapsed_formatted}. Arquivos salvos em: ${data.output_dir}`);
    for (const item of data.problem_pages || []) {
      appendLog("AVISO", `${item.name}: páginas não recuperadas ${item.pages.join(", ")}.`);
    }
  } else if (eventName === "batch_stopped") {
    state.isConverting = false;
    updateControlsState();
    clearInterval(state.timerInterval);
    progressController.finishStopped();
    document.getElementById("statusMessage").innerText = `Fila interrompida: ${data.success_count} convertido(s).`;
    showToast("Fila de conversão interrompida pelo usuário.", "info");
    appendLog("AVISO", "Fila interrompida pelo usuário.");
  } else if (eventName === "batch_error") {
    playBeep("error");
    state.isConverting = false;
    updateControlsState();
    clearInterval(state.timerInterval);
    progressController.finishStopped();
    showToast(`Erro na execução do lote: ${data.error_message}`, "error");
    appendLog("ERRO", `${data.error_message}\n${data.details}`);
  } else if (eventName === "toast") {
    showToast(data.message, data.type || "info");
  }
};

// --------------------------------------------------------------------------
// Visualizador e Parser de Markdown
// --------------------------------------------------------------------------
function updatePreviewDropdown() {
  const select = document.getElementById("previewDocSelect");
  if (state.convertedResults.length === 0) {
    select.innerHTML = `<option value="">Nenhum documento convertido</option>`;
    return;
  }

  select.innerHTML = state.convertedResults.map(
    (item) => `<option value="${escapeHtml(item.markdown_id)}">${escapeHtml(item.name)}</option>`
  ).join("");

  if (!state.currentPreviewId && state.convertedResults.length > 0) {
    const last = state.convertedResults[state.convertedResults.length - 1];
    previewSpecificMarkdown(last.markdown_id, last.markdown_path);
  }
}

async function previewSpecificMarkdown(markdownId, displayPath = "") {
  state.currentPreviewId = markdownId;
  state.currentPreviewPath = displayPath;
  switchView("preview");
  
  const select = document.getElementById("previewDocSelect");
  select.value = markdownId;

  const res = await window.pywebview.api.read_markdown_preview(markdownId);
  const container = document.getElementById("previewRenderArea");
  const subtitle = document.getElementById("previewSubtitle");
  const btnOpen = document.getElementById("btnOpenInApp");
  const btnCopy = document.getElementById("btnCopyPreview");

  if (!res.ok) {
    container.replaceChildren();
    const errorMessage = document.createElement("div");
    errorMessage.className = "p-4 text-rose-600";
    errorMessage.textContent = res.error || "Falha ao ler Markdown.";
    container.appendChild(errorMessage);
    btnOpen.disabled = true;
    btnCopy.disabled = true;
    return;
  }

  subtitle.innerText = `${res.name} • ${res.size_formatted}`;
  btnOpen.disabled = false;
  btnCopy.disabled = false;

  container.innerHTML = renderMarkdownToHtml(res.content);
  state.currentPreviewPath = res.path;
  await hydrateMarkdownAssets(container, markdownId);
}

function handlePreviewDocChange() {
  const select = document.getElementById("previewDocSelect");
  if (select.value) {
    const item = state.convertedResults.find((result) => result.markdown_id === select.value);
    previewSpecificMarkdown(select.value, item?.markdown_path || "");
  }
}

function openCurrentPreviewFile() {
  if (!state.currentPreviewId) return;
  openMarkdownDirectly(state.currentPreviewId);
}

async function copyCurrentPreviewContent() {
  if (!state.currentPreviewId) return;
  const res = await window.pywebview.api.read_markdown_preview(state.currentPreviewId);
  if (res.ok) {
    navigator.clipboard.writeText(res.content);
    playBeep("success");
    showToast("Conteúdo Markdown copiado!", "success");
  }
}

function openLastMarkdownResult() {
  if (state.convertedResults.length === 0) return;
  const last = state.convertedResults[state.convertedResults.length - 1];
  openMarkdownDirectly(last.markdown_id);
}

async function openMarkdownDirectly(markdownId) {
  playBeep("click");
  await window.pywebview.api.open_markdown(markdownId);
}

async function openCurrentOutputFolder() {
  playBeep("click");
  await window.pywebview.api.open_folder(state.outputDirId);
}

let markdownSanitizerConfigured = false;

function isSafeHttpUrl(value) {
  const raw = String(value || "").trim();
  if (!/^https?:\/\//i.test(raw) || /[\u0000-\u001f\u007f]/.test(raw)) return false;
  try {
    const parsed = new URL(raw);
    return ["http:", "https:"].includes(parsed.protocol) && Boolean(parsed.hostname) && !parsed.username && !parsed.password;
  } catch (_error) {
    return false;
  }
}

function isSafeRelativeAssetRef(value) {
  let decoded = String(value || "").trim();
  if (!decoded || decoded.startsWith("/") || decoded.startsWith("\\") || decoded.includes("\\")) return false;
  if (decoded.includes("?") || decoded.includes("#") || /^[A-Za-z][A-Za-z0-9+.-]*:/.test(decoded)) return false;
  try {
    for (let i = 0; i < 3; i += 1) {
      const next = decodeURIComponent(decoded);
      if (next === decoded) break;
      decoded = next;
    }
  } catch (_error) {
    return false;
  }
  const parts = decoded.split("/");
  return !parts.some((part) => part === ".." || part === "." || part === "");
}

function configureMarkdownSanitizer() {
  if (markdownSanitizerConfigured) return;
  if (!window.DOMPurify || !window.marked) throw new Error("Renderizador Markdown local indisponível.");
  window.DOMPurify.addHook("uponSanitizeAttribute", (node, data) => {
    const tagName = String(node.tagName || "").toLowerCase();
    if (tagName === "a" && data.attrName === "href" && !isSafeHttpUrl(data.attrValue)) data.keepAttr = false;
    if (tagName === "img" && data.attrName === "src" && !isSafeRelativeAssetRef(data.attrValue)) data.keepAttr = false;
  });
  window.DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    const tagName = String(node.tagName || "").toLowerCase();
    if (tagName === "a" && node.hasAttribute("href")) {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
      node.className = "text-sky-600 underline";
    }
    if (tagName === "img" && node.hasAttribute("src")) {
      node.className = "max-w-full rounded-xl border border-sky-100 shadow-sm my-3";
    }
  });
  markdownSanitizerConfigured = true;
}

function renderMarkdownToHtml(markdown) {
  if (!markdown) return "";
  configureMarkdownSanitizer();
  const parsed = window.marked.parse(String(markdown), { gfm: true, breaks: false, async: false });
  return window.DOMPurify.sanitize(parsed, {
    ALLOWED_TAGS: [
      "h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "blockquote",
      "pre", "code", "table", "thead", "tbody", "tr", "th", "td", "hr", "br", "strong",
      "em", "del", "a", "img",
    ],
    ALLOWED_ATTR: ["href", "title", "src", "alt"],
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    FORBID_TAGS: ["script", "style", "svg", "math", "iframe", "object", "embed", "form", "input", "button", "video", "audio"],
  });
}

async function hydrateMarkdownAssets(container, markdownId) {
  if (!container || !markdownId || !window.pywebview?.api) return;
  const images = Array.from(container.querySelectorAll("img[src]")).slice(0, 200);
  for (let offset = 0; offset < images.length; offset += 8) {
    const batch = images.slice(offset, offset + 8);
    await Promise.all(batch.map(async (image) => {
      const relativeRef = image.getAttribute("src");
      image.removeAttribute("src");
      if (!isSafeRelativeAssetRef(relativeRef)) return;
      const result = await window.pywebview.api.read_markdown_asset(markdownId, relativeRef);
      if (result?.ok) image.src = result.data_uri;
    }));
  }
}

function setupMarkdownPreviewInteractions() {
  const container = document.getElementById("previewRenderArea");
  if (!container || container.dataset.externalLinksReady === "true") return;
  container.dataset.externalLinksReady = "true";
  container.addEventListener("click", (event) => {
    const link = event.target.closest?.("a[href]");
    if (!link || !isSafeHttpUrl(link.href)) return;
    event.preventDefault();
    void window.pywebview?.api?.open_external_url(link.href);
  });
}

// --------------------------------------------------------------------------
// Console de Logs
// --------------------------------------------------------------------------
function appendLog(type, message) {
  const consoleEl = document.getElementById("logConsole");
  if (!consoleEl) return;
  const timeStr = new Date().toLocaleTimeString("pt-BR");
  
  let badgeColor = "text-sky-600";
  if (type === "OK") badgeColor = "text-emerald-600";
  if (type === "ERRO") badgeColor = "text-rose-600 font-bold";
  if (type === "AVISO") badgeColor = "text-amber-600";

  const entry = document.createElement("div");
  entry.className = "flex items-start gap-2 text-[11px] leading-relaxed border-b border-sky-50 pb-1";
  const timeEl = document.createElement("span");
  timeEl.className = "text-slate-400 flex-shrink-0";
  timeEl.textContent = `[${timeStr}]`;
  const typeEl = document.createElement("span");
  typeEl.className = `${badgeColor} flex-shrink-0 uppercase font-bold`;
  typeEl.textContent = `[${String(type ?? "INFO")}]`;
  const messageEl = document.createElement("span");
  messageEl.className = "text-slate-700 flex-1 break-words whitespace-pre-wrap";
  messageEl.textContent = String(message ?? "");
  entry.append(timeEl, typeEl, messageEl);
  
  // Remove placeholder inicial se existir
  if (consoleEl.children.length === 1 && consoleEl.children[0].innerText.includes("Aguardando")) {
    consoleEl.innerHTML = "";
  }

  consoleEl.appendChild(entry);
  consoleEl.scrollTop = consoleEl.scrollHeight;
}

function clearActivityLogs() {
  playBeep("click");
  document.getElementById("logConsole").innerHTML = `<div class="text-slate-400 font-sans text-xs">Registro limpo.</div>`;
}

// --------------------------------------------------------------------------
// Segurança e Bloqueio de Inspeção
// --------------------------------------------------------------------------
document.addEventListener("contextmenu", (event) => {
  event.preventDefault();
});

document.addEventListener("keydown", (event) => {
  if (
    event.key === "F12" ||
    (event.ctrlKey && event.shiftKey && ["I", "i", "J", "j", "C", "c"].includes(event.key)) ||
    (event.ctrlKey && ["u", "U"].includes(event.key))
  ) {
    event.preventDefault();
  }
  if (event.key === "F1") {
    event.preventDefault();
    appManual.open();
  }
});

// ==========================================================================
// Cache LRU por Memória Estimada (Item 14)
// ==========================================================================
class LRUMemoryCache {
  constructor(maxMemoryBytes = 120 * 1024 * 1024) { // 120 MB padrão
    this.maxMemoryBytes = maxMemoryBytes;
    this.currentMemoryBytes = 0;
    this.cache = new Map();
  }

  get(key) {
    const entry = this.cache.get(key);
    if (!entry) return null;
    entry.lastAccessed = Date.now();
    return entry.data;
  }

  set(key, data, width = 800, height = 1100) {
    const memoryBytes = Math.round(width * height * 4);
    if (this.cache.has(key)) {
      const oldEntry = this.cache.get(key);
      this.currentMemoryBytes -= oldEntry.memoryBytes;
    }
    this.cache.set(key, { data, memoryBytes, lastAccessed: Date.now() });
    this.currentMemoryBytes += memoryBytes;
    this.evict();
  }

  evict() {
    while (this.currentMemoryBytes > this.maxMemoryBytes && this.cache.size > 1) {
      let oldestKey = null;
      let oldestTime = Infinity;
      for (const [key, entry] of this.cache.entries()) {
        if (entry.lastAccessed < oldestTime) {
          oldestTime = entry.lastAccessed;
          oldestKey = key;
        }
      }
      if (oldestKey !== null) {
        const entry = this.cache.get(oldestKey);
        this.currentMemoryBytes -= entry.memoryBytes;
        this.cache.delete(oldestKey);
      } else {
        break;
      }
    }
  }

  clear() {
    this.cache.clear();
    this.currentMemoryBytes = 0;
  }
}

// ==========================================================================
// Super PDF Controller (Leitor e Editor de PDF Integrado)
// ==========================================================================
class SuperPdfController {
  constructor() {
    this.currentFileId = null;
    this.currentFilePath = null;
    this.currentFileName = "";
    this.currentPage = 0;
    this.totalPages = 0;
    this.pageWidth = 595.0;
    this.pageHeight = 842.0;
    this.pageRotation = 0;
    this.currentTool = "pan"; // "pan" | "pen" | "highlight_pen" | "highlight_block" | "text" | "snippet"
    
    // Configurações de Caneta e Grifador
    this.penColor = "#0284c7";
    this.penWidth = 4;
    this.highlightColor = "#fde047";
    this.highlightPenWidth = 18;
    this.highlightMode = "pen"; // "pen" | "block"
    
    // Configurações de Texto Personalizado
    this.textColor = "#0f172a";
    this.textFontSize = 14;
    this.textBold = false;
    this.textBoxStyle = "none"; // "none" | "postit" | "white" | "danger"

    this.documentStates = new Map();
    this.activeDocumentState = null;
    this.annotations = new Map(); // pageIndex -> Array of annotation objects
    this.undoStack = [];
    this.isInteracting = false;
    this.currentStroke = [];
    this.startPoint = null;
    this.activeSnippetData = null;
    this.pageCache = new Map();
    this.lruCache = new LRUMemoryCache(120 * 1024 * 1024); // Limite de 120 MB em memória (Item 14)
    this.isRendering = false;
    this.isIndexing = false;
    this.indexingPercent = 0;
    this.indexingPaused = false;
    this.pendingPasswordFile = null;
    this.pendingPasswordFileId = null;
    this.isEncrypted = false;
    this.currentPassword = "";
    this.bookmarks = [];
    this.sidebarTab = "pages";
    this.saveStateTimeout = null;
    this.loadRequestId = 0;
    this.renderRequestId = 0;
    this.documentSwitchResolver = null;
    this.documentSwitchDecisionProvider = null;
    this.initialized = false;

    // 24 Cores no estilo Microsoft Edge PDF
    this.penPalette = [
      "#000000", "#4b5563", "#6b7280", "#9ca3af", "#d1d5db", "#ffffff",
      "#9f1239", "#dc2626", "#ea580c", "#d97706", "#eab308", "#facc15",
      "#84cc16", "#22c55e", "#059669", "#06b6d4", "#0284c7", "#4338ca",
      "#7c3aed", "#581c87", "#f43f5e", "#fb923c", "#fde047", "#a855f7"
    ];

    // 6 Cores Fluorescentes para Marca-texto
    this.hlPalette = [
      "#fde047", "#86efac", "#67e8f9", "#f472b6", "#ef4444", "#c084fc"
    ];

    // 6 Cores para Texto
    this.textPalette = [
      "#0f172a", "#0358a1", "#dc2626", "#16a34a", "#7c3aed", "#ffffff"
    ];
  }

  init() {
    if (this.initialized) return;
    this.initialized = true;
    this.bindCanvasEvents();
    this.bindWindowEvents();
    this.bindFlyoutEvents();
    this.buildColorPalettes();
  }

  documentIdentity(fileId) {
    return String(fileId || "");
  }

  getOrCreateDocumentState(fileId, filePath = "") {
    const identity = this.documentIdentity(fileId);
    let documentState = this.documentStates.get(identity);
    if (!documentState) {
      documentState = {
        identity,
        fileId,
        filePath,
        pages: [],
        pageCache: new Map(),
        annotations: new Map(),
        undoStack: [],
        password: "",
        currentPage: 0,
        zoom: "1.0",
        bookmarks: [],
        metadata: {},
        opened: false,
      };
      this.documentStates.set(identity, documentState);
    }
    return documentState;
  }

  captureActiveDocumentState() {
    if (!this.activeDocumentState || !this.currentFileId) return;
    const zoomSelect = document.getElementById("pdfZoomSelect");
    this.activeDocumentState.fileId = this.currentFileId;
    this.activeDocumentState.filePath = this.currentFilePath;
    this.activeDocumentState.pageCache = this.pageCache;
    this.activeDocumentState.annotations = this.annotations;
    this.activeDocumentState.undoStack = this.undoStack;
    this.activeDocumentState.password = this.currentPassword;
    this.activeDocumentState.currentPage = this.currentPage;
    this.activeDocumentState.zoom = zoomSelect?.value || this.activeDocumentState.zoom || "1.0";
    this.activeDocumentState.bookmarks = this.bookmarks;
  }

  activateDocumentState(fileId, filePath, password = null) {
    const documentState = this.getOrCreateDocumentState(fileId, filePath);
    this.activeDocumentState = documentState;
    this.annotations = documentState.annotations;
    this.undoStack = documentState.undoStack;
    this.pageCache = documentState.pageCache;
    this.currentPassword = password ?? documentState.password;
    this.currentPage = documentState.currentPage;
    this.bookmarks = documentState.bookmarks;
    return documentState;
  }

  hasPendingAnnotations() {
    for (const list of this.annotations.values()) {
      if (list.length > 0) return true;
    }
    return false;
  }

  requestDocumentSwitchDecision(nextFilePath) {
    if (this.documentSwitchDecisionProvider) {
      return Promise.resolve(this.documentSwitchDecisionProvider(this.currentFilePath, nextFilePath));
    }
    const modal = document.getElementById("documentSwitchModal");
    const currentPath = document.getElementById("documentSwitchCurrentPath");
    const nextPath = document.getElementById("documentSwitchNextPath");
    if (!modal) return Promise.resolve("draft");
    if (this.documentSwitchResolver) {
      this.documentSwitchResolver("cancel");
      this.documentSwitchResolver = null;
    }
    if (currentPath) currentPath.textContent = this.currentFilePath || "";
    if (nextPath) nextPath.textContent = nextFilePath || "";
    modal.classList.remove("hidden");
    return new Promise((resolve) => {
      this.documentSwitchResolver = resolve;
    });
  }

  chooseDocumentSwitchAction(action) {
    const modal = document.getElementById("documentSwitchModal");
    if (modal) modal.classList.add("hidden");
    const resolver = this.documentSwitchResolver;
    this.documentSwitchResolver = null;
    if (resolver) resolver(action);
  }

  async prepareDocumentSwitch(nextFileId, nextFilePath) {
    if (!this.currentFileId || this.documentIdentity(this.currentFileId) === this.documentIdentity(nextFileId)) {
      return true;
    }
    this.captureActiveDocumentState();
    if (!this.hasPendingAnnotations()) return true;

    const action = await this.requestDocumentSwitchDecision(nextFilePath);
    if (action === "cancel") return false;
    if (action === "save") return this.saveAnnotations();
    if (action === "discard") {
      this.annotations.clear();
      this.undoStack.length = 0;
      this.captureActiveDocumentState();
    }
    return action === "draft" || action === "discard";
  }

  bindCanvasEvents() {
    const annotCanvas = document.getElementById("pdfAnnotationCanvas");
    if (!annotCanvas) return;

    annotCanvas.addEventListener("pointerdown", (e) => this.onPointerDown(e));
    annotCanvas.addEventListener("pointermove", (e) => this.onPointerMove(e));
    annotCanvas.addEventListener("pointerup", (e) => this.onPointerUp(e));
    annotCanvas.addEventListener("pointerleave", (e) => this.onPointerLeave(e));
  }

  bindWindowEvents() {
    window.addEventListener("resize", () => {
      const zoomSelect = document.getElementById("pdfZoomSelect");
      if (zoomSelect && (zoomSelect.value === "fit-width" || zoomSelect.value === "fit-page")) {
        this.renderCurrentPage();
      }
    });
  }

  bindFlyoutEvents() {
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".edge-flyout") && !e.target.closest("#tool-pen-container") && !e.target.closest("#tool-highlight-container") && !e.target.closest("#tool-text-container")) {
        this.closeAllFlyouts();
      }
    });
  }

  buildColorPalettes() {
    // 1. Grade de Caneta
    const penGrid = document.getElementById("penColorPaletteGrid");
    if (penGrid) {
      penGrid.innerHTML = this.penPalette
        .map(
          (c) =>
            `<button data-action="superPdf.setPenColor('${c}')" class="swatch-btn ${
              c === this.penColor ? "selected" : ""
            }" style="background-color: ${c};" title="${c}"></button>`
        )
        .join("");
    }

    // 2. Swatches de Marca-texto
    const hlGrid = document.getElementById("hlColorPaletteGrid");
    if (hlGrid) {
      hlGrid.innerHTML = this.hlPalette
        .map(
          (c) =>
            `<button data-action="superPdf.setHighlightColor('${c}')" class="swatch-btn ${
              c === this.highlightColor ? "selected" : ""
            }" style="background-color: ${c};" title="${c}"></button>`
        )
        .join("");
    }

    // 3. Swatches de Texto
    const textGrid = document.getElementById("textColorPaletteGrid");
    if (textGrid) {
      textGrid.innerHTML = this.textPalette
        .map(
          (c) =>
            `<button data-action="superPdf.setTextColor('${c}')" class="swatch-btn ${
              c === this.textColor ? "selected" : ""
            }" style="background-color: ${c};" title="${c}"></button>`
        )
        .join("");
    }
  }

  closeAllFlyouts() {
    ["pen", "highlight", "text"].forEach((name) => {
      const el = document.getElementById(`flyout-${name}`);
      if (el) el.classList.add("hidden");
    });
  }

  toggleFlyout(name) {
    const el = document.getElementById(`flyout-${name}`);
    if (!el) return;
    const isHidden = el.classList.contains("hidden");
    this.closeAllFlyouts();
    if (isHidden) {
      el.classList.remove("hidden");
      if (name === "pen") {
        this.setTool("pen");
        this.updatePenPreview();
      } else if (name === "highlight") {
        this.setTool(this.highlightMode === "block" ? "highlight_block" : "highlight_pen");
        this.updateHighlightPreview();
      } else if (name === "text") {
        this.setTool("text");
      }
    }
  }

  handleToolMainClick(toolName) {
    if (toolName === "pen") {
      if (this.currentTool === "pen") {
        this.toggleFlyout("pen");
      } else {
        this.setTool("pen");
      }
    } else if (toolName === "highlight") {
      const targetTool = this.highlightMode === "block" ? "highlight_block" : "highlight_pen";
      if (this.currentTool === targetTool) {
        this.toggleFlyout("highlight");
      } else {
        this.setTool(targetTool);
      }
    } else if (toolName === "text") {
      if (this.currentTool === "text") {
        this.toggleFlyout("text");
      } else {
        this.setTool("text");
      }
    }
  }

  drawWavePreview(canvasId, color, strokeWidth, isHighlight = false) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    ctx.save();
    ctx.beginPath();
    ctx.strokeStyle = isHighlight ? this.hexToRgba(color, 0.5) : color;
    ctx.lineWidth = Math.max(2, strokeWidth);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    // Desenha uma onda S suave e elegante
    const w = canvas.width;
    const h = canvas.height;
    const yMid = h / 2;

    ctx.moveTo(15, yMid + 4);
    ctx.bezierCurveTo(w * 0.28, yMid - 10, w * 0.42, yMid - 10, w * 0.52, yMid + 2);
    ctx.bezierCurveTo(w * 0.65, yMid + 12, w * 0.82, yMid + 8, w - 15, yMid - 4);
    ctx.stroke();
    ctx.restore();
  }

  updatePenPreview() {
    this.drawWavePreview("penWavePreview", this.penColor, this.penWidth, false);
    const label = document.getElementById("labelPenWidth");
    if (label) label.innerText = `${this.penWidth} px`;
    const slider = document.getElementById("sliderPenWidth");
    if (slider) slider.value = this.penWidth;
  }

  updateHighlightPreview() {
    this.drawWavePreview("hlWavePreview", this.highlightColor, this.highlightPenWidth, true);
    const label = document.getElementById("labelHlWidth");
    if (label) label.innerText = `${this.highlightPenWidth} px`;
    const slider = document.getElementById("sliderHlWidth");
    if (slider) slider.value = this.highlightPenWidth;
  }

  onPenWidthChange(val) {
    this.penWidth = parseFloat(val) || 4;
    this.updatePenPreview();
  }

  onHlWidthChange(val) {
    this.highlightPenWidth = parseFloat(val) || 18;
    this.updateHighlightPreview();
  }

  onTextSizeChange(val) {
    this.textFontSize = parseFloat(val) || 14;
    const label = document.getElementById("labelTextSize");
    if (label) label.innerText = `${this.textFontSize} pt`;
  }

  setHighlightMode(mode) {
    this.highlightMode = mode;
    const btnPen = document.getElementById("btnHlModePen");
    const btnBlock = document.getElementById("btnHlModeBlock");

    if (btnPen && btnBlock) {
      if (mode === "pen") {
        btnPen.className = "py-1 px-2 rounded-lg text-xs font-semibold bg-white text-sky-900 shadow-xs flex items-center justify-center gap-1 transition";
        btnBlock.className = "py-1 px-2 rounded-lg text-xs font-semibold text-slate-600 hover:text-sky-900 flex items-center justify-center gap-1 transition";
        this.setTool("highlight_pen");
      } else {
        btnBlock.className = "py-1 px-2 rounded-lg text-xs font-semibold bg-white text-sky-900 shadow-xs flex items-center justify-center gap-1 transition";
        btnPen.className = "py-1 px-2 rounded-lg text-xs font-semibold text-slate-600 hover:text-sky-900 flex items-center justify-center gap-1 transition";
        this.setTool("highlight_block");
      }
    }
  }

  async openFileDialog() {
    if (!window.pywebview || !window.pywebview.api) {
      showToast("Aguardando carregamento da interface...", "info");
      return;
    }
    playBeep("click");
    const selected = await window.pywebview.api.choose_files();
    if (selected && selected.length > 0) {
      addProcessedFiles(selected);
      this.loadDocument(selected[0].file_id, null, selected[0].path);
    }
  }

  updateDocumentDropdown() {
    const select = document.getElementById("pdfViewerSelect");
    if (!select) return;

    if (state.files.length === 0) {
      select.innerHTML = `<option value="">Nenhum PDF carregado</option>`;
      return;
    }

    select.innerHTML = `
      <option value="">Selecione um PDF...</option>
      ${state.files
        .map(
          (f) =>
            `<option value="${escapeHtml(f.file_id)}" ${f.file_id === this.currentFileId ? "selected" : ""}>${escapeHtml(f.name)}</option>`
        )
        .join("")}
    `;
  }

  async onSelectDocument(fileId) {
    if (!fileId) return;
    const selected = state.files.find((file) => file.file_id === fileId);
    const opened = await this.loadDocument(fileId, null, selected?.path || "");
    if (!opened) {
      const select = document.getElementById("pdfViewerSelect");
      if (select) select.value = this.currentFileId || "";
    }
  }

  async loadDocument(fileId, password = null, displayPath = "") {
    if (!fileId) return false;
    const knownFile = state.files.find((file) => file.file_id === fileId);
    const filePath = displayPath || knownFile?.path || this.documentStates.get(this.documentIdentity(fileId))?.filePath || "";
    const requestId = ++this.loadRequestId;
    const canSwitch = await this.prepareDocumentSwitch(fileId, filePath);
    if (!canSwitch || requestId !== this.loadRequestId) return false;

    playBeep("click");
    const info = await window.pywebview.api.get_pdf_info(fileId, password);
    if (requestId !== this.loadRequestId) return false;
    if (!info.ok) {
      if (info.needs_password) {
        this.pendingPasswordFile = filePath;
        this.pendingPasswordFileId = fileId;
        this.openUnlockModal();
        return false;
      }
      showToast(`Erro ao abrir PDF: ${info.error}`, "error");
      return false;
    }

    this.captureActiveDocumentState();
    this.currentFileId = fileId;
    this.currentFilePath = info.file_path || filePath;
    const documentState = this.activateDocumentState(fileId, this.currentFilePath, password);
    this.currentFileName = info.file_name;
    this.totalPages = info.page_count;
    this.isEncrypted = Boolean(info.is_encrypted);
    this.bookmarks = info.bookmarks || [];
    documentState.bookmarks = this.bookmarks;
    documentState.metadata = info.metadata || {};

    // Solcita primeira faixa de 50 páginas por demanda (Item 12)
    const rangeData = await window.pywebview.api.get_pdf_page_range(fileId, 0, 50, password);
    if (rangeData && rangeData.ok && rangeData.pages) {
      documentState.pages = rangeData.pages;
    } else {
      documentState.pages = [];
    }

    // Restaura primeiro o rascunho da sessão; na primeira abertura, usa o histórico persistido.
    if (documentState.opened) {
      this.currentPage = Math.min(Math.max(0, documentState.currentPage), Math.max(0, this.totalPages - 1));
      const zoomSelect = document.getElementById("pdfZoomSelect");
      if (zoomSelect) zoomSelect.value = documentState.zoom || "1.0";
    } else if (info.session_state && info.session_state.found) {
      if (info.session_state.last_page_read > 0 && info.session_state.last_page_read < this.totalPages) {
        this.currentPage = info.session_state.last_page_read;
      } else {
        this.currentPage = 0;
      }
      if (info.session_state.preferred_zoom) {
        const zoomSelect = document.getElementById("pdfZoomSelect");
        if (zoomSelect) zoomSelect.value = info.session_state.preferred_zoom;
      }
    } else {
      this.currentPage = 0;
    }
    documentState.currentPage = this.currentPage;
    documentState.password = this.currentPassword;
    documentState.opened = true;

    this.updateDocumentDropdown();
    const select = document.getElementById("pdfViewerSelect");
    if (select) select.value = fileId;

    const totalPagesLabel = document.getElementById("pdfTotalPagesLabel");
    if (totalPagesLabel) totalPagesLabel.innerText = `/ ${this.totalPages}`;
    const pageInput = document.getElementById("pdfPageInput");
    if (pageInput) {
      pageInput.max = this.totalPages;
      pageInput.disabled = false;
    }
    const btnPrev = document.getElementById("btnPdfPrev");
    if (btnPrev) btnPrev.disabled = false;
    const btnNext = document.getElementById("btnPdfNext");
    if (btnNext) btnNext.disabled = false;
    const btnProtect = document.getElementById("btnToolProtect");
    if (btnProtect) btnProtect.disabled = false;
    const btnPrint = document.getElementById("btnToolPrint");
    if (btnPrint) btnPrint.disabled = false;
    const btnSave = document.getElementById("btnToolSave");
    if (btnSave) btnSave.disabled = false;

    const emptyState = document.getElementById("pdfEmptyState");
    if (emptyState) emptyState.classList.add("hidden");
    const canvasWrapper = document.getElementById("pdfCanvasWrapper");
    if (canvasWrapper) canvasWrapper.classList.remove("hidden");

    this.renderThumbnails(documentState.pages);
    this.renderBookmarksList();
    this.updateBookmarkButtonState();
    await this.renderCurrentPage();
    return requestId === this.loadRequestId;
  }

  async ensurePageRange(pageIndex) {
    if (!this.currentFileId) return;
    const documentState = this.getOrCreateDocumentState(this.currentFileId, this.currentFilePath);
    if (!documentState.pages) documentState.pages = [];
    if (documentState.pages[pageIndex]) return;

    const startPage = Math.floor(pageIndex / 50) * 50;
    const rangeData = await window.pywebview.api.get_pdf_page_range(this.currentFileId, startPage, 50, this.currentPassword);
    if (rangeData && rangeData.ok && rangeData.pages) {
      rangeData.pages.forEach((p) => {
        documentState.pages[p.page_number] = p;
      });
      this.renderThumbnails(documentState.pages);
    }
  }

  renderThumbnails(pages) {
    const container = document.getElementById("pdfThumbnailsContainer");
    const badge = document.getElementById("thumbCountBadge");
    if (badge) {
      if (pages && pages.length > 0) {
        badge.innerText = this.totalPages || pages.length;
        badge.classList.remove("hidden");
      } else {
        badge.innerText = "0";
        badge.classList.add("hidden");
      }
    }
    if (!container) return;

    if (!pages || pages.length === 0) {
      container.innerHTML = `<div class="text-[11px] text-slate-400 text-center py-8">Nenhuma página encontrada.</div>`;
      return;
    }

    container.innerHTML = pages
      .filter(Boolean)
      .map(
        (p) => `
      <div data-action="superPdf.goToPage(${p.page_number})" id="thumb-card-${p.page_number}" class="pdf-thumb-card p-2 rounded-xl border border-sky-100 bg-white shadow-xs flex items-center justify-between text-xs font-semibold cursor-pointer hover:border-sky-300 transition ${
          p.page_number === this.currentPage ? "active" : ""
        }">
        <span class="text-slate-700">Pág. ${p.page_number + 1}</span>
        <span class="text-[10px] font-mono text-slate-400">${Math.round(p.width)}x${Math.round(p.height)}</span>
      </div>
    `
      )
      .join("");
  }

  updateActiveThumbnail() {
    for (let i = 0; i < this.totalPages; i++) {
      const el = document.getElementById(`thumb-card-${i}`);
      if (el) {
        if (i === this.currentPage) {
          el.classList.add("active");
          el.scrollIntoView({ block: "nearest", behavior: "smooth" });
        } else {
          el.classList.remove("active");
        }
      }
    }
  }

  async renderCurrentPage(forceReload = false) {
    if (!this.currentFileId) return;
    const requestId = ++this.renderRequestId;
    const requestedFileId = this.currentFileId;
    const requestedPage = this.currentPage;
    this.isRendering = true;

    const loader = document.getElementById("pdfLoadingIndicator");
    if (loader) loader.classList.remove("hidden");

    const pageInput = document.getElementById("pdfPageInput");
    if (pageInput) pageInput.value = this.currentPage + 1;

    await this.ensurePageRange(requestedPage);
    this.updateActiveThumbnail();

    try {
      const cacheKey = `${requestedFileId}:${requestedPage}`;
      let pageData = this.lruCache.get(cacheKey);

      if (!pageData || forceReload) {
        pageData = await window.pywebview.api.render_page_hq(requestedFileId, requestedPage, 150);
        if (
          requestId !== this.renderRequestId ||
          requestedFileId !== this.currentFileId ||
          requestedPage !== this.currentPage
        ) {
          return;
        }
        if (!pageData.ok) {
          if (pageData.needs_password) {
            this.pendingPasswordFile = this.currentFilePath;
            this.pendingPasswordFileId = requestedFileId;
            this.openUnlockModal();
          } else {
            showToast(pageData.error, "error");
          }
          if (loader) loader.classList.add("hidden");
          this.isRendering = false;
          return;
        }
        this.lruCache.set(cacheKey, pageData, pageData.width || 800, pageData.height || 1100);
      }
      this.pageWidth = pageData.width;
      this.pageHeight = pageData.height;
      this.pageRotation = pageData.rotation;

      const scale = this.calculateDisplayScale();
      const displayWidth = Math.round(this.pageWidth * scale);
      const displayHeight = Math.round(this.pageHeight * scale);

      const pageCanvas = document.getElementById("pdfPageCanvas");
      const annotCanvas = document.getElementById("pdfAnnotationCanvas");
      const wrapper = document.getElementById("pdfCanvasWrapper");

      if (wrapper) {
        wrapper.style.width = `${displayWidth}px`;
        wrapper.style.height = `${displayHeight}px`;
      }

      if (pageCanvas) {
        pageCanvas.width = pageData.pixel_width || displayWidth;
        pageCanvas.height = pageData.pixel_height || displayHeight;
        pageCanvas.style.width = `${displayWidth}px`;
        pageCanvas.style.height = `${displayHeight}px`;
      }

      if (annotCanvas) {
        annotCanvas.width = displayWidth;
        annotCanvas.height = displayHeight;
        annotCanvas.style.width = `${displayWidth}px`;
        annotCanvas.style.height = `${displayHeight}px`;
      }

      const imageUrl = pageData.image || pageData.image_base64 || "";
      if (pageCanvas && imageUrl) {
        const ctx = pageCanvas.getContext("2d");
        const img = new Image();
        img.onload = () => {
          if (
            requestId !== this.renderRequestId ||
            requestedFileId !== this.currentFileId ||
            requestedPage !== this.currentPage
          ) {
            return;
          }
          ctx.clearRect(0, 0, pageCanvas.width, pageCanvas.height);
          ctx.drawImage(img, 0, 0);
          this.redrawAnnotations();
          if (loader) loader.classList.add("hidden");
          this.isRendering = false;
        };
        img.onerror = () => {
          if (requestId === this.renderRequestId) {
            if (loader) loader.classList.add("hidden");
            this.isRendering = false;
          }
        };
        img.src = imageUrl;
      } else {
        if (loader) loader.classList.add("hidden");
        this.isRendering = false;
      }
    } catch (err) {
      console.error("Erro ao renderizar página:", err);
      if (requestId === this.renderRequestId) {
        if (loader) loader.classList.add("hidden");
        this.isRendering = false;
      }
    }
  }

  calculateDisplayScale() {
    const zoomSelect = document.getElementById("pdfZoomSelect");
    const val = zoomSelect ? zoomSelect.value : "1.0";
    const stage = document.getElementById("pdfStageContainer");
    const availableWidth = stage ? stage.clientWidth - 56 : 760;

    if (val === "fit-width") {
      return Math.max(0.3, availableWidth / this.pageWidth);
    }
    if (val === "fit-page") {
      const availableHeight = stage ? stage.clientHeight - 56 : 680;
      return Math.max(0.3, Math.min(availableWidth / this.pageWidth, availableHeight / this.pageHeight));
    }
    return parseFloat(val) || 1.0;
  }

  setZoom(val) {
    const select = document.getElementById("pdfZoomSelect");
    if (select) select.value = val;
    if (this.activeDocumentState) this.activeDocumentState.zoom = val;
    this.renderCurrentPage();
  }

  zoomIn() {
    const select = document.getElementById("pdfZoomSelect");
    const options = ["0.5", "0.75", "1.0", "1.25", "1.5", "2.0"];
    const curIndex = options.indexOf(select.value);
    if (curIndex >= 0 && curIndex < options.length - 1) {
      this.setZoom(options[curIndex + 1]);
    } else {
      this.setZoom("1.25");
    }
  }

  zoomOut() {
    const select = document.getElementById("pdfZoomSelect");
    const options = ["0.5", "0.75", "1.0", "1.25", "1.5", "2.0"];
    const curIndex = options.indexOf(select.value);
    if (curIndex > 0) {
      this.setZoom(options[curIndex - 1]);
    } else {
      this.setZoom("0.75");
    }
  }

  goToPage(pageIdx) {
    if (pageIdx < 0 || pageIdx >= this.totalPages) return;
    this.currentPage = pageIdx;
    if (this.activeDocumentState) this.activeDocumentState.currentPage = pageIdx;
    this.updateBookmarkButtonState();
    this.renderCurrentPage();

    // Auto-salvamento debounced do histórico de leitura
    if (this.saveStateTimeout) clearTimeout(this.saveStateTimeout);
    this.saveStateTimeout = setTimeout(() => {
      if (this.currentFileId && window.pywebview && window.pywebview.api) {
        const zoomVal = document.getElementById("pdfZoomSelect")?.value || "1.0";
        window.pywebview.api.save_reading_state(this.currentFileId, this.currentPage, zoomVal);
      }
    }, 500);
  }

  prevPage() {
    if (this.currentPage > 0) {
      this.goToPage(this.currentPage - 1);
    }
  }

  nextPage() {
    if (this.currentPage < this.totalPages - 1) {
      this.goToPage(this.currentPage + 1);
    }
  }

  setSidebarTab(tab) {
    this.sidebarTab = tab;
    const btnPages = document.getElementById("btnTabPages");
    const btnBms = document.getElementById("btnTabBookmarks");
    const pCont = document.getElementById("pdfThumbnailsContainer");
    const bCont = document.getElementById("pdfBookmarksContainer");
    if (!btnPages || !btnBms || !pCont || !bCont) return;

    if (tab === "pages") {
      btnPages.className = "flex-1 py-1 rounded-lg bg-white text-sky-900 shadow-xs text-center transition";
      btnBms.className = "flex-1 py-1 rounded-lg text-slate-600 hover:text-sky-900 text-center transition flex items-center justify-center gap-1";
      pCont.classList.remove("hidden");
      bCont.classList.add("hidden");
    } else {
      btnBms.className = "flex-1 py-1 rounded-lg bg-white text-sky-900 shadow-xs text-center transition flex items-center justify-center gap-1";
      btnPages.className = "flex-1 py-1 rounded-lg text-slate-600 hover:text-sky-900 text-center transition";
      pCont.classList.add("hidden");
      bCont.classList.remove("hidden");
      this.renderBookmarksList();
    }
  }

  renderBookmarksList() {
    const container = document.getElementById("pdfBookmarksContainer");
    const badge = document.getElementById("bookmarkCountBadge");
    if (!container) return;

    if (badge) {
      badge.innerText = this.bookmarks.length;
      badge.classList.toggle("hidden", this.bookmarks.length === 0);
    }

    if (this.bookmarks.length === 0) {
      container.innerHTML = `<div class="text-[11px] text-slate-400 text-center py-8">Nenhum marcador salvo. Clique na estrela ⭐ para marcar a página atual.</div>`;
      return;
    }

    container.innerHTML = this.bookmarks
      .map(
        (bm) => `
      <div class="p-2 rounded-xl border border-sky-100 bg-white shadow-xs flex items-center justify-between text-xs group hover:border-amber-300 transition">
        <div data-action="superPdf.goToPage(${bm.page_number})" class="flex-1 cursor-pointer truncate">
          <div class="font-bold text-slate-800 truncate">${escapeHtml(bm.title || `Página ${bm.page_number + 1}`)}</div>
          <div class="text-[10px] text-amber-700 font-mono">Pág. ${bm.page_number + 1}</div>
        </div>
        <button data-action="superPdf.deleteBookmark(${bm.id}, event)" class="p-1 text-slate-300 hover:text-rose-600 opacity-0 group-hover:opacity-100 transition" title="Remover Marcador">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
        </button>
      </div>
    `
      )
      .join("");
  }

  async toggleBookmark() {
    if (!this.currentFilePath) return;
    const existing = this.bookmarks.find((b) => b.page_number === this.currentPage);
    if (existing) {
      await this.deleteBookmark(existing.id);
    } else {
      const title = `Página ${this.currentPage + 1}`;
      const res = await window.pywebview.api.add_bookmark(this.currentFileId, this.currentPage, title);
      if (res.ok) {
        this.bookmarks.push(res);
        this.updateBookmarkButtonState();
        this.renderBookmarksList();
        playBeep("success");
        showToast(`Marcador adicionado na Página ${this.currentPage + 1}!`, "success");
      }
    }
  }

  async deleteBookmark(id, event) {
    if (event) event.stopPropagation();
    const res = await window.pywebview.api.delete_bookmark(this.currentFileId, id);
    if (res.ok) {
      this.bookmarks = this.bookmarks.filter((b) => b.id !== id);
      this.updateBookmarkButtonState();
      this.renderBookmarksList();
      playBeep("click");
      showToast("Marcador removido.", "info");
    }
  }

  updateBookmarkButtonState() {
    const icon = document.getElementById("iconBookmarkStar");
    if (!icon) return;
    const isBookmarked = this.bookmarks.some((b) => b.page_number === this.currentPage);
    if (isBookmarked) {
      icon.setAttribute("fill", "currentColor");
      icon.classList.add("text-amber-500");
    } else {
      icon.setAttribute("fill", "none");
      icon.classList.remove("text-amber-500");
    }
  }

  setTool(tool) {
    playBeep("click");
    this.currentTool = tool;

    // Atualiza estados dos botões principais
    const btnPan = document.getElementById("tool-pan");
    const containerPen = document.getElementById("tool-pen-container");
    const containerHl = document.getElementById("tool-highlight-container");
    const containerText = document.getElementById("tool-text-container");
    const btnSnippet = document.getElementById("tool-snippet");

    if (btnPan) {
      btnPan.classList.toggle("bg-white", tool === "pan");
      btnPan.classList.toggle("text-sky-900", tool === "pan");
      btnPan.classList.toggle("shadow-sm", tool === "pan");
      btnPan.classList.toggle("text-slate-600", tool !== "pan");
    }
    if (containerPen) {
      containerPen.classList.toggle("ring-2", tool === "pen");
      containerPen.classList.toggle("ring-sky-500", tool === "pen");
      containerPen.classList.toggle("bg-sky-50", tool === "pen");
    }
    if (containerHl) {
      const isHl = tool === "highlight_pen" || tool === "highlight_block";
      containerHl.classList.toggle("ring-2", isHl);
      containerHl.classList.toggle("ring-amber-500", isHl);
      containerHl.classList.toggle("bg-amber-50", isHl);
    }
    if (containerText) {
      containerText.classList.toggle("ring-2", tool === "text");
      containerText.classList.toggle("ring-blue-500", tool === "text");
      containerText.classList.toggle("bg-blue-50", tool === "text");
    }
    if (btnSnippet) {
      btnSnippet.classList.toggle("bg-rose-50", tool === "snippet");
      btnSnippet.classList.toggle("text-rose-700", tool === "snippet");
      btnSnippet.classList.toggle("ring-2", tool === "snippet");
      btnSnippet.classList.toggle("ring-rose-500", tool === "snippet");
    }

    const canvas = document.getElementById("pdfAnnotationCanvas");
    if (canvas) {
      canvas.className = "absolute inset-0";
      if (tool === "pan") canvas.classList.add("cursor-pan");
      else if (tool === "pen") canvas.classList.add("cursor-pen");
      else if (tool === "highlight_pen") canvas.classList.add("cursor-highlight-pen");
      else if (tool === "highlight_block") canvas.classList.add("cursor-highlight-block");
      else if (tool === "text") canvas.classList.add("cursor-text-tool");
      else if (tool === "snippet") canvas.classList.add("cursor-snippet");
    }
  }

  setPenColor(color) {
    this.penColor = color;
    this.buildColorPalettes();
    this.updatePenPreview();
    playBeep("click");
  }

  setHighlightColor(color) {
    this.highlightColor = color;
    this.buildColorPalettes();
    this.updateHighlightPreview();
    playBeep("click");
  }

  setTextColor(color) {
    this.textColor = color;
    this.buildColorPalettes();
    playBeep("click");
  }

  toggleTextBold() {
    this.textBold = !this.textBold;
    const btn = document.getElementById("btnTextBold");
    if (btn) {
      btn.classList.toggle("bg-sky-600", this.textBold);
      btn.classList.toggle("text-white", this.textBold);
      btn.classList.toggle("text-slate-700", !this.textBold);
    }
    playBeep("click");
  }

  setTextBoxStyle(style) {
    this.textBoxStyle = style;
    ["none", "postit", "white", "danger"].forEach((s) => {
      const el = document.getElementById(`btnStyle${s.charAt(0).toUpperCase() + s.slice(1)}`);
      if (el) {
        el.classList.toggle("active-style", s === style);
      }
    });
  }

  screenToPdf(x, y) {
    const canvas = document.getElementById("pdfAnnotationCanvas");
    const scaleX = this.pageWidth / canvas.width;
    const scaleY = this.pageHeight / canvas.height;
    return {
      x: x * scaleX,
      y: y * scaleY,
    };
  }

  pdfToScreen(x, y) {
    const canvas = document.getElementById("pdfAnnotationCanvas");
    const scaleX = canvas.width / this.pageWidth;
    const scaleY = canvas.height / this.pageHeight;
    return {
      x: x * scaleX,
      y: y * scaleY,
    };
  }

  onPointerDown(e) {
    if (!this.currentFilePath) return;

    // 1. Se já havia um card em edição ativa, apenas desfoque e fixe ele, sem criar nova caixa no mesmo clique
    const activeEl = document.activeElement;
    if (activeEl && (activeEl.classList.contains("card-content") || activeEl.closest(".pdf-text-card"))) {
      activeEl.blur();
      if (window.getSelection) {
        const sel = window.getSelection();
        if (sel) sel.removeAllRanges();
      }
      this.isInteracting = false;
      return;
    }

    if (this.currentTool === "pan") return;

    const rect = e.target.getBoundingClientRect();
    const clientX = e.clientX - rect.left;
    const clientY = e.clientY - rect.top;
    const pdfPt = this.screenToPdf(clientX, clientY);

    this.isInteracting = true;
    this.startPoint = { x: clientX, y: clientY, pdfX: pdfPt.x, pdfY: pdfPt.y };

    if (this.currentTool === "pen" || this.currentTool === "highlight_pen") {
      this.currentStroke = [[pdfPt.x, pdfPt.y]];
    } else if (this.currentTool === "text") {
      this.isInteracting = false;
      this.createInlineTextInput(clientX, clientY, pdfPt.x, pdfPt.y);
      this.setTool("pan");
    }
  }

  onPointerMove(e) {
    if (!this.isInteracting) return;

    const rect = e.target.getBoundingClientRect();
    const clientX = e.clientX - rect.left;
    const clientY = e.clientY - rect.top;
    const pdfPt = this.screenToPdf(clientX, clientY);

    const canvas = document.getElementById("pdfAnnotationCanvas");
    const ctx = canvas.getContext("2d");

    if (this.currentTool === "pen") {
      this.currentStroke.push([pdfPt.x, pdfPt.y]);
      this.redrawAnnotations();

      ctx.save();
      ctx.beginPath();
      ctx.strokeStyle = this.penColor;
      ctx.lineWidth = this.penWidth * (canvas.width / this.pageWidth);
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      const start = this.pdfToScreen(this.currentStroke[0][0], this.currentStroke[0][1]);
      ctx.moveTo(start.x, start.y);
      for (let i = 1; i < this.currentStroke.length; i++) {
        const pt = this.pdfToScreen(this.currentStroke[i][0], this.currentStroke[i][1]);
        ctx.lineTo(pt.x, pt.y);
      }
      ctx.stroke();
      ctx.restore();
    } else if (this.currentTool === "highlight_pen") {
      this.currentStroke.push([pdfPt.x, pdfPt.y]);
      this.redrawAnnotations();

      ctx.save();
      ctx.beginPath();
      ctx.strokeStyle = this.hexToRgba(this.highlightColor, 0.45);
      ctx.lineWidth = this.highlightPenWidth * (canvas.width / this.pageWidth);
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      const start = this.pdfToScreen(this.currentStroke[0][0], this.currentStroke[0][1]);
      ctx.moveTo(start.x, start.y);
      for (let i = 1; i < this.currentStroke.length; i++) {
        const pt = this.pdfToScreen(this.currentStroke[i][0], this.currentStroke[i][1]);
        ctx.lineTo(pt.x, pt.y);
      }
      ctx.stroke();
      ctx.restore();
    } else if (this.currentTool === "highlight_block") {
      this.redrawAnnotations();
      ctx.save();
      ctx.fillStyle = this.hexToRgba(this.highlightColor, 0.45);
      const w = clientX - this.startPoint.x;
      const h = clientY - this.startPoint.y;
      ctx.fillRect(this.startPoint.x, this.startPoint.y, w, h);
      ctx.restore();
    } else if (this.currentTool === "snippet") {
      this.redrawAnnotations();
      ctx.save();
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = "#0284c7";
      ctx.lineWidth = 1.5;
      ctx.fillStyle = "rgba(56, 189, 248, 0.15)";
      const x = Math.min(this.startPoint.x, clientX);
      const y = Math.min(this.startPoint.y, clientY);
      const w = Math.abs(clientX - this.startPoint.x);
      const h = Math.abs(clientY - this.startPoint.y);
      ctx.fillRect(x, y, w, h);
      ctx.strokeRect(x, y, w, h);
      ctx.restore();
    }
  }

  async onPointerUp(e) {
    if (!this.isInteracting) return;
    this.isInteracting = false;

    const rect = e.target.getBoundingClientRect();
    const clientX = e.clientX - rect.left;
    const clientY = e.clientY - rect.top;
    const pdfPt = this.screenToPdf(clientX, clientY);

    if (this.currentTool === "pen" && this.currentStroke.length > 1) {
      const annot = {
        type: "ink",
        page_number: this.currentPage,
        strokes: [this.currentStroke],
        color: this.penColor,
        width: this.penWidth,
      };
      this.addAnnotation(annot);
      this.currentStroke = [];
      this.redrawAnnotations();
    } else if (this.currentTool === "highlight_pen" && this.currentStroke.length > 1) {
      const annot = {
        type: "highlight_pen",
        page_number: this.currentPage,
        strokes: [this.currentStroke],
        color: this.highlightColor,
        width: this.highlightPenWidth,
      };
      this.addAnnotation(annot);
      this.currentStroke = [];
      this.redrawAnnotations();
    } else if (this.currentTool === "highlight_block") {
      const x0 = Math.min(this.startPoint.pdfX, pdfPt.x);
      const y0 = Math.min(this.startPoint.pdfY, pdfPt.y);
      const x1 = Math.max(this.startPoint.pdfX, pdfPt.x);
      const y1 = Math.max(this.startPoint.pdfY, pdfPt.y);

      if (Math.abs(x1 - x0) > 4 && Math.abs(y1 - y0) > 4) {
        const annot = {
          type: "highlight_block",
          page_number: this.currentPage,
          rect: [x0, y0, x1, y1],
          color: this.highlightColor,
        };
        this.addAnnotation(annot);
        this.redrawAnnotations();
      }
    } else if (this.currentTool === "snippet") {
      const x0 = Math.min(this.startPoint.pdfX, pdfPt.x);
      const y0 = Math.min(this.startPoint.pdfY, pdfPt.y);
      const x1 = Math.max(this.startPoint.pdfX, pdfPt.x);
      const y1 = Math.max(this.startPoint.pdfY, pdfPt.y);

      this.redrawAnnotations();

      if (Math.abs(x1 - x0) > 6 && Math.abs(y1 - y0) > 6) {
        await this.triggerSnippetExtraction(x0, y0, x1, y1);
      }
    }
  }

  onPointerLeave() {
    if (this.isInteracting) {
      this.isInteracting = false;
      this.redrawAnnotations();
    }
  }

  createInlineTextInput(screenX, screenY, pdfX, pdfY) {
    const annot = {
      id: "txt_" + Date.now() + "_" + Math.random().toString(36).substr(2, 4),
      type: "text",
      page_number: this.currentPage,
      x: pdfX,
      y: pdfY,
      width: 180,
      height: 45,
      text: "",
      fontsize: this.textFontSize,
      bold: this.textBold,
      text_color: this.textColor,
      style: this.textBoxStyle || "postit",
    };
    this.addAnnotation(annot);
    this.redrawAnnotations();

    setTimeout(() => {
      const card = document.querySelector(`[data-annot-id="${annot.id}"]`);
      if (card) {
        const contentEl = card.querySelector(".card-content");
        if (contentEl) {
          contentEl.focus();
        }
      }
    }, 50);
  }

  deleteAnnotation(annot) {
    const list = this.annotations.get(this.currentPage) || [];
    const idx = list.indexOf(annot);
    if (idx !== -1) {
      list.splice(idx, 1);
      this.annotations.set(this.currentPage, list);
      this.redrawAnnotations();
      playBeep("click");
    }
  }

  addAnnotation(annot) {
    const list = this.annotations.get(this.currentPage) || [];
    list.push(annot);
    this.annotations.set(this.currentPage, list);
    this.undoStack.push({ page: this.currentPage, annot });
    playBeep("click");
  }

  undoAnnotation() {
    if (this.undoStack.length === 0) {
      showToast("Nenhuma anotação para desfazer.", "info");
      return;
    }
    const last = this.undoStack.pop();
    const list = this.annotations.get(last.page) || [];
    const idx = list.indexOf(last.annot);
    if (idx !== -1) {
      list.splice(idx, 1);
      this.annotations.set(last.page, list);
    }
    playBeep("click");
    this.redrawAnnotations();
  }

  clearPageAnnotations() {
    this.annotations.set(this.currentPage, []);
    playBeep("click");
    this.redrawAnnotations();
    showToast("Anotações da página removidas.", "info");
  }

  redrawAnnotations() {
    const canvas = document.getElementById("pdfAnnotationCanvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const list = this.annotations.get(this.currentPage) || [];

    // 1. Renderiza traços e desenhos vetoriais no Canvas
    for (const item of list) {
      // 1.1 Caneta Comum
      if (item.type === "ink") {
        ctx.save();
        ctx.strokeStyle = item.color;
        ctx.lineWidth = item.width * (canvas.width / this.pageWidth);
        ctx.lineCap = "round";
        ctx.lineJoin = "round";

        for (const stroke of item.strokes) {
          if (stroke.length > 0) {
            ctx.beginPath();
            const start = this.pdfToScreen(stroke[0][0], stroke[0][1]);
            ctx.moveTo(start.x, start.y);
            for (let i = 1; i < stroke.length; i++) {
              const pt = this.pdfToScreen(stroke[i][0], stroke[i][1]);
              ctx.lineTo(pt.x, pt.y);
            }
            ctx.stroke();
          }
        }
        ctx.restore();
      }
      // 1.2 Caneta Marca-Texto Livre
      else if (item.type === "highlight_pen") {
        ctx.save();
        ctx.strokeStyle = this.hexToRgba(item.color, 0.45);
        ctx.lineWidth = (item.width || 18) * (canvas.width / this.pageWidth);
        ctx.lineCap = "round";
        ctx.lineJoin = "round";

        for (const stroke of item.strokes) {
          if (stroke.length > 0) {
            ctx.beginPath();
            const start = this.pdfToScreen(stroke[0][0], stroke[0][1]);
            ctx.moveTo(start.x, start.y);
            for (let i = 1; i < stroke.length; i++) {
              const pt = this.pdfToScreen(stroke[i][0], stroke[i][1]);
              ctx.lineTo(pt.x, pt.y);
            }
            ctx.stroke();
          }
        }
        ctx.restore();
      }
      // 1.3 Marca-Texto em Bloco
      else if (item.type === "highlight" || item.type === "highlight_block") {
        ctx.save();
        ctx.fillStyle = this.hexToRgba(item.color, 0.45);
        const p0 = this.pdfToScreen(item.rect[0], item.rect[1]);
        const p1 = this.pdfToScreen(item.rect[2], item.rect[3]);
        ctx.fillRect(p0.x, p0.y, p1.x - p0.x, p1.y - p0.y);
        ctx.restore();
      }
    }

    // 2. Renderiza Anotações de Texto na Camada DOM Nativa (Subpixel Text Rendering do WebView2)
    const textContainer = document.getElementById("pdfOverlayTextContainer");
    if (!textContainer) return;

    const activeEl = document.activeElement;
    const activeCard = activeEl ? activeEl.closest(".pdf-text-card") : null;
    const activeId = activeCard && activeCard.dataset ? activeCard.dataset.annotId : null;

    textContainer.innerHTML = "";
    const textItems = list.filter((item) => item.type === "text");

    for (const item of textItems) {
      if (!item.id) item.id = "txt_" + Math.random().toString(36).substr(2, 9);

      const screenPos = this.pdfToScreen(item.x, item.y);
      const scaleFont = item.fontsize * (canvas.width / this.pageWidth);
      const styleName = item.style || "postit";

      const card = document.createElement("div");
      card.className = `pdf-text-card text-style-${styleName}`;
      card.dataset.annotId = item.id;
      card.style.left = `${screenPos.x}px`;
      card.style.top = `${screenPos.y}px`;

      // Impede propagação de cliques para o canvas ou container inferior
      card.addEventListener("mousedown", (e) => e.stopPropagation());
      card.addEventListener("pointerdown", (e) => e.stopPropagation());
      card.addEventListener("click", (e) => e.stopPropagation());

      const contentEl = document.createElement("div");
      contentEl.className = "card-content";
      contentEl.setAttribute("contenteditable", "true");
      contentEl.setAttribute("spellcheck", "false");
      contentEl.setAttribute("data-placeholder", "Digite sua anotação...");
      contentEl.style.fontSize = `${Math.max(11, scaleFont)}px`;
      contentEl.style.fontWeight = item.bold ? "700" : "500";
      if (item.text_color) {
        contentEl.style.color = item.text_color;
      }
      contentEl.innerText = item.text || "";

      // Impede que eventos no elemento editável vazem
      contentEl.addEventListener("mousedown", (e) => e.stopPropagation());
      contentEl.addEventListener("pointerdown", (e) => e.stopPropagation());
      contentEl.addEventListener("click", (e) => e.stopPropagation());

      // Botão de deletar flutuante
      const delBtn = document.createElement("span");
      delBtn.className = "text-item-delete-btn";
      delBtn.innerText = "×";
      delBtn.title = "Excluir anotação";
      delBtn.contentEditable = "false";
      delBtn.addEventListener("mousedown", (e) => {
        e.stopPropagation();
        e.preventDefault();
        this.deleteAnnotation(item);
      });
      card.appendChild(delBtn);

      contentEl.addEventListener("input", () => {
        item.text = contentEl.innerText;
        const scale = this.calculateDisplayScale();
        if (scale > 0) {
          item.width = card.offsetWidth / scale;
          item.height = card.offsetHeight / scale;
        }
      });

      contentEl.addEventListener("blur", () => {
        const clean = contentEl.innerText.trim();
        item.text = contentEl.innerText;
        const scale = this.calculateDisplayScale();
        if (scale > 0) {
          item.width = card.offsetWidth / scale;
          item.height = card.offsetHeight / scale;
        }
        if (!clean) {
          this.deleteAnnotation(item);
        }
      });

      contentEl.addEventListener("keydown", (e) => {
        // Ctrl + Enter ou Esc: Finaliza a edição, desfoque e fixa o card
        if ((e.ctrlKey && e.key === "Enter") || e.key === "Escape") {
          e.preventDefault();
          contentEl.blur();
          if (window.getSelection) {
            const sel = window.getSelection();
            if (sel) sel.removeAllRanges();
          }
        }
      });

      card.addEventListener("click", (e) => {
        e.stopPropagation();
        if (e.target !== delBtn && document.activeElement !== contentEl) {
          contentEl.focus();
        }
      });

      card.appendChild(contentEl);
      textContainer.appendChild(card);

      if (item.id === activeId) {
        setTimeout(() => {
          contentEl.focus();
          const range = document.createRange();
          range.selectNodeContents(contentEl);
          range.collapse(false);
          const sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(range);
        }, 50);
      }
    }
  }

  hexToRgba(hex, alpha = 1.0) {
    if (!hex) return `rgba(253, 224, 71, ${alpha})`;
    let c = hex.replace("#", "");
    if (c.length === 3) c = c.split("").map((x) => x + x).join("");
    const num = parseInt(c, 16);
    if (isNaN(num)) return `rgba(253, 224, 71, ${alpha})`;
    return `rgba(${(num >> 16) & 255}, ${(num >> 8) & 255}, ${num & 255}, ${alpha})`;
  }

  async chooseCopyDestination(suffix) {
    const destination = await window.pywebview.api.choose_pdf_save_destination(this.currentFileId, suffix);
    if (!destination) return null;
    if (!destination.ok) {
      showToast(destination.error || "Não foi possível autorizar o destino da cópia.", "error");
      return null;
    }
    return destination.output_file_id;
  }

  registerCopiedFile(result) {
    if (result && result.new_file) addProcessedFiles([result.new_file]);
  }

  async rotatePage(degrees, asCopy = false) {
    if (!this.currentFilePath) return;
    playBeep("click");
    const outputFileId = asCopy ? await this.chooseCopyDestination("rotacionado") : null;
    if (asCopy && !outputFileId) return;
    const res = await window.pywebview.api.rotate_pdf_page(
      this.currentFileId,
      this.currentPage,
      degrees,
      this.currentPassword || null,
      outputFileId,
    );
    if (res.ok) {
      showToast(res.message, "success");
      playBeep("success");
      await this.renderCurrentPage(true);
    } else {
      if (res.needs_password) {
        this.pendingPasswordFile = this.currentFilePath;
        this.pendingPasswordFileId = this.currentFileId;
        this.openUnlockModal();
      } else {
        showToast(`Erro ao rotacionar: ${res.error}`, "error");
      }
    }
  }

  async saveAnnotations(asCopy = false) {
    if (!this.currentFilePath) return false;
    playBeep("click");

    // Sincroniza qualquer card que esteja atualmente em edição antes de coletar
    const activeEl = document.activeElement;
    if (activeEl && activeEl.classList.contains("card-content")) {
      activeEl.blur();
    }

    const allAnnots = [];
    this.annotations.forEach((list) => {
      allAnnots.push(...list);
    });

    if (allAnnots.length === 0) {
      showToast("Nenhuma nova anotação para gravar.", "info");
      return true;
    }

    const outputFileId = asCopy ? await this.chooseCopyDestination("anotado") : null;
    if (asCopy && !outputFileId) return false;
    const res = await window.pywebview.api.save_pdf_annotations({
      file_id: this.currentFileId,
      annotations: allAnnots,
      password: this.currentPassword || null,
      output_file_id: outputFileId,
    });

    if (res.ok) {
      playBeep("success");
      this.registerCopiedFile(res);
      showToast(res.message, "success");
      appendLog("OK", `${this.currentFileName}: ${res.saved_count} anotação(ões) gravada(s) nativamente no PDF.`);
      this.annotations.clear();
      this.undoStack = [];
      if (this.activeDocumentState) {
        this.activeDocumentState.annotations = this.annotations;
        this.activeDocumentState.undoStack = this.undoStack;
      }
      if (!res.is_copy) await this.renderCurrentPage(true);
      return true;
    } else {
      playBeep("error");
      if (res.needs_password) {
        this.pendingPasswordFile = this.currentFilePath;
        this.pendingPasswordFileId = this.currentFileId;
        this.openUnlockModal();
      } else {
        showToast(`Erro ao salvar: ${res.error}`, "error");
      }
      return false;
    }
  }

  async triggerSnippetExtraction(x0, y0, x1, y1) {
    playBeep("click");
    const res = await window.pywebview.api.extract_snippet({
      file_id: this.currentFileId,
      page_number: this.currentPage,
      rect: [x0, y0, x1, y1],
      dpi: 150,
    });

    if (!res.ok) {
      if (res.needs_password) {
        this.pendingPasswordFile = this.currentFilePath;
        this.pendingPasswordFileId = this.currentFileId;
        this.openUnlockModal();
      } else {
        showToast(`Erro ao capturar trecho: ${res.error}`, "error");
      }
      return;
    }

    this.activeSnippetData = res;
    this.openSnippetModal(res);
  }

  openSnippetModal(data) {
    const modal = document.getElementById("snippetModal");
    const imgEl = document.getElementById("snippetImgPreview");
    const textEl = document.getElementById("snippetTextArea");
    const ocrBadge = document.getElementById("snippetOcrBadge");

    imgEl.src = data.image_base64;
    textEl.value = data.text || "(Nenhum texto detectado nesta área de recorte)";

    if (ocrBadge) {
      if (data.ocr_applied) {
        ocrBadge.classList.remove("hidden");
      } else {
        ocrBadge.classList.add("hidden");
      }
    }

    modal.classList.remove("hidden");
  }

  closeSnippetModal() {
    document.getElementById("snippetModal").classList.add("hidden");
  }

  copySnippetText() {
    const text = document.getElementById("snippetTextArea").value;
    if (text) {
      navigator.clipboard.writeText(text);
      playBeep("success");
      showToast("Texto copiado para a área de transferência!", "success");
    }
  }

  async copySnippetImage() {
    if (!this.activeSnippetData) return;
    try {
      const response = await fetch(this.activeSnippetData.image_base64);
      const blob = await response.blob();
      await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
      playBeep("success");
      showToast("Imagem copiada para a área de transferência!", "success");
    } catch (err) {
      const link = document.createElement("a");
      link.download = `snippet_pag_${this.currentPage + 1}.png`;
      link.href = this.activeSnippetData.image_base64;
      link.click();
      showToast("Imagem salva em arquivo!", "success");
    }
  }

  openProtectModal() {
    if (!this.currentFilePath) return;
    playBeep("click");

    const unencSec = document.getElementById("protectSectionUnencrypted");
    const encSec = document.getElementById("protectSectionEncrypted");
    const titleEl = document.getElementById("protectModalTitle");

    // Sempre limpa todos os campos de senha por segurança
    const userPwEl = document.getElementById("inputUserPw");
    if (userPwEl) userPwEl.value = "";
    const ownerPwEl = document.getElementById("inputOwnerPw");
    if (ownerPwEl) ownerPwEl.value = "";
    const pwInput =
      document.getElementById("inputCurrentPwForUnprotect") ||
      document.getElementById("input-remove-password") ||
      document.querySelector("#protectModal input[type='password']");
    if (pwInput) pwInput.value = "";

    if (this.isEncrypted) {
      if (unencSec) unencSec.classList.add("hidden");
      if (encSec) encSec.classList.remove("hidden");
      if (titleEl) titleEl.innerText = "Gerenciar Proteção / Remover Senha";
      if (pwInput) setTimeout(() => pwInput.focus(), 60);
    } else {
      if (unencSec) unencSec.classList.remove("hidden");
      if (encSec) encSec.classList.add("hidden");
      if (titleEl) titleEl.innerText = "Proteger Documento PDF";
      if (userPwEl) setTimeout(() => userPwEl.focus(), 60);
    }

    document.getElementById("protectModal").classList.remove("hidden");
  }

  closeProtectModal() {
    document.getElementById("protectModal").classList.add("hidden");
  }

  async submitProtectPdf(asCopy = false) {
    const userPw = document.getElementById("inputUserPw").value.trim();
    const ownerPw = document.getElementById("inputOwnerPw").value.trim();

    if (!userPw) {
      showToast("Digite a senha de abertura.", "error");
      return;
    }

    const outputFileId = asCopy ? await this.chooseCopyDestination("protegido") : null;
    if (asCopy && !outputFileId) return;
    playBeep("click");
    const res = await window.pywebview.api.protect_pdf(this.currentFileId, userPw, ownerPw, outputFileId);
    this.closeProtectModal();

    if (res.ok) {
      playBeep("success");
      this.registerCopiedFile(res);
      if (!res.is_copy) {
        this.isEncrypted = true;
        this.currentPassword = userPw;
        if (this.activeDocumentState) this.activeDocumentState.password = userPw;
      }
      showToast(res.message, "success");
      appendLog("OK", `${this.currentFileName}: Protegido com criptografia AES-256.`);
    } else {
      playBeep("error");
      showToast(`Erro ao proteger PDF: ${res.error}`, "error");
    }
  }

  async submitUnprotectPdf(asCopy = false) {
    const inputEl = document.getElementById("inputCurrentPwForUnprotect");
    const currentPw = inputEl ? inputEl.value.trim() : "";

    const outputFileId = asCopy ? await this.chooseCopyDestination("sem-senha") : null;
    if (asCopy && !outputFileId) return;
    playBeep("click");
    const res = await window.pywebview.api.unprotect_pdf(
      this.currentFileId,
      currentPw || this.currentPassword,
      outputFileId,
    );
    this.closeProtectModal();

    if (res.ok) {
      playBeep("success");
      this.registerCopiedFile(res);
      if (!res.is_copy) {
        this.isEncrypted = false;
        this.currentPassword = "";
        if (this.activeDocumentState) this.activeDocumentState.password = "";
      }
      showToast(res.message, "success");
      appendLog("OK", `${this.currentFileName}: Proteção por senha removida com sucesso.`);
      if (!res.is_copy) await this.loadDocument(this.currentFileId, null, this.currentFilePath);
    } else {
      playBeep("error");
      showToast(`Erro ao remover senha: ${res.error}`, "error");
    }
  }

  openUnlockModal() {
    const modal = document.getElementById("unlockModal");
    const input = document.getElementById("inputUnlockPw");
    const errorMsg = document.getElementById("unlockErrorMsg");
    if (modal && input) {
      input.value = "";
      if (errorMsg) errorMsg.classList.add("hidden");
      modal.classList.remove("hidden");
      setTimeout(() => input.focus(), 100);
    }
  }

  closeUnlockModal() {
    const modal = document.getElementById("unlockModal");
    if (modal) modal.classList.add("hidden");
  }

  async submitUnlockPdf() {
    const input = document.getElementById("inputUnlockPw");
    const errorMsg = document.getElementById("unlockErrorMsg");
    const pw = input ? input.value.trim() : "";

    if (!pw) {
      if (errorMsg) {
        errorMsg.innerText = "Por favor, digite a senha.";
        errorMsg.classList.remove("hidden");
      }
      return;
    }

    if (!this.pendingPasswordFileId) return;

    const unlockedFilePath = this.pendingPasswordFile;
    const unlockedFileId = this.pendingPasswordFileId;
    const res = await window.pywebview.api.set_pdf_password(unlockedFileId, pw);
    if (res.ok) {
      this.closeUnlockModal();
      showToast("PDF desbloqueado com sucesso!", "success");
      playBeep("success");
      await this.loadDocument(unlockedFileId, pw, unlockedFilePath);
    } else {
      if (errorMsg) {
        errorMsg.innerText = res.error || "Senha incorreta.";
        errorMsg.classList.remove("hidden");
      }
      playBeep("error");
    }
  }

  printDocument() {
    if (!this.currentFilePath) {
      showToast("Nenhum documento carregado para impressão.", "info");
      return;
    }

    playBeep("click");
    const pageCanvas = document.getElementById("pdfPageCanvas");
    const annotCanvas = document.getElementById("pdfAnnotationCanvas");

    if (!pageCanvas || !annotCanvas) {
      showToast("Erro ao preparar documento para impressão.", "error");
      return;
    }

    try {
      // 1. Cria canvas composto offscreen em alta definição
      const offscreen = document.createElement("canvas");
      offscreen.width = pageCanvas.width;
      offscreen.height = pageCanvas.height;
      const offCtx = offscreen.getContext("2d");

      // 2. Fundo branco garantido
      offCtx.fillStyle = "#ffffff";
      offCtx.fillRect(0, 0, offscreen.width, offscreen.height);

      // 3. Desenha o fundo da página renderizada
      offCtx.drawImage(pageCanvas, 0, 0);

      // 4. Desenha as anotações sobrepostas escaladas para a resolução nativa do pixmap
      offCtx.save();
      const scaleX = pageCanvas.width / annotCanvas.width;
      const scaleY = pageCanvas.height / annotCanvas.height;
      offCtx.scale(scaleX, scaleY);
      offCtx.drawImage(annotCanvas, 0, 0);
      offCtx.restore();

      // 4.5. Desenha anotações de texto e cards na imagem composta de impressão
      const list = this.annotations.get(this.currentPage) || [];
      const textItems = list.filter((item) => item.type === "text" && item.text);

      for (const item of textItems) {
        const textScaleX = offscreen.width / this.pageWidth;
        const textScaleY = offscreen.height / this.pageHeight;
        const tx = item.x * textScaleX;
        const ty = item.y * textScaleY;
        const fontSize = item.fontsize * textScaleX;

        offCtx.save();
        if (item.style && item.style !== "none") {
          const paddingX = 8 * textScaleX;
          const paddingY = 5 * textScaleY;
          const textLen = item.text.length;
          const cardW = textLen * fontSize * 0.55 + paddingX * 2 + 10;
          const cardH = fontSize * 1.5 + paddingY * 2;

          let bg = "rgba(254, 240, 138, 0.9)";
          let bar = "#eab308";
          let textColor = "#713f12";

          if (item.style === "danger") {
            bg = "rgba(254, 226, 226, 0.9)";
            bar = "#ef4444";
            textColor = "#991b1b";
          } else if (item.style === "white") {
            bg = "rgba(255, 255, 255, 0.95)";
            bar = "#0284c7";
            textColor = "#0c4a6e";
          }

          // Fundo do card
          offCtx.fillStyle = bg;
          offCtx.fillRect(tx, ty, cardW, cardH);

          // Borda esquerda destacada
          offCtx.fillStyle = bar;
          offCtx.fillRect(tx, ty, 4 * textScaleX, cardH);

          // Texto
          const fontPrefix = item.bold ? "700 " : "500 ";
          offCtx.font = `${fontPrefix}${fontSize}px "Segoe UI", "Calibri", "Arial", sans-serif`;
          offCtx.fillStyle = item.text_color || textColor;
          offCtx.textBaseline = "top";
          offCtx.fillText(item.text, tx + paddingX + 2, ty + paddingY);
        } else {
          const fontPrefix = item.bold ? "700 " : "500 ";
          offCtx.font = `${fontPrefix}${fontSize}px "Segoe UI", "Calibri", "Arial", sans-serif`;
          offCtx.fillStyle = item.text_color || "#1a1a1a";
          offCtx.textBaseline = "top";
          offCtx.fillText(item.text, tx, ty);
        }
        offCtx.restore();
      }

      const compositeDataUrl = offscreen.toDataURL("image/png");

      // 5. Injeta no iframe de impressão isolado
      let iframe = document.getElementById("pdfPrintIframe");
      if (!iframe) {
        iframe = document.createElement("iframe");
        iframe.id = "pdfPrintIframe";
        iframe.className = "hidden";
        document.body.appendChild(iframe);
      }

      const doc = iframe.contentWindow.document;
      doc.open();
      doc.write(`
        <!DOCTYPE html>
        <html lang="pt-BR">
        <head>
          <meta charset="UTF-8">
          <title>${this.currentFileName || "Imprimir Documento"} - Página ${this.currentPage + 1}</title>
          <style>
            @page {
              size: auto;
              margin: 0mm;
            }
            html, body {
              margin: 0;
              padding: 0;
              width: 100%;
              height: 100%;
              display: flex;
              justify-content: center;
              align-items: center;
              background-color: #ffffff;
              -webkit-print-color-adjust: exact;
              print-color-adjust: exact;
            }
            img {
              width: 100%;
              height: 100%;
              max-width: 100vw;
              max-height: 100vh;
              object-fit: contain;
              display: block;
            }
          </style>
        </head>
        <body></body>
        </html>
      `);
      doc.close();
      const printImage = doc.createElement("img");
      printImage.addEventListener("load", () => {
        setTimeout(() => {
          iframe.contentWindow.focus();
          iframe.contentWindow.print();
        }, 150);
      }, { once: true });
      printImage.src = compositeDataUrl;
      doc.body.appendChild(printImage);
    } catch (err) {
      console.error("Erro no motor de impressão:", err);
      showToast("Não foi possível gerar a impressão.", "error");
    }
  }
}

const superPdf = new SuperPdfController();
window.superPdf = superPdf;

// --------------------------------------------------------------------------
// Controlador de Seleção e Menu de Contexto Flutuante
// --------------------------------------------------------------------------
class SelectionController {
  constructor() {
    this.selectedText = "";
    this.menu = null;
    this._onSelectionChange = this.handleSelection.bind(this);
    this._onDocumentClick = this.handleDocClick.bind(this);
  }

  init() {
    this.menu = document.getElementById("floatingSelectionMenu");
    document.addEventListener("mouseup", this._onSelectionChange);
    document.addEventListener("keyup", this._onSelectionChange);
    document.addEventListener("mousedown", this._onDocumentClick);
  }

  handleSelection(e) {
    // Se o clique foi dentro do próprio menu flutuante, ignora
    if (this.menu && this.menu.contains(e.target)) return;

    // Aguarda micro-delay para o navegador consolidar a seleção
    setTimeout(() => {
      const sel = window.getSelection();
      const text = sel ? sel.toString().trim() : "";

      if (text.length >= 2) {
        this.selectedText = text;
        try {
          if (sel.rangeCount > 0) {
            const range = sel.getRangeAt(0);
            const rect = range.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
              const x = Math.max(120, Math.min(window.innerWidth - 120, rect.left + rect.width / 2));
              const y = Math.max(40, rect.top - 10);
              this.showMenu(x, y);
              return;
            }
          }
        } catch (err) {
          console.debug("Erro ao calcular bounding box da seleção:", err);
        }
      }
      this.hideMenu();
    }, 20);
  }

  handleDocClick(e) {
    if (this.menu && !this.menu.contains(e.target)) {
      this.hideMenu();
    }
  }

  showMenu(x, y) {
    if (!this.menu) return;
    this.menu.style.left = `${x}px`;
    this.menu.style.top = `${y}px`;
    this.menu.classList.add("visible");
  }

  hideMenu() {
    if (this.menu) {
      this.menu.classList.remove("visible");
    }
  }

  copySelectedText() {
    if (!this.selectedText) return;
    navigator.clipboard.writeText(this.selectedText);
    playBeep("success");
    showToast("Texto copiado para a área de transferência!", "success");
    this.hideMenu();
  }
}

// --------------------------------------------------------------------------
// Controlador de Síntese de Voz Neural (Edge TTS Mini-Player)
// --------------------------------------------------------------------------
class NeuralTtsController {
  constructor() {
    this.audio = new Audio();
    this.currentText = "";
    this.currentVoice = "pt-BR-FranciscaNeural";
    this.currentSpeed = 1.0;
    this.audioSegments = [];
    this.audioSegmentIndex = 0;
    this.isPlaying = false;
    this.isLoading = false;
    this.playerEl = null;
    this.waveEl = null;
    this.statusEl = null;
    this.loadingBadge = null;
    this.btnPlay = null;
    this.iconPlay = null;
    this.iconPause = null;
    this.seekbar = null;
    this.currentTimeLabel = null;
    this.totalTimeLabel = null;
    this.voiceSelect = null;
  }

  init() {
    this.playerEl = document.getElementById("neuralAudioPlayer");
    this.waveEl = document.getElementById("ttsSoundWave");
    this.statusEl = document.getElementById("ttsStatusText");
    this.loadingBadge = document.getElementById("ttsLoadingBadge");
    this.btnPlay = document.getElementById("btnTtsPlay");
    this.iconPlay = document.getElementById("iconTtsPlay");
    this.iconPause = document.getElementById("iconTtsPause");
    this.seekbar = document.getElementById("ttsSeekbar");
    this.currentTimeLabel = document.getElementById("ttsCurrentTime");
    this.totalTimeLabel = document.getElementById("ttsTotalTime");
    this.voiceSelect = document.getElementById("ttsVoiceSelect");

    // Eventos do elemento HTML5 Audio
    this.audio.addEventListener("timeupdate", () => this.onTimeUpdate());
    this.audio.addEventListener("loadedmetadata", () => this.onLoadedMetadata());
    this.audio.addEventListener("ended", () => this.onPlaybackEnded());
    this.audio.addEventListener("play", () => this.setPlayState(true));
    this.audio.addEventListener("pause", () => this.setPlayState(false));
    this.audio.addEventListener("error", (e) => this.onAudioError(e));
  }

  async playText(text) {
    const raw = (text || "").trim();
    if (!raw) {
      showToast("Nenhum texto disponível para leitura em áudio.", "info");
      return;
    }

    this.currentText = raw;
    appSelection.hideMenu();
    this.showPlayer();
    this.setLoading(true);

    if (this.statusEl) {
      const preview = raw.length > 50 ? raw.substring(0, 48) + "..." : raw;
      this.statusEl.innerText = `Gerando áudio neural: "${preview}"`;
    }

    try {
      const res = await window.pywebview.api.synthesize_speech(
        this.currentText,
        this.currentVoice,
        "+0%",
        "+0Hz"
      );

      this.setLoading(false);

      if (!res.ok) {
        showToast(res.error || "Falha na síntese de áudio.", "error");
        if (this.statusEl) this.statusEl.innerText = `Erro: ${res.error}`;
        playBeep("error");
        return;
      }

      this.audioSegments = Array.isArray(res.audio_segments) && res.audio_segments.length
        ? res.audio_segments
        : [res.audio_base64];
      this.audioSegmentIndex = 0;
      this.audio.src = this.audioSegments[0];
      this.audio.playbackRate = this.currentSpeed;
      await this.audio.play();

      const voiceName = this.voiceSelect ? this.voiceSelect.options[this.voiceSelect.selectedIndex].text : this.currentVoice;
      if (this.statusEl) {
        this.statusEl.innerText = `Lendo com voz ${voiceName}`;
      }
      playBeep("success");
    } catch (err) {
      this.setLoading(false);
      console.error("Erro no TTS:", err);
      showToast("Erro ao processar áudio neural.", "error");
    }
  }

  playSelectedText() {
    if (appSelection.selectedText) {
      this.playText(appSelection.selectedText);
    }
  }

  playSnippetText() {
    const text = document.getElementById("snippetTextArea")?.value || "";
    if (text) {
      this.playText(text);
    } else {
      showToast("O recorte não contém texto para sintetizar.", "info");
    }
  }

  togglePlay() {
    if (!this.audio.src) {
      if (this.currentText) {
        this.playText(this.currentText);
      }
      return;
    }

    if (this.audio.paused) {
      this.audio.play();
    } else {
      this.audio.pause();
    }
  }

  setPlayState(isPlaying) {
    this.isPlaying = isPlaying;
    if (this.iconPlay && this.iconPause) {
      if (isPlaying) {
        this.iconPlay.classList.add("hidden");
        this.iconPause.classList.remove("hidden");
      } else {
        this.iconPlay.classList.remove("hidden");
        this.iconPause.classList.add("hidden");
      }
    }
    if (this.waveEl) {
      if (isPlaying) {
        this.waveEl.classList.remove("paused");
      } else {
        this.waveEl.classList.add("paused");
      }
    }
  }

  setLoading(isLoading) {
    this.isLoading = isLoading;
    if (this.loadingBadge) {
      if (isLoading) {
        this.loadingBadge.classList.remove("hidden");
      } else {
        this.loadingBadge.classList.add("hidden");
      }
    }
    if (this.waveEl && isLoading) {
      this.waveEl.classList.add("paused");
    }
  }

  onTimeUpdate() {
    if (!this.audio.duration || isNaN(this.audio.duration)) return;
    const current = this.audio.currentTime;
    const duration = this.audio.duration;
    const percent = (current / duration) * 100;

    if (this.seekbar) {
      this.seekbar.value = percent;
    }
    if (this.currentTimeLabel) {
      this.currentTimeLabel.innerText = this.formatTime(current);
    }
  }

  onLoadedMetadata() {
    if (this.totalTimeLabel && this.audio.duration && !isNaN(this.audio.duration)) {
      this.totalTimeLabel.innerText = this.formatTime(this.audio.duration);
    }
  }

  onPlaybackEnded() {
    if (this.audioSegmentIndex + 1 < this.audioSegments.length) {
      this.audioSegmentIndex += 1;
      this.audio.src = this.audioSegments[this.audioSegmentIndex];
      this.audio.playbackRate = this.currentSpeed;
      this.audio.play();
      if (this.statusEl) {
        this.statusEl.innerText = `Lendo bloco ${this.audioSegmentIndex + 1} de ${this.audioSegments.length}`;
      }
      return;
    }
    this.setPlayState(false);
    if (this.seekbar) this.seekbar.value = 0;
    if (this.currentTimeLabel) this.currentTimeLabel.innerText = "0:00";
    if (this.statusEl) this.statusEl.innerText = "Leitura concluída";
  }

  onAudioError(e) {
    console.error("Erro na reprodução do áudio:", e);
    this.setPlayState(false);
    this.setLoading(false);
  }

  seek(percent) {
    if (this.audio.duration && !isNaN(this.audio.duration)) {
      this.audio.currentTime = (percent / 100) * this.audio.duration;
    }
  }

  setSpeed(speed) {
    this.currentSpeed = parseFloat(speed);
    if (this.audio) {
      this.audio.playbackRate = this.currentSpeed;
    }
    document.querySelectorAll(".tts-speed-btn").forEach((btn) => {
      if (parseFloat(btn.getAttribute("data-speed")) === this.currentSpeed) {
        btn.className = "tts-speed-btn active px-1.5 py-0.5 rounded-lg bg-white text-sky-800 shadow-xs";
      } else {
        btn.className = "tts-speed-btn px-1.5 py-0.5 rounded-lg text-slate-600 hover:text-sky-900";
      }
    });
    playBeep("click");
  }

  setVoice(voiceId) {
    this.currentVoice = voiceId;
    playBeep("click");
    // Se estiver com áudio ativo ou carregado, re-sintetiza com a nova voz
    if (this.currentText && !this.audio.paused) {
      this.playText(this.currentText);
    }
  }

  showPlayer() {
    if (this.playerEl) {
      this.playerEl.classList.add("active");
    }
  }

  stopAndHide() {
    if (this.audio) {
      this.audio.pause();
      this.audio.currentTime = 0;
    }
    this.audioSegments = [];
    this.audioSegmentIndex = 0;
    this.setPlayState(false);
    this.setLoading(false);
    if (this.playerEl) {
      this.playerEl.classList.remove("active");
    }
    playBeep("click");
  }

  formatTime(seconds) {
    if (isNaN(seconds) || seconds < 0) return "0:00";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs < 10 ? "0" : ""}${secs}`;
  }
}

// --------------------------------------------------------------------------
// Controlador de Tradução Jurídica & Multilíngue (Deep Translator)
// --------------------------------------------------------------------------
class TranslatorController {
  constructor() {
    this.modal = null;
    this.origTextEl = null;
    this.resultTextEl = null;
    this.targetLangSelect = null;
    this.statusBadge = null;
  }

  init() {
    this.modal = document.getElementById("translationModal");
    this.origTextEl = document.getElementById("transOriginalText");
    this.resultTextEl = document.getElementById("transResultText");
    this.targetLangSelect = document.getElementById("transTargetLang");
    this.statusBadge = document.getElementById("transStatusBadge");
  }

  openModal(text, targetLang = "pt") {
    const raw = (text || "").trim();
    if (!raw) {
      showToast("Nenhum texto informado para tradução.", "info");
      return;
    }

    appSelection.hideMenu();
    if (this.modal) this.modal.classList.remove("hidden");
    if (this.origTextEl) this.origTextEl.value = raw;
    if (this.targetLangSelect) this.targetLangSelect.value = targetLang;
    if (this.resultTextEl) this.resultTextEl.value = "";

    this.executeTranslation(raw, targetLang);
  }

  translateSelectedText() {
    if (appSelection.selectedText) {
      this.openModal(appSelection.selectedText);
    }
  }

  translateSnippetText() {
    const text = document.getElementById("snippetTextArea")?.value || "";
    if (text) {
      this.openModal(text);
    } else {
      showToast("O recorte não possui texto para traduzir.", "info");
    }
  }

  async retranslate() {
    const text = this.origTextEl?.value || "";
    const target = this.targetLangSelect?.value || "pt";
    if (!text.trim()) {
      showToast("Digite ou cole um texto para traduzir.", "info");
      return;
    }
    playBeep("click");
    await this.executeTranslation(text.trim(), target);
  }

  async executeTranslation(text, targetLang) {
    if (this.statusBadge) this.statusBadge.classList.remove("hidden");
    if (this.resultTextEl) {
      this.resultTextEl.placeholder = "Traduzindo trecho com alta precisão...";
    }

    try {
      const res = await window.pywebview.api.translate_text(text, targetLang, "auto");
      if (this.statusBadge) this.statusBadge.classList.add("hidden");

      if (res.ok) {
        if (this.resultTextEl) {
          this.resultTextEl.value = res.translated_text;
        }
        playBeep("success");
      } else {
        showToast(res.error || "Falha na tradução.", "error");
        if (this.resultTextEl) {
          this.resultTextEl.value = `Erro: ${res.error}`;
        }
        playBeep("error");
      }
    } catch (err) {
      if (this.statusBadge) this.statusBadge.classList.add("hidden");
      console.error("Erro na tradução:", err);
      showToast("Não foi possível completar a tradução.", "error");
    }
  }

  copyResult() {
    const resText = this.resultTextEl?.value || "";
    if (resText) {
      navigator.clipboard.writeText(resText);
      playBeep("success");
      showToast("Tradução copiada para a área de transferência!", "success");
    }
  }

  closeModal() {
    if (this.modal) this.modal.classList.add("hidden");
    playBeep("click");
  }
}

// ==========================================================================
// Global Search Controller (Acervo Pessoal & Busca Textual Instantânea FTS5)
// ==========================================================================
class GlobalSearchController {
  constructor() {
    this.modal = null;
    this.searchInput = null;
    this.resultsContainer = null;
    this.metricsLabel = null;
    this.clearBtn = null;
    this.currentFilter = "all"; // "all" | "pdf_page" | "markdown"
    this.debounceTimer = null;
    this.allResults = [];
    this.visibleResults = [];
  }

  init() {
    this.modal = document.getElementById("globalSearchModal");
    this.searchInput = document.getElementById("globalSearchInput");
    this.resultsContainer = document.getElementById("globalSearchResultsContainer");
    this.metricsLabel = document.getElementById("searchMetricsLabel");
    this.clearBtn = document.getElementById("btnClearSearchInput");

    // Atalho Universal: Ctrl + Shift + F ou Cmd + Shift + F
    window.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === "F" || e.key === "f")) {
        e.preventDefault();
        this.openModal();
      } else if (e.key === "Escape" && this.modal && !this.modal.classList.contains("hidden")) {
        this.closeModal();
      }
    });
  }

  openModal() {
    if (!this.modal) return;
    this.modal.classList.remove("hidden");
    playBeep("click");
    setTimeout(() => {
      if (this.searchInput) {
        this.searchInput.focus();
        this.searchInput.select();
      }
    }, 50);

    if (this.searchInput && this.searchInput.value.trim()) {
      this.executeSearch(this.searchInput.value.trim());
    } else {
      this.loadRecentLibrary();
    }
  }

  closeModal() {
    if (this.modal) this.modal.classList.add("hidden");
    playBeep("click");
  }

  clearInput() {
    if (this.searchInput) {
      this.searchInput.value = "";
      this.searchInput.focus();
    }
    if (this.clearBtn) this.clearBtn.classList.add("hidden");
    this.loadRecentLibrary();
  }

  onSearchInput(value) {
    if (this.clearBtn) {
      this.clearBtn.classList.toggle("hidden", !value.trim());
    }

    if (this.debounceTimer) clearTimeout(this.debounceTimer);
    this.debounceTimer = setTimeout(() => {
      const q = value.trim();
      if (q) {
        this.executeSearch(q);
      } else {
        this.loadRecentLibrary();
      }
    }, 200);
  }

  setFilter(filter) {
    this.currentFilter = filter;
    document.querySelectorAll(".search-filter-btn").forEach((btn) => {
      btn.classList.remove("bg-sky-600", "text-white", "active");
      btn.classList.add("bg-slate-100", "text-slate-600");
    });

    const activeId = filter === "all" ? "filterSearchAll" : filter === "pdf_page" ? "filterSearchPdf" : "filterSearchMd";
    const activeBtn = document.getElementById(activeId);
    if (activeBtn) {
      activeBtn.classList.remove("bg-slate-100", "text-slate-600");
      activeBtn.classList.add("bg-sky-600", "text-white", "active");
    }

    this.renderResults();
  }

  async executeSearch(query) {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const res = await window.pywebview.api.search_library(query);
      if (res.ok) {
        this.allResults = res.results || [];
        this.renderResults();
      }
    } catch (err) {
      console.error("Erro na busca global:", err);
    }
  }

  async loadRecentLibrary() {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const res = await window.pywebview.api.get_recent_library();
      if (res.ok && res.documents && res.documents.length > 0) {
        this.allResults = res.documents.map((doc) => ({
          resource_id: doc.resource_id,
          file_path: doc.file_path,
          display_path: doc.display_path || doc.file_path,
          file_name: doc.file_name,
          page_number: doc.last_page_read || 0,
          content_type: "pdf_page",
          title: `Documento Recente (${doc.page_count} págs.)`,
          snippet: `Última leitura na Página ${(doc.last_page_read || 0) + 1}.`,
          rank: 0,
          is_recent: true,
          availability_status: doc.availability_status,
          library_entry_id: doc.library_entry_id,
        }));
        this.renderResults(true);
      } else {
        this.allResults = [];
        this.renderResults(false);
      }
    } catch (err) {
      console.error("Erro ao carregar recentes:", err);
    }
  }

  renderResults(isRecentList = false) {
    if (!this.resultsContainer) return;

    let filtered = this.allResults;
    if (this.currentFilter !== "all") {
      filtered = this.allResults.filter((r) => r.content_type === this.currentFilter);
    }

    if (this.metricsLabel) {
      this.metricsLabel.innerText = isRecentList
        ? `${filtered.length} recentes no acervo`
        : `${filtered.length} resultados encontrados`;
    }

    if (filtered.length === 0) {
      this.visibleResults = [];
      this.resultsContainer.innerHTML = `
        <div class="py-12 text-center space-y-2 text-slate-400">
          <svg class="w-10 h-10 mx-auto text-sky-200" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
          <p class="text-xs font-semibold text-slate-600">Nenhum resultado para os filtros atuais.</p>
          <p class="text-[11px] text-slate-400">Tente buscar por termos sinônimos ou termos parciais.</p>
        </div>
      `;
      return;
    }

    this.visibleResults = filtered;
    this.resultsContainer.innerHTML = filtered
      .map((item, index) => {
        const isPdf = item.content_type === "pdf_page";
        const isUnavailable = isPdf && item.availability_status === "temporarily_unavailable";
        const badgeColor = isUnavailable
          ? "bg-amber-100 text-amber-800"
          : isPdf
          ? "bg-rose-100 text-rose-800"
          : "bg-indigo-100 text-indigo-800";
        const typeLabel = isUnavailable
          ? "Indisponível - Disco/Rede"
          : isPdf
          ? `PDF • Pág. ${item.page_number + 1}`
          : "Markdown";

        const actionButtons = isUnavailable
          ? `
            <div class="flex items-center gap-1.5 flex-shrink-0">
              <button data-action="appLibrary.relocateDocument('${escapeHtml(item.library_entry_id)}')" class="px-2 py-1 rounded-xl text-[10px] font-semibold bg-amber-600 hover:bg-amber-700 text-white transition flex items-center gap-1 shadow-xs">
                Localizar novamente
              </button>
              <button data-action="appLibrary.removeDocument('${escapeHtml(item.library_entry_id)}')" class="px-2 py-1 rounded-xl text-[10px] font-semibold bg-slate-200 hover:bg-rose-100 text-slate-700 hover:text-rose-700 transition">
                Remover
              </button>
            </div>
          `
          : `
            <button data-action="appSearch.openResult(${index})" class="px-2.5 py-1 rounded-xl text-[11px] font-semibold bg-sky-600 hover:bg-sky-700 text-white transition flex items-center gap-1 shadow-xs flex-shrink-0">
              <span>Abrir</span>
              <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>
            </button>
          `;

        return `
          <div class="p-3.5 rounded-2xl border border-sky-100 bg-white/90 hover:bg-sky-50/50 hover:border-sky-300 transition shadow-xs flex flex-col gap-1.5 group">
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-2 truncate">
                <span class="text-[10px] font-bold px-2 py-0.5 rounded-full ${badgeColor} flex-shrink-0">
                  ${typeLabel}
                </span>
                <span class="text-xs font-bold text-slate-800 truncate" title="${escapeHtml(item.display_path || item.file_path)}">
                  ${escapeHtml(item.file_name)}
                </span>
              </div>
              ${actionButtons}
            </div>
            <div class="text-xs text-slate-600 font-sans leading-relaxed bg-slate-50/80 p-2 rounded-xl border border-slate-100">
              ${safeSearchSnippetHtml(item.snippet || "(Correspondência no título do documento)")}
            </div>
          </div>
        `;
      })
      .join("");
  }

  async openResult(index) {
    const item = this.visibleResults[index];
    if (!item) return;

    const { resource_id: resourceId, page_number: pageNumber, content_type: contentType } = item;
    const displayPath = item.display_path || item.file_path || "";
    if (!resourceId) return;
    this.closeModal();
    if (contentType === "pdf_page") {
      switchView("superpdf");
      await superPdf.loadDocument(resourceId, null, displayPath);
      superPdf.goToPage(pageNumber);
      showToast(`Saltando para Página ${pageNumber + 1}`, "info");
    } else {
      await previewSpecificMarkdown(resourceId, displayPath);
      showToast("Visualizando documento no acervo.", "info");
    }
  }
}

// --------------------------------------------------------------------------
// Gerenciador do Acervo e Arquivos Indisponíveis (Item 16)
// --------------------------------------------------------------------------
const appLibrary = {
  async relocateDocument(libraryEntryId) {
    playBeep("click");
    const selected = await window.pywebview.api.choose_files();
    if (!selected || selected.length === 0) return;
    const res = await window.pywebview.api.relocate_library_document(libraryEntryId, selected[0].file_id);
    if (res.ok) {
      showToast("Localização do arquivo atualizada no acervo!", "success");
      if (window.appSearch) appSearch.loadRecentLibrary();
    } else {
      showToast(`Falha ao relocalizar: ${res.error}`, "error");
    }
  },

  async removeDocument(libraryEntryId) {
    playBeep("click");
    const res = await window.pywebview.api.remove_library_document(libraryEntryId);
    if (res.ok) {
      showToast("Documento removido do acervo.", "info");
      if (window.appSearch) appSearch.loadRecentLibrary();
    } else {
      showToast(`Falha ao remover: ${res.error}`, "error");
    }
  }
};
window.appLibrary = appLibrary;

// --------------------------------------------------------------------------
// Controlador de Licença e Ativação por Hardware (Hardware Node-Locking)
// --------------------------------------------------------------------------
class LicenseManager {
  constructor() {
    this.isActivated = false;
    this.machineId = "";
  }

  async checkActivation() {
    if (!window.pywebview || !window.pywebview.api) return;
    try {
      const res = await window.pywebview.api.get_license_info();
      if (res) {
        this.isActivated = !!res.is_activated;
        this.machineId = res.machine_id || "";

        const midInput = document.getElementById("activationMachineId");
        if (midInput) midInput.value = this.machineId;

        const modal = document.getElementById("activationModal");
        if (!this.isActivated) {
          if (modal) modal.classList.remove("hidden");
        } else {
          if (modal) modal.classList.add("hidden");
          setTimeout(() => checkWelcomeGuide(), 150);
        }
      }
    } catch (err) {
      console.error("Erro ao verificar ativação:", err);
    }
  }

  openModal() {
    const modal = document.getElementById("activationModal");
    if (modal) modal.classList.remove("hidden");
    const midInput = document.getElementById("activationMachineId");
    if (midInput && this.machineId) midInput.value = this.machineId;
    const keyInput = document.getElementById("inputActivationKey");
    if (keyInput) setTimeout(() => keyInput.focus(), 60);
  }

  copyMachineId() {
    if (!this.machineId) return;
    navigator.clipboard.writeText(this.machineId);
    playBeep("success");
    showToast("Código da Máquina copiado para a área de transferência!", "success");
  }

  async submitActivation() {
    const keyInput = document.getElementById("inputActivationKey");
    const key = (keyInput ? keyInput.value : "").trim().toUpperCase();
    const alertBox = document.getElementById("activationAlertBox");
    const btn = document.getElementById("btnSubmitActivation");

    if (!key) {
      if (alertBox) {
        alertBox.className = "p-3 rounded-2xl text-xs font-semibold bg-rose-50 text-rose-800 border border-rose-200 animate-fadeIn";
        alertBox.innerText = "Por favor, digite ou cole a Chave de Ativação.";
        alertBox.classList.remove("hidden");
      }
      playBeep("error");
      return;
    }

    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `<span class="inline-block animate-spin mr-2">⏳</span> Validando Chave...`;
    }

    try {
      const res = await window.pywebview.api.activate_software(key);
      if (res.ok) {
        this.isActivated = true;
        playBeep("success");
        if (alertBox) {
          alertBox.className = "p-3 rounded-2xl text-xs font-semibold bg-emerald-50 text-emerald-800 border border-emerald-200 animate-fadeIn";
          alertBox.innerText = res.message || "NexoJuris ativado com sucesso! Acesso completo liberado.";
          alertBox.classList.remove("hidden");
        }
        setTimeout(() => {
          const modal = document.getElementById("activationModal");
          if (modal) modal.classList.add("hidden");
          showToast("NexoJuris Ativado com Sucesso!", "success");
          setTimeout(() => checkWelcomeGuide(), 150);
        }, 1200);
      } else {
        playBeep("error");
        if (alertBox) {
          alertBox.className = "p-3 rounded-2xl text-xs font-semibold bg-rose-50 text-rose-800 border border-rose-200 animate-fadeIn";
          alertBox.innerText = res.error || "Chave de ativação inválida para este computador.";
          alertBox.classList.remove("hidden");
        }
      }
    } catch (err) {
      playBeep("error");
      if (alertBox) {
        alertBox.className = "p-3 rounded-2xl text-xs font-semibold bg-rose-50 text-rose-800 border border-rose-200 animate-fadeIn";
        alertBox.innerText = `Erro na ativação: ${err}`;
        alertBox.classList.remove("hidden");
      }
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = `
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"></path></svg>
          <span>Ativar NexoJuris Agora</span>
        `;
      }
    }
  }
}

class TermsManager {
  constructor() {
    this.scrolledToBottom = false;
    this.checkbox = null;
    this.button = null;
    this.scrollArea = null;
    this.notice = null;
    this.lbl = null;
    this.eventsBound = false;
  }

  init() {
    this.checkbox = document.getElementById("chkAcceptTerms");
    this.button = document.getElementById("btnAcceptTerms");
    this.scrollArea = document.getElementById("termsScrollArea");
    this.notice = document.getElementById("termsScrollNotice");
    this.lbl = document.getElementById("lblAcceptTerms");

    if (!this.scrollArea) return;

    // Reset initial states
    this.scrolledToBottom = false;
    this.checkbox.checked = false;
    this.checkbox.disabled = true;
    this.checkbox.style.cursor = "not-allowed";
    this.lbl.style.cursor = "not-allowed";
    this.lbl.className = "text-[11px] font-semibold text-slate-400 cursor-not-allowed";
    this.button.disabled = true;
    this.button.className = "w-full py-3 rounded-2xl font-bold text-xs bg-slate-200 text-slate-400 cursor-not-allowed transition flex items-center justify-center gap-2";
    this.notice.innerText = "Por favor, role os termos de uso até o fim para liberar as opções de consentimento.";
    this.notice.className = "text-center text-[10px] text-amber-600 font-semibold bg-amber-50 border border-amber-100 p-2 rounded-xl";

    if (!this.eventsBound) {
      this.scrollArea.addEventListener("scroll", () => this.handleScroll());
      this.checkbox.addEventListener("change", () => this.handleCheckboxChange());
      this.eventsBound = true;
    }
    
    // Check if scrollbar is not needed (e.g. large screen/height) and enable directly
    setTimeout(() => this.handleScroll(), 100);

  }

  handleScroll() {
    if (this.scrolledToBottom) return;

    const threshold = 15; // pixels buffer
    const position = this.scrollArea.scrollHeight - this.scrollArea.scrollTop - this.scrollArea.clientHeight;

    if (position <= threshold) {
      this.scrolledToBottom = true;
      this.checkbox.disabled = false;
      this.checkbox.style.cursor = "pointer";
      this.lbl.style.cursor = "pointer";
      this.lbl.className = "text-[11px] font-semibold text-slate-700 cursor-pointer";
      this.notice.innerText = "Leitura concluída. Agora marque a caixa abaixo e clique em Aceitar e Prosseguir.";
      this.notice.className = "text-center text-[10px] text-emerald-600 font-semibold bg-emerald-50 border border-emerald-100 p-2 rounded-xl animate-pulse";
    }
  }

  handleCheckboxChange() {
    if (this.checkbox.checked && this.scrolledToBottom) {
      this.button.disabled = false;
      this.button.className = "w-full py-3 rounded-2xl font-bold text-xs bg-gradient-to-r from-sky-600 to-blue-700 hover:from-sky-500 hover:to-blue-600 text-white shadow-lg shadow-sky-500/25 transition flex items-center justify-center gap-2 cursor-pointer";
    } else {
      this.button.disabled = true;
      this.button.className = "w-full py-3 rounded-2xl font-bold text-xs bg-slate-200 text-slate-400 cursor-not-allowed transition flex items-center justify-center gap-2";
    }
  }

  async confirmAcceptance() {
    if (!this.checkbox.checked || !this.scrolledToBottom) return;
    try {
      playBeep("success");
      const res = await window.pywebview.api.accept_terms();
      if (res && res.ok) {
        const modal = document.getElementById("termsModal");
        if (modal) modal.classList.add("hidden");
        showToast("Termos de Uso aceitos formalmente.", "success");
        // Dá sequência à inicialização do app
        await initializeAfterTerms();
      } else {
        showToast("Erro ao registrar o aceite dos termos.", "error");
      }
    } catch (err) {
      console.error("Erro ao aceitar termos:", err);
      showToast("Falha na comunicação com o backend.", "error");
    }
  }
}

class WelcomeManager {
  constructor() {
    this.modal = null;
    this.currentTab = 1;
  }

  init() {
    this.modal = document.getElementById("welcomeModal");
  }

  open() {
    this.init();
    if (this.modal) {
      this.modal.classList.remove("hidden");
      this.switchTab(1);
    }
  }

  close() {
    this.init();
    if (this.modal) {
      this.modal.classList.add("hidden");
    }
    localStorage.setItem("has_seen_welcome_guide", "true");
    playBeep("click");
  }

  switchTab(tabNum) {
    this.currentTab = tabNum;
    for (let i = 1; i <= 3; i++) {
      const btn = document.getElementById(`welcomeTabBtn${i}`);
      const content = document.getElementById(`welcomeTabContent${i}`);
      if (i === tabNum) {
        if (btn) {
          btn.className = "flex-1 pb-2.5 text-center border-b-2 border-sky-600 text-sky-600 transition-all focus:outline-none font-bold";
        }
        if (content) {
          content.classList.remove("hidden");
        }
      } else {
        if (btn) {
          btn.className = "flex-1 pb-2.5 text-center border-b-2 border-transparent text-slate-500 hover:text-slate-800 transition-all focus:outline-none";
        }
        if (content) {
          content.classList.add("hidden");
        }
      }
    }
    playBeep("click");
  }
}

class ManualManager {
  constructor() {
    this.modal = null;
    this.viewMode = "html"; // "html" | "markdown"
  }

  init() {
    this.modal = document.getElementById("manualModal");
  }

  open() {
    this.init();
    if (this.modal) {
      this.modal.classList.remove("hidden");
      this.viewMode = "html";
      document.getElementById("manualHtmlView").classList.remove("hidden");
      document.getElementById("manualMarkdownView").classList.add("hidden");
      document.getElementById("txtToggleManualView").innerText = "Markdown Puro";
      this.scrollToChapter("cap1");
    }
  }

  close() {
    this.init();
    if (this.modal) {
      this.modal.classList.add("hidden");
    }
    playBeep("click");
  }

  scrollToChapter(chapId) {
    const el = document.getElementById(`manualSec-${chapId}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    // Update active nav button
    const navButtons = document.querySelectorAll(".manual-nav-btn");
    navButtons.forEach(btn => {
      if (btn.id === `manualNav-${chapId}`) {
        btn.className = "manual-nav-btn w-full text-left px-2 py-1.5 rounded-xl text-[11px] font-bold text-sky-700 bg-sky-50 flex items-center gap-1.5 transition";
        const dot = btn.querySelector("span");
        if (dot) dot.className = "w-1.5 h-1.5 rounded-full bg-sky-500";
      } else {
        btn.className = "manual-nav-btn w-full text-left px-2 py-1.5 rounded-xl text-[11px] font-semibold text-slate-600 hover:bg-slate-100 flex items-center gap-1.5 transition";
        const dot = btn.querySelector("span");
        if (dot) dot.className = "w-1.5 h-1.5 rounded-full bg-slate-300";
      }
    });
    playBeep("click");
  }

  toggleViewMode() {
    const htmlView = document.getElementById("manualHtmlView");
    const mdView = document.getElementById("manualMarkdownView");
    const txtLabel = document.getElementById("txtToggleManualView");
    const textarea = document.getElementById("manualMarkdownTextarea");

    if (this.viewMode === "html") {
      this.viewMode = "markdown";
      htmlView.classList.add("hidden");
      mdView.classList.remove("hidden");
      txtLabel.innerText = "Modo Formatado";
      textarea.value = this.getRawMarkdownContent();
    } else {
      this.viewMode = "html";
      htmlView.classList.remove("hidden");
      mdView.classList.add("hidden");
      txtLabel.innerText = "Markdown Puro";
    }
    playBeep("click");
  }

  filterContent() {
    const query = document.getElementById("manualSearchInput").value.toLowerCase().trim();
    const sections = document.querySelectorAll(".manual-section");

    sections.forEach(sec => {
      const text = sec.innerText.toLowerCase();
      if (text.includes(query)) {
        sec.classList.remove("hidden");
      } else {
        sec.classList.add("hidden");
      }
    });
  }

  printManual() {
    window.print();
  }

  getRawMarkdownContent() {
    return `# NexoJuris v1.3.2 - Manual de Instruções

## Capítulo 1: Introdução, Arquitetura Local e Privacidade
Conversão, indexação SQLite, OCR e renderização de PDF ocorrem localmente no computador.
Dica: Os arquivos PDF e as imagens não são enviados à nuvem. Tradução e voz são opcionais, exigem internet e enviam somente o texto selecionado ao serviço externo correspondente.

## Capítulo 2: Motor de Conversão (Jurisprudência vs Curso, Híbrido)
Processador híbrido inteligente que detecta texto vetorial nativo e aciona OCR local apenas em imagens ou páginas digitalizadas.
- Perfil Jurisprudência: Estruturação ideal para STF/STJ.
- Perfil Material de Curso: Limpeza de ruídos e títulos espúrios de OCR.

## Capítulo 3: Visualizador de Markdown e Integração com Windows
O arquivo Markdown gerado pode ser lido imediatamente com realce de sintaxe na aba correspondente. A integração com o Windows permite clicar com o botão direito no arquivo PDF no Explorer e escolher "Enviar para -> NexoJuris".

## Capítulo 4: Super Leitor, Snippet/OCR, Voz e Tradutor
Visualize PDFs grandes em alta resolução. Adicione notas, canetas e marca-textos salvas nativamente no arquivo. O OCR de recorte é local; tradução e síntese de voz usam, respectivamente, Google Translator e Microsoft Edge TTS e exigem internet.

## Capítulo 5: Acervo Pessoal (Busca Textual SQLite FTS5)
Banco de dados indexado localmente. Pesquisa textual instantânea usando algoritmo BM25 com destaque do termo procurado em todos os documentos convertidos ou inspecionados.

## Capítulo 6: Criptografia AES-256, Senhas e Licenciamento
Proteção e desproteção de PDFs locais com criptografia AES-256 comercial. O licenciamento é vinculado ao hardware (Node-locking) 100% offline.`;
  }
}

function checkWelcomeGuide() {
  const hasSeen = localStorage.getItem("has_seen_welcome_guide");
  if (!hasSeen) {
    appWelcome.open();
  }
}

// Instâncias Globais
const appSelection = new SelectionController();
const appTts = new NeuralTtsController();
const appTranslator = new TranslatorController();
const appSearch = new GlobalSearchController();
const appLicense = new LicenseManager();
const appTerms = new TermsManager();
const appWelcome = new WelcomeManager();
const appManual = new ManualManager();

window.appSelection = appSelection;
window.appTts = appTts;
window.appTranslator = appTranslator;
window.appSearch = appSearch;
window.appLicense = appLicense;
window.appTerms = appTerms;
window.appWelcome = appWelcome;
window.appManual = appManual;

