/* AskCSV frontend — Enterprise AI Data Analyst
   Supports: Multi-file CSV/XLSX/XLS, Spreadsheet Grid, Auto-Dashboard, Data Quality,
   Time-Series Forecasting, Executive Reports, Observability, and Gemini/OpenRouter/Groq AI switching.
*/

const $ = (sel) => document.querySelector(sel);
const state = {
  sessionId: null,
  mode: "demo",           // "gemini", "openrouter", "groq", "demo"
  model: "",
  geminiConfigured: false,
  openrouterConfigured: false,
  groqConfigured: false,
  tables: [],
  activeTable: null,
  activeView: "chat",     // "chat", "upload", "viewer", "dashboard", "quality", "forecast", "report", "observability"
  tablePreviews: {},     // in-memory & cached preview data { tableName: { columns, rows, total_rows, schema } }
  tabulatorInstance: null,
  busy: false,
  configData: null,
};

const EXAMPLES = [
  "Which region generated the highest revenue?",
  "Show monthly sales trends as a bar chart",
  "Which products are underperforming?",
  "What are the top five customers?",
  "Detect anomalies in the dataset",
];

const LS_SESSION_KEY = "askcsv_session_v3";
const LS_PREVIEW_PREFIX = "askcsv_prev_v3_";
const LS_API_BASE_KEY = "askcsv_api_base_v1";

function getApiBase() {
  return (localStorage.getItem(LS_API_BASE_KEY) || window.ASKCSV_API_BASE || "").trim();
}

function getApiUrl(endpoint) {
  const base = getApiBase().replace(/\/+$/, "");
  const cleanEndpoint = endpoint.startsWith("/") ? endpoint : "/" + endpoint;
  return base ? base + cleanEndpoint : cleanEndpoint;
}

/* ------------------------------------------------------------------ utils */
function escapeHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function md(text) {
  const inline = (s) => escapeHtml(s)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  const lines = String(text).split("\n");
  let html = "", inList = false, para = [];
  const flushPara = () => {
    if (para.length) { html += `<p>${inline(para.join(" "))}</p>`; para = []; }
  };
  for (const line of lines) {
    const t = line.trim();
    if (!t) { flushPara(); if (inList) { html += "</ul>"; inList = false; } continue; }
    if (/^#{1,4}\s/.test(t)) {
      flushPara(); if (inList) { html += "</ul>"; inList = false; }
      html += `<h4>${inline(t.replace(/^#+\s/, ""))}</h4>`;
    } else if (/^[-•]\s/.test(t)) {
      flushPara();
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${inline(t.replace(/^[-•]\s/, ""))}</li>`;
    } else {
      if (inList) { html += "</ul>"; inList = false; }
      para.push(t);
    }
  }
  flushPara(); if (inList) html += "</ul>";
  return html;
}

let toastTimer = null;
function toast(msg, kind) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  t.style.color = kind === "warn" ? "#ffb4a3" : "#eaf1ff";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 2800);
}

function scrollBottom() {
  const c = $("#chat-messages-container") || $("#chat");
  if (c) c.scrollTop = c.scrollHeight;
}

const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString("en-IN"));

/* ------------------------------------------------------------------ storage & caching */
function saveSessionToStorage() {
  if (!state.sessionId) return;
  const payload = {
    sessionId: state.sessionId,
    tables: state.tables,
    activeTable: state.activeTable,
    mode: state.mode,
    model: state.model,
    timestamp: Date.now(),
  };
  try {
    localStorage.setItem(LS_SESSION_KEY, JSON.stringify(payload));
  } catch (e) { }
}

function restoreSessionFromStorage() {
  try {
    const raw = localStorage.getItem(LS_SESSION_KEY);
    if (!raw) return false;
    const data = JSON.parse(raw);
    if (data && data.sessionId && Array.isArray(data.tables) && data.tables.length) {
      state.sessionId = data.sessionId;
      state.tables = data.tables;
      state.activeTable = data.activeTable || (data.tables[0] ? data.tables[0].table : null);
      if (data.mode) state.mode = data.mode;
      if (data.model) state.model = data.model;
      return true;
    }
  } catch (e) { }
  return false;
}

function clearCacheAndReset() {
  try {
    localStorage.removeItem(LS_SESSION_KEY);
    Object.keys(localStorage).forEach((k) => {
      if (k.startsWith(LS_PREVIEW_PREFIX)) localStorage.removeItem(k);
    });
  } catch (e) { }
  location.reload();
}

function getCachedPreview(table) {
  if (state.tablePreviews[table]) return state.tablePreviews[table];
  try {
    const raw = localStorage.getItem(LS_PREVIEW_PREFIX + table);
    if (raw) {
      const data = JSON.parse(raw);
      state.tablePreviews[table] = data;
      return data;
    }
  } catch (e) { }
  return null;
}

function setCachedPreview(table, data) {
  state.tablePreviews[table] = data;
  try {
    localStorage.setItem(LS_PREVIEW_PREFIX + table, JSON.stringify(data));
  } catch (e) { }
}

/* ------------------------------------------------------------------ toggle panel */
function toggleDataProfile(forceState) {
  const shell = $(".shell");
  const sidebarRight = $("#sidebar-right");
  const toggleBtn = $("#btn-toggle-profile");
  if (!shell || !sidebarRight) return;

  const isCollapsed = sidebarRight.classList.contains("collapsed");
  const shouldCollapse = forceState !== undefined ? !forceState : !isCollapsed;

  if (shouldCollapse) {
    shell.classList.add("no-profile");
    sidebarRight.classList.add("collapsed");
    if (toggleBtn) {
      toggleBtn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px">right_panel_open</span>Inspector';
      toggleBtn.classList.add("ghost");
    }
    try { localStorage.setItem("askcsv_profile_collapsed", "true"); } catch (e) {}
  } else {
    shell.classList.remove("no-profile");
    sidebarRight.classList.remove("collapsed");
    if (toggleBtn) {
      toggleBtn.innerHTML = '<span class="material-symbols-outlined" style="font-size:16px">right_panel_close</span>Inspector';
      toggleBtn.classList.remove("ghost");
    }
    try { localStorage.setItem("askcsv_profile_collapsed", "false"); } catch (e) {}
  }
}

function restoreProfileToggleState() {
  try {
    const collapsed = localStorage.getItem("askcsv_profile_collapsed") === "true";
    if (collapsed) {
      toggleDataProfile(false);
    }
  } catch (e) {}
}

/* ------------------------------------------------------------------ init */
async function init() {
  await fetchHealthAndConfig();
  const restored = restoreSessionFromStorage();
  updateModePill();
  updateTableCountBadge();
  restoreProfileToggleState();
  bind();

  if (restored && state.tables.length) {
    renderTables();
    $("#welcome").classList.add("hidden");
    $("#chat").classList.remove("hidden");
    $("#composer").classList.remove("hidden");
    if (state.activeTable) {
      const activeObj = state.tables.find((t) => t.table === state.activeTable) || state.tables[0];
      if (activeObj) renderProfile(activeObj);
    }
    const msgContainer = $("#chat-messages-container") || $("#chat");
    if (!msgContainer.children.length) addAssistantGreeting();
    toast(`Restored ${state.tables.length} cached dataset${state.tables.length > 1 ? "s" : ""}`);
  }
}

async function fetchHealthAndConfig() {
  try {
    const r = await fetch(getApiUrl("/api/config"));
    if (r.ok) {
      state.configData = await r.json();
      if (!state.mode || state.mode === "demo") state.mode = state.configData.active_provider;
      if (!state.model) state.model = state.configData.active_model;
      state.geminiConfigured = state.configData.gemini.configured;
      state.openrouterConfigured = state.configData.openrouter.configured;
      state.groqConfigured = state.configData.groq.configured;
    }
  } catch {
    state.mode = "demo";
  }
}

function updateModePill() {
  const btn = $("#btn-api-switch");
  const lbl = $("#mode-label");
  btn.className = "mode-pill-btn " + state.mode;
  const cm = $("#composer-model");
  if (state.mode === "gemini") {
    lbl.textContent = `Gemini (${state.model || "3.6-flash"})`;
  } else if (state.mode === "openrouter") {
    const shortModel = (state.model || "gpt-4o-mini").split("/").pop();
    lbl.textContent = `OpenRouter (${shortModel})`;
  } else if (state.mode === "groq") {
    const shortModel = (state.model || "openai/gpt-oss-20b").split("/").pop();
    lbl.textContent = `Groq (${shortModel})`;
  } else {
    lbl.textContent = "Offline Demo Mode";
  }
  if (cm) cm.textContent = lbl.textContent;
}

function updateActiveTableUi() {
  const name = state.activeTable || "";
  const pill = $("#active-pill");
  if (pill) pill.classList.toggle("hidden", !name);
  const pn = $("#active-pill-name");
  if (pn) pn.textContent = name;
  const sc = $("#composer-scope");
  if (sc) sc.textContent = "SCOPE: " + (name || "—");
}

const VIEW_TITLES = {
  chat: "Chat", upload: "Upload & Datasets", viewer: "Spreadsheet", dashboard: "Dashboard",
  quality: "Data Quality", forecast: "Forecast", report: "Report", observability: "Observability",
};

function setNavOpen(open) {
  $("#nav").classList.toggle("open", open);
  $("#nav-backdrop").classList.toggle("open", open);
}

function updateTableCountBadge() {
  const badge = $("#table-count-badge");
  if (state.tables.length > 0) {
    badge.textContent = state.tables.length;
    badge.style.display = "inline-block";
  } else {
    badge.style.display = "none";
  }
}

/* ------------------------------------------------------------------ event bindings */
function bind() {
  // Sidebar Upload & Sample
  $("#btn-upload").onclick = () => $("#file-input").click();
  $("#file-input").onchange = (e) => { uploadFiles([...e.target.files]); e.target.value = ""; };
  $("#btn-sample").onclick = loadSample;
  $("#btn-reset").onclick = clearCacheAndReset;
  $("#btn-new-thread").onclick = () => {
    if (!state.sessionId) return toast("Upload or load a dataset first.", "warn");
    switchTab("chat");
    handleNewChat();
  };
  $("#btn-nav-toggle").onclick = () => setNavOpen(true);
  $("#nav-backdrop").onclick = () => setNavOpen(false);

  // Welcome Dropzone
  const dz = $("#dropzone");
  if (dz) {
    dz.onclick = () => $("#file-input").click();
    ["dragover", "dragenter"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.add("over"); }));
    ["dragleave", "drop"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
    dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("over"); uploadFiles([...e.dataTransfer.files]); });
  }

  // Upload Tab Dropzone & Buttons
  $("#btn-upload-page-action").onclick = () => $("#file-input").click();
  $("#btn-sample-page-action").onclick = loadSample;
  const dzUploadTab = $("#dropzone-upload-tab");
  if (dzUploadTab) {
    dzUploadTab.onclick = () => $("#file-input").click();
    ["dragover", "dragenter"].forEach((t) => dzUploadTab.addEventListener(t, (e) => { e.preventDefault(); dzUploadTab.classList.add("over"); }));
    ["dragleave", "drop"].forEach((t) => dzUploadTab.addEventListener(t, (e) => { e.preventDefault(); dzUploadTab.classList.remove("over"); }));
    dzUploadTab.addEventListener("drop", (e) => { e.preventDefault(); dzUploadTab.classList.remove("over"); uploadFiles([...e.dataTransfer.files]); });
  }

  // Composer in chat
  $("#composer").onsubmit = (e) => {
    e.preventDefault();
    const v = $("#input").value.trim();
    if (v && !state.busy) { $("#input").value = ""; send(v); }
  };

  // Tabs Navigation
  document.querySelectorAll(".tab").forEach((t) => {
    t.onclick = () => switchTab(t.dataset.view);
  });

  // Spreadsheet Toolbar events
  $("#viewer-search-input").oninput = (e) => {
    const q = e.target.value.trim();
    if (state.tabulatorInstance) {
      if (!q) {
        state.tabulatorInstance.clearFilter();
      } else {
        state.tabulatorInstance.setFilter((data) => {
          return Object.values(data).some((val) => String(val ?? "").toLowerCase().includes(q.toLowerCase()));
        });
      }
    }
  };

  $("#viewer-page-size").onchange = (e) => {
    const size = parseInt(e.target.value, 10);
    if (state.tabulatorInstance) state.tabulatorInstance.setPageSize(size);
  };

  $("#btn-export-csv").onclick = () => {
    if (state.tabulatorInstance) {
      state.tabulatorInstance.download("csv", `${state.activeTable || "data"}_export.csv`);
      toast("Downloading CSV export…");
    }
  };

  $("#btn-viewer-ask").onclick = () => {
    switchTab("chat");
    $("#input").value = `Analyze table ${state.activeTable || ""}`;
    $("#input").focus();
  };

  // Toggle profile panel
  const toggleProfileBtn = $("#btn-toggle-profile");
  const closeProfileBtn = $("#btn-close-profile");
  if (toggleProfileBtn) toggleProfileBtn.onclick = () => toggleDataProfile();
  if (closeProfileBtn) closeProfileBtn.onclick = () => toggleDataProfile(false);

  // New Chat & Clear Chat buttons
  const newChatBtn = $("#btn-new-chat");
  const clearChatBtn = $("#btn-clear-chat");
  if (newChatBtn) newChatBtn.onclick = handleNewChat;
  if (clearChatBtn) clearChatBtn.onclick = handleClearChat;

  // Report Actions
  $("#btn-download-report-md").onclick = () => {
    if (window._currentReportMd) {
      const blob = new Blob([window._currentReportMd], { type: "text/markdown;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `Executive_Report_${state.activeTable || "dataset"}.md`;
      a.click();
      toast("Downloaded Executive Report");
    }
  };
  $("#btn-print-report").onclick = () => window.print();

  // Observability Refresh
  $("#btn-refresh-logs").onclick = () => renderObservabilityView();

  // API / Model Switcher Modal
  $("#btn-api-switch").onclick = openApiModal;
  $("#btn-close-modal").onclick = closeApiModal;
  $("#btn-cancel-modal").onclick = closeApiModal;
  $("#btn-save-modal").onclick = applyApiModal;

  // Close modal on outside click or Escape
  $("#modal-api-switch").onclick = (e) => {
    if (e.target.id === "modal-api-switch") closeApiModal();
  };
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeApiModal();
  });

  document.querySelectorAll(".provider-btn").forEach((btn) => {
    btn.onclick = () => selectModalProvider(btn.dataset.provider);
  });

  $("#select-model-presets").onchange = (e) => {
    if (e.target.value) $("#input-model").value = e.target.value;
  };
}

function switchTab(view) {
  state.activeView = view;
  const ttl = $("#page-title");
  if (ttl) ttl.textContent = VIEW_TITLES[view] || "AskCSV";
  setNavOpen(false);
  updateActiveTableUi();
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  
  const allViews = [
    { id: "#welcome", show: view === "chat" && !state.sessionId },
    { id: "#chat", show: view === "chat" && !!state.sessionId },
    { id: "#view-upload", show: view === "upload" },
    { id: "#view-viewer", show: view === "viewer" },
    { id: "#view-dashboard", show: view === "dashboard" },
    { id: "#view-quality", show: view === "quality" },
    { id: "#view-forecast", show: view === "forecast" },
    { id: "#view-report", show: view === "report" },
    { id: "#view-observability", show: view === "observability" },
    { id: "#composer", show: view === "chat" && !!state.sessionId },
  ];

  allViews.forEach((v) => {
    const el = $(v.id);
    if (el) el.classList.toggle("hidden", !v.show);
  });

  if (view === "upload") renderUploadView();
  else if (view === "viewer") renderDataViewer();
  else if (view === "dashboard") renderDashboardView();
  else if (view === "quality") renderQualityView();
  else if (view === "forecast") renderForecastView();
  else if (view === "report") renderReportView();
  else if (view === "observability") renderObservabilityView();
}

/* ------------------------------------------------------------------ API Modal */
let modalSelectedProvider = "openrouter";

function openApiModal() {
  modalSelectedProvider = state.mode;
  selectModalProvider(modalSelectedProvider);
  const inputApiBase = $("#input-api-base");
  if (inputApiBase) inputApiBase.value = getApiBase();
  const modal = $("#modal-api-switch");
  modal.style.display = "flex";
  modal.classList.remove("hidden");
}

function closeApiModal() {
  const modal = $("#modal-api-switch");
  modal.style.display = "none";
  modal.classList.add("hidden");
}

function selectModalProvider(prov) {
  modalSelectedProvider = prov;
  document.querySelectorAll(".provider-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.provider === prov);
  });

  const groupModel = $("#group-model-select");
  const groupKey = $("#group-api-key");
  const keyStatus = $("#api-key-status");
  const selectPresets = $("#select-model-presets");
  const inputModel = $("#input-model");

  if (prov === "demo") {
    groupModel.classList.add("hidden");
    groupKey.classList.add("hidden");
    return;
  }

  groupModel.classList.remove("hidden");
  groupKey.classList.remove("hidden");

  selectPresets.innerHTML = '<option value="">-- Choose Preset Model --</option>';
  const presets = prov === "gemini"
    ? (state.configData?.gemini?.models || ["gemini-3.6-flash", "gemini-3.7-flash", "gemini-2.5-pro"])
    : prov === "groq"
      ? (state.configData?.groq?.models || ["openai/gpt-oss-20b"])
      : (state.configData?.openrouter?.models || [
        "openai/gpt-4o-mini",
        "openai/gpt-4o",
        "anthropic/claude-sonnet-5.5",
        "deepseek/deepseek-chat",
        "meta-llama/llama-3.3-70b-instruct",
        "google/gemini-2.5-flash",
      ]);

  presets.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m;
    selectPresets.appendChild(opt);
  });

  const defaultModel = prov === "gemini"
    ? (state.configData?.gemini?.model || "gemini-3.6-flash")
    : prov === "groq"
      ? (state.configData?.groq?.model || "openai/gpt-oss-20b")
      : (state.configData?.openrouter?.model || "openai/gpt-4o-mini");

  inputModel.value = (state.mode === prov && state.model) ? state.model : defaultModel;

  const isConfigured = prov === "gemini" ? state.geminiConfigured
    : prov === "groq" ? state.groqConfigured : state.openrouterConfigured;
  const providerName = prov === "gemini" ? "Gemini" : prov === "groq" ? "Groq" : "OpenRouter";
  keyStatus.innerHTML = isConfigured
    ? `<span style="color:var(--green)">✓ ${providerName} API Key is active</span>`
    : `<span style="color:var(--amber)">⚠ No API key found in .env. Enter one above to unlock.</span>`;
}

async function applyApiModal() {
  const prov = modalSelectedProvider;
  const model = $("#input-model").value.trim();
  const apiKey = $("#input-api-key").value.trim();
  const apiBase = $("#input-api-base") ? $("#input-api-base").value.trim() : "";

  try {
    localStorage.setItem(LS_API_BASE_KEY, apiBase);
  } catch (e) {}

  const payload = {
    provider: prov,
    model: model || undefined,
    api_key: apiKey || undefined,
    session_id: state.sessionId || undefined,
  };

  try {
    const res = await api("/api/config/switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (res && res.ok) {
      state.mode = res.mode;
      state.model = res.model;
      state.geminiConfigured = res.gemini_configured;
      state.openrouterConfigured = res.openrouter_configured;
      state.groqConfigured = res.groq_configured;
      saveSessionToStorage();
      updateModePill();
      closeApiModal();
      toast(`Switched to ${res.mode.toUpperCase()} (${res.model})`);
    } else {
      closeApiModal();
      if (apiBase) toast(`Saved Backend URL: ${apiBase}`);
    }
  } catch (e) {
    toast("Error switching config: " + e.message, "warn");
  }
}

/* ------------------------------------------------------------------ API */
async function api(url, opts) {
  try {
    const fullUrl = getApiUrl(url);
    const r = await fetch(fullUrl, opts);
    if (!r.ok) {
      const e = await r.json().catch(() => ({ detail: r.statusText }));
      toast(e.detail || `Request failed (${r.status})`, "warn");
      return null;
    }
    return await r.json();
  } catch (e) {
    toast(`Network error: ${e.message}. Set Backend API URL in ⚙️ Settings if hosted remotely.`, "warn");
    return null;
  }
}

async function uploadFiles(files) {
  const validFiles = files.filter((f) => {
    const ext = f.name.toLowerCase();
    return ext.endsWith(".csv") || ext.endsWith(".xlsx") || ext.endsWith(".xls");
  });
  if (!validFiles.length) return toast("Supported formats: .csv, .xlsx, .xls", "warn");
  
  const fd = new FormData();
  validFiles.forEach((f) => fd.append("files", f));
  toast("Uploading & processing files…");
  const res = await api("/api/upload", { method: "POST", body: fd });
  if (res) applySession(res);
}

async function loadSample() {
  const res = await api("/api/sample", { method: "POST" });
  if (res) applySession(res);
}

function applySession(res) {
  state.sessionId = res.session_id;
  state.tables = res.tables;
  state.tablePreviews = {};
  if (state.tables.length > 0) {
    state.activeTable = state.tables[0].table;
  }
  saveSessionToStorage();
  updateTableCountBadge();
  renderTables();
  $("#welcome").classList.add("hidden");
  $("#chat").classList.remove("hidden");
  $("#composer").classList.remove("hidden");
  
  if (state.activeTable) {
    const activeObj = state.tables.find((t) => t.table === state.activeTable) || state.tables[0];
    if (activeObj) renderProfile(activeObj);
  }

  switchTab(state.activeView || "chat");
  const msgContainer = $("#chat-messages-container") || $("#chat");
  if (!msgContainer.children.length) addAssistantGreeting();
  toast(`Loaded ${res.tables.length} table${res.tables.length > 1 ? "s" : ""}`);
}

/* ------------------------------------------------------------------ sidebar */
function renderTables() {
  updateActiveTableUi();
  const list = $("#table-list");
  list.innerHTML = "";
  if (!state.tables.length) {
    list.innerHTML = '<div class="empty-note">No datasets loaded.<br>Upload CSV/XLSX or load sample.</div>';
    return;
  }
  state.tables.forEach((t) => {
    const q = t.quality || {};
    const card = document.createElement("div");
    card.className = "table-card" + (t.table === state.activeTable ? " active" : "");
    const badges = [];
    if (q.duplicates > 0) badges.push(`<span class="badge warn">${q.duplicates} dupes</span>`);
    if (q.null_cells > 0) badges.push(`<span class="badge warn">${q.null_cells} nulls</span>`);
    badges.push(`<span class="badge good">${t.columns.length} cols</span>`);
    card.innerHTML = `
      <div class="t-name"><span class="material-symbols-outlined">database</span>${escapeHtml(t.table)}</div>
      <div class="t-meta">${fmt(t.rows)} rows</div>
      <div class="t-badges">${badges.join("")}</div>`;
    card.onclick = () => {
      state.activeTable = t.table;
      saveSessionToStorage();
      renderTables();
      renderProfile(t);
      switchTab(state.activeView);
    };
    list.appendChild(card);
  });
}

function renderProfile(t) {
  const body = $("#profile-body");
  const badge = $("#active-table-badge");
  if (badge) badge.textContent = t.table;
  const q = t.quality || {};
  const rows = t.columns.map((c) => {
    let info = c.type;
    if (c.avg != null) info += ` · avg ${fmt(Math.round(c.avg))}`;
    else if (c.top_values && c.top_values.length) info += ` · ${c.top_values.slice(0, 2).join(", ")}`;
    if (c.nulls) info += ` · ${c.nulls} nulls`;
    const numeric = c.avg != null;
    const extra = info.split(" · ").slice(1).join(" · ");
    return `<div class="p-col">
      <div><div class="p-name">${escapeHtml(c.name)}</div>
      ${extra ? `<div class="p-info">${escapeHtml(extra)}</div>` : ""}</div>
      <span class="type-badge${numeric ? "" : " text"}">${escapeHtml(c.type)}</span>
    </div>`;
  }).join("");
  body.innerHTML = `
    <div class="profile-head">
      <div class="p-table"><span class="material-symbols-outlined">database</span>${escapeHtml(t.table)}</div>
      <div class="p-meta">${fmt(t.rows)} rows · ${t.columns.length} columns · ${fmt(q.null_cells)} null cells · ${fmt(q.duplicates)} duplicate rows</div>
    </div>
    ${rows}`;
}

/* ------------------------------------------------------------------ table pill selector helper */
function renderTablePills(containerId, onSelectCallback) {
  const container = $(containerId);
  if (!container) return;
  container.innerHTML = "";
  if (!state.tables.length) return;

  state.tables.forEach((t) => {
    const btn = document.createElement("button");
    btn.className = "table-pill-btn" + (t.table === state.activeTable ? " active" : "");
    btn.textContent = t.table;
    btn.onclick = () => {
      state.activeTable = t.table;
      saveSessionToStorage();
      renderTables();
      const activeObj = state.tables.find((item) => item.table === t.table);
      if (activeObj) renderProfile(activeObj);
      if (onSelectCallback) onSelectCallback(t.table);
    };
    container.appendChild(btn);
  });
}

/* ------------------------------------------------------------------ 1. Upload Tab View */
function renderUploadView() {
  const container = $("#upload-tables-container");
  if (!state.tables.length) {
    container.innerHTML = '<div class="empty-note">No datasets uploaded yet. Upload a CSV or Excel file above.</div>';
    return;
  }
  container.innerHTML = "";
  state.tables.forEach((t) => {
    const q = t.quality || {};
    const card = document.createElement("div");
    card.className = "upload-table-card";
    card.innerHTML = `
      <div class="upload-table-info">
        <div class="upload-table-title">${escapeHtml(t.table)}</div>
        <div class="upload-table-meta">
          <span><b>${fmt(t.rows)}</b> rows</span> ·
          <span><b>${t.columns.length}</b> columns</span> ·
          <span class="badge ${q.null_cells > 0 ? 'warn' : 'good'}">${fmt(q.null_cells)} null cells</span>
          <span class="badge ${q.duplicates > 0 ? 'warn' : 'good'}">${fmt(q.duplicates)} duplicate rows</span>
        </div>
      </div>
      <div class="upload-table-actions">
        <button class="btn small primary btn-open-viewer" data-table="${escapeHtml(t.table)}">📑 Spreadsheet</button>
        <button class="btn small ghost btn-open-dash" data-table="${escapeHtml(t.table)}">Dashboard</button>
      </div>`;
    container.appendChild(card);
  });

  container.querySelectorAll(".btn-open-viewer").forEach((b) => {
    b.onclick = () => {
      state.activeTable = b.dataset.table;
      saveSessionToStorage();
      switchTab("viewer");
    };
  });

  container.querySelectorAll(".btn-open-dash").forEach((b) => {
    b.onclick = () => {
      state.activeTable = b.dataset.table;
      saveSessionToStorage();
      switchTab("dashboard");
    };
  });
}

/* ------------------------------------------------------------------ 2. Spreadsheet Viewer */
async function loadTableData(table) {
  const cached = getCachedPreview(table);
  if (cached) return cached;
  if (!state.sessionId) return null;

  const res = await api(`/api/preview/${state.sessionId}/${table}?limit=1000`);
  if (res && res.ok) {
    setCachedPreview(table, res);
    return res;
  }
  return null;
}

async function renderDataViewer() {
  const statsText = $("#viewer-stats-text");
  const gridContainer = $("#spreadsheet-grid");

  if (!state.tables.length) {
    $("#viewer-table-pills").innerHTML = "";
    statsText.textContent = "No datasets loaded yet.";
    gridContainer.innerHTML = '<div class="empty-note">Please upload a dataset or load the sample to inspect it in the spreadsheet viewer.</div>';
    return;
  }

  renderTablePills("#viewer-table-pills", () => renderDataViewer());
  const currentTable = state.activeTable || state.tables[0].table;
  statsText.textContent = `Loading data for "${currentTable}"…`;

  const preview = await loadTableData(currentTable);
  if (!preview || !preview.rows || !preview.rows.length) {
    statsText.textContent = `Table "${currentTable}" has no rows.`;
    gridContainer.innerHTML = '<div class="empty-note">No rows found in this table.</div>';
    return;
  }

  statsText.textContent = `Displaying ${fmt(preview.rows.length)} of ${fmt(preview.total_rows)} rows · ${preview.columns.length} columns`;

  const tableData = preview.rows.map((rowArr, idx) => {
    const obj = { id: idx + 1 };
    preview.columns.forEach((colName, colIdx) => {
      obj[colName] = rowArr[colIdx];
    });
    return obj;
  });

  const columns = [
    { title: "#", field: "id", width: 50, headerSort: false, resizable: false, cssClass: "preview-row-num" },
  ];

  preview.columns.forEach((colName) => {
    const schemaItem = (preview.schema || []).find((c) => c.name === colName);
    const typeLabel = schemaItem ? schemaItem.type : "";
    const isNumeric = typeLabel && /(int|float|double|numeric|decimal|hugeint)/i.test(typeLabel);

    columns.push({
      title: `${escapeHtml(colName)} <small style="opacity:0.6;font-size:10px;">${escapeHtml(typeLabel)}</small>`,
      field: colName,
      sorter: isNumeric ? "number" : "string",
      headerSort: true,
      minWidth: 110,
      formatter: (cell) => {
        const val = cell.getValue();
        if (val === null || val === undefined) return '<span style="color:#777588;font-style:italic;">null</span>';
        if (isNumeric && typeof val === "number") return val.toLocaleString("en-IN");
        return escapeHtml(val);
      },
    });
  });

  gridContainer.innerHTML = "";

  if (typeof Tabulator !== "undefined") {
    try {
      state.tabulatorInstance = new Tabulator(gridContainer, {
        data: tableData,
        columns: columns,
        layout: "fitDataFill",
        pagination: true,
        paginationSize: parseInt($("#viewer-page-size").value, 10) || 50,
        paginationSizeSelector: [25, 50, 100, 250, 500],
        movableColumns: true,
        height: "100%",
        placeholder: "No Matching Records Found",
      });
      return;
    } catch (e) {
      console.warn("Tabulator init error", e);
    }
  }

  // HTML Fallback
  const ths = `<th>#</th>` + preview.columns.map((c) => `<th>${escapeHtml(c)}</th>`).join("");
  const trs = preview.rows.slice(0, 100).map((r, idx) => {
    const tds = `<td class="preview-row-num">${idx + 1}</td>` +
      r.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("");
    return `<tr>${tds}</tr>`;
  }).join("");
  gridContainer.innerHTML = `<div style="overflow:auto;height:100%;"><table class="preview-table"><thead><tr>${ths}</tr></thead><tbody>${trs}</tbody></table></div>`;
}

/* ------------------------------------------------------------------ 3. Auto Dashboard */
async function renderDashboardView() {
  const kpisRow = $("#dashboard-kpis-row");
  const chartsGrid = $("#dashboard-charts-grid");
  if (!state.tables.length || !state.sessionId) {
    kpisRow.innerHTML = "";
    chartsGrid.innerHTML = '<div class="empty-note">Upload a dataset first to generate an executive dashboard.</div>';
    return;
  }

  renderTablePills("#dashboard-table-pills", () => renderDashboardView());
  const currentTable = state.activeTable || state.tables[0].table;
  kpisRow.innerHTML = '<div class="empty-note">Computing KPI Scorecard…</div>';
  chartsGrid.innerHTML = "";

  const res = await api(`/api/dashboard/${state.sessionId}/${currentTable}`);
  if (!res || !res.ok) {
    kpisRow.innerHTML = `<div class="empty-note">Could not generate dashboard: ${escapeHtml(res?.error || "Unknown error")}</div>`;
    return;
  }

  // Render KPIs
  kpisRow.innerHTML = res.kpis.map((k) => `
    <div class="kpi-card">
      <div class="kpi-label">${escapeHtml(k.label)}</div>
      <div class="kpi-value">${escapeHtml(k.value)}</div>
      <div class="kpi-sub">${escapeHtml(k.sub || "")}</div>
    </div>
  `).join("");

  // Render Charts
  chartsGrid.innerHTML = "";
  res.charts.forEach((ch, idx) => {
    const box = document.createElement("div");
    box.className = "dashboard-chart-box";
    const plotDiv = document.createElement("div");
    plotDiv.id = `dash-chart-${idx}`;
    plotDiv.style.height = "280px";
    box.appendChild(plotDiv);
    chartsGrid.appendChild(box);

    requestAnimationFrame(() => {
      try {
        Plotly.newPlot(plotDiv, ch.spec.data, ch.spec.layout, { responsive: true, displayModeBar: false });
      } catch (e) {
        box.innerHTML = `<div class="empty-note">Could not render chart: ${e.message}</div>`;
      }
    });
  });
}

/* ------------------------------------------------------------------ 4. Data Quality View */
async function renderQualityView() {
  const area = $("#quality-content-area");
  if (!state.tables.length || !state.sessionId) {
    area.innerHTML = '<div class="empty-note">Upload a dataset first to view data quality scorecards.</div>';
    return;
  }

  renderTablePills("#quality-table-pills", () => renderQualityView());
  const currentTable = state.activeTable || state.tables[0].table;
  area.innerHTML = '<div class="empty-note">Running data quality & health audit…</div>';

  const res = await api(`/api/quality/${state.sessionId}/${currentTable}`);
  if (!res || !res.ok) {
    area.innerHTML = `<div class="empty-note">Error auditing data quality.</div>`;
    return;
  }

  const isHealthy = res.health_score >= 80;
  area.innerHTML = `
    <div class="quality-summary-row">
      <div class="quality-score-card">
        <div class="score-circle ${isHealthy ? '' : 'warn'}">${res.health_score}</div>
        <div class="score-label">Health Score</div>
        <small style="color:var(--muted);">${res.completeness_pct}% Complete</small>
      </div>
      <div class="recs-box">
        <h4>Audit Summary &amp; Recommendations</h4>
        <ul>
          ${res.recommendations.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}
        </ul>
      </div>
    </div>

    <div>
      <h3 style="font-size:14px;margin:16px 0 8px;">Column Completeness &amp; Outlier Matrix</h3>
      <table class="quality-table">
        <thead>
          <tr>
            <th>Column</th>
            <th>Type</th>
            <th>Missing / Nulls</th>
            <th>Distinct Values</th>
            <th>Health Status</th>
            <th>Audit Notes</th>
          </tr>
        </thead>
        <tbody>
          ${res.columns.map((c) => `
            <tr>
              <td><b>${escapeHtml(c.name)}</b></td>
              <td><span class="type-badge">${escapeHtml(c.type)}</span></td>
              <td>${fmt(c.nulls)} (${c.null_pct}%)</td>
              <td>${fmt(c.distinct)}</td>
              <td><span class="badge ${c.status === 'Healthy' ? 'good' : 'warn'}">${c.status}</span></td>
              <td>${escapeHtml(c.issues.join(", "))}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

/* ------------------------------------------------------------------ 5. Forecasting View */
let forecastParams = { dateCol: null, metricCol: null, periods: 6 };

async function renderForecastView(dateOverride, metricOverride, periodsOverride) {
  const area = $("#forecast-content-area");
  if (!state.tables.length || !state.sessionId) {
    area.innerHTML = '<div class="empty-note">Upload a dataset first to run time-series forecasting.</div>';
    return;
  }

  renderTablePills("#forecast-table-pills", () => {
    forecastParams = { dateCol: null, metricCol: null, periods: 6 };
    renderForecastView();
  });
  
  const currentTable = state.activeTable || state.tables[0].table;
  
  if (dateOverride !== undefined) forecastParams.dateCol = dateOverride;
  if (metricOverride !== undefined) forecastParams.metricCol = metricOverride;
  if (periodsOverride !== undefined) forecastParams.periods = periodsOverride;

  let url = `/api/forecast/${state.sessionId}/${currentTable}?periods=${forecastParams.periods || 6}`;
  if (forecastParams.dateCol) url += `&date_col=${encodeURIComponent(forecastParams.dateCol)}`;
  if (forecastParams.metricCol) url += `&metric_col=${encodeURIComponent(forecastParams.metricCol)}`;

  area.innerHTML = '<div class="empty-note">Computing time-series forecast and projections…</div>';

  const res = await api(url);
  if (!res || !res.ok) {
    area.innerHTML = `<div class="empty-note">${escapeHtml(res?.error || "Could not generate forecast.")}</div>`;
    return;
  }

  const dateOpts = (res.available_dates || []).map(d => `<option value="${escapeHtml(d)}" ${d === res.date_column ? "selected" : ""}>${escapeHtml(d)}</option>`).join("");
  const metricOpts = (res.available_metrics || []).map(m => {
    const label = m === "__record_count__" ? "Record Volume (Count)" : m.replace(/_/g, " ").replace(/\b\w/g, l => l.toUpperCase());
    return `<option value="${escapeHtml(m)}" ${m === res.raw_metric ? "selected" : ""}>${escapeHtml(label)}</option>`;
  }).join("");

  area.innerHTML = `
    <div class="forecast-toolbar" style="display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin-bottom:14px;background:var(--panel);padding:10px 14px;border-radius:10px;border:1px solid var(--border-soft);">
      <div style="display:flex;align-items:center;gap:6px;">
        <label style="font-size:12px;font-weight:600;color:var(--muted);">Date Column:</label>
        <select id="forecast-date-select" class="viewer-select" style="padding:4px 8px;font-size:12px;">${dateOpts}</select>
      </div>
      <div style="display:flex;align-items:center;gap:6px;">
        <label style="font-size:12px;font-weight:600;color:var(--muted);">Forecast Metric:</label>
        <select id="forecast-metric-select" class="viewer-select" style="padding:4px 8px;font-size:12px;">${metricOpts}</select>
      </div>
      <div style="display:flex;align-items:center;gap:6px;">
        <label style="font-size:12px;font-weight:600;color:var(--muted);">Horizon:</label>
        <select id="forecast-periods-select" class="viewer-select" style="padding:4px 8px;font-size:12px;">
          <option value="3" ${res.forecast_periods === 3 ? "selected" : ""}>3 Periods</option>
          <option value="6" ${res.forecast_periods === 6 ? "selected" : ""}>6 Periods</option>
          <option value="12" ${res.forecast_periods === 12 ? "selected" : ""}>12 Periods</option>
        </select>
      </div>
    </div>

    <div class="forecast-meta-pills" style="margin-bottom:14px;">
      <span class="forecast-pill">📈 Trend: <b>${res.trend_direction}</b> (${res.growth_rate_pct}% rate)</span>
      <span class="forecast-pill">📅 Date Column: <b>${escapeHtml(res.date_column)}</b></span>
      <span class="forecast-pill">💰 Target Metric: <b>${escapeHtml(res.metric_column)}</b></span>
      <span class="forecast-pill">🔮 Projected: <b>+${res.forecast_periods} Periods</b></span>
    </div>
    <div class="forecast-chart-card">
      <div id="forecast-plot" style="height:360px;"></div>
    </div>
  `;

  const dateSel = $("#forecast-date-select");
  if (dateSel) dateSel.onchange = (e) => renderForecastView(e.target.value, undefined, undefined);

  const metricSel = $("#forecast-metric-select");
  if (metricSel) metricSel.onchange = (e) => renderForecastView(undefined, e.target.value, undefined);

  const periodsSel = $("#forecast-periods-select");
  if (periodsSel) periodsSel.onchange = (e) => renderForecastView(undefined, undefined, parseInt(e.target.value, 10));

  requestAnimationFrame(() => {
    try {
      Plotly.newPlot("forecast-plot", res.spec.data, res.spec.layout, { responsive: true, displayModeBar: false });
    } catch (e) { }
  });
}

/* ------------------------------------------------------------------ 6. Report View */
async function renderReportView() {
  const area = $("#report-content-area");
  if (!state.tables.length || !state.sessionId) {
    area.innerHTML = '<div class="empty-note">Upload a dataset first to generate an executive report.</div>';
    return;
  }

  renderTablePills("#report-table-pills", () => renderReportView());
  const currentTable = state.activeTable || state.tables[0].table;
  area.innerHTML = '<div class="empty-note">Drafting executive data report…</div>';

  const res = await api(`/api/report/${state.sessionId}/${currentTable}`);
  if (!res || !res.ok) {
    area.innerHTML = `<div class="empty-note">Could not generate report.</div>`;
    return;
  }

  window._currentReportMd = res.markdown;
  area.innerHTML = `
    <div class="report-paper">
      ${md(res.markdown)}
    </div>
  `;
}

/* ------------------------------------------------------------------ 7. Observability View */
async function renderObservabilityView() {
  const area = $("#observability-content-area");
  if (!state.sessionId) {
    area.innerHTML = '<div class="empty-note">No active session. Load a dataset and ask questions to view execution logs.</div>';
    return;
  }

  area.innerHTML = '<div class="empty-note">Loading query execution logs…</div>';
  const res = await api(`/api/logs/${state.sessionId}`);
  if (!res || !res.ok) {
    area.innerHTML = `<div class="empty-note">Error loading logs.</div>`;
    return;
  }

  const logs = res.logs || [];
  area.innerHTML = `
    <div class="forecast-meta-pills" style="margin-bottom:14px;">
      <span class="forecast-pill">Session: <b>${escapeHtml(res.session_id)}</b></span>
      <span class="forecast-pill">Active Provider: <b>${escapeHtml(String(res.provider).toUpperCase())}</b> (${escapeHtml(res.model)})</span>
      <span class="forecast-pill">Queries Executed: <b>${logs.length}</b></span>
    </div>

    ${logs.length ? `
      <table class="logs-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>DuckDB SQL Query</th>
            <th>Rows</th>
            <th>Latency</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          ${logs.map((l) => `
            <tr>
              <td><small>${escapeHtml(l.timestamp)}</small></td>
              <td><code class="log-sql">${escapeHtml(l.sql)}</code></td>
              <td>${fmt(l.rows)}</td>
              <td>${l.latency_ms} ms</td>
              <td><span class="badge good">OK</span></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    ` : '<div class="empty-note">No SQL queries recorded yet. Ask a question in chat to see live logs.</div>'}
  `;
}

/* ------------------------------------------------------------------ chat */
async function handleNewChat() {
  if (!state.sessionId) {
    return toast("Upload or load a dataset first.", "warn");
  }
  const chatContainer = $("#chat-messages-container") || $("#chat");
  chatContainer.innerHTML = "";
  
  // Call backend to reset session contents (in-memory multi-turn context)
  try {
    await api("/api/chat/new", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId }),
    });
  } catch (e) {}

  addAssistantGreeting();
  toast("✨ New conversation started. Context refreshed!");
}

function handleClearChat() {
  const chatContainer = $("#chat-messages-container") || $("#chat");
  chatContainer.innerHTML = "";
  addAssistantGreeting();
  toast("Chat messages cleared.");
}

function addUser(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = `<div class="bubble"></div>`;
  el.querySelector(".bubble").textContent = text;
  const chatContainer = $("#chat-messages-container") || $("#chat");
  chatContainer.appendChild(el);
  scrollBottom();
}

function addAssistantGreeting() {
  const names = state.tables.map((t) => `**${t.table}**`).join(", ");
  const total = state.tables.reduce((a, t) => a + t.rows, 0);
  const msg = addAssistant();
  const provName = state.mode === "gemini" ? "Google Gemini"
    : state.mode === "openrouter" ? "OpenRouter"
      : state.mode === "groq" ? "Groq" : "Offline Demo";
  msg.addToken(`Hello! I've loaded ${names} — **${fmt(total)} rows** total.\n\n` +
    `Connected via **${provName}** (${state.model || "active model"}). ` +
    `Ask me questions and I'll analyze with DuckDB SQL, chart patterns, forecast trends, and check anomalies.`);
  msg.newBlock(chipBlock());
}

function chipBlock() {
  const wrap = document.createElement("div");
  wrap.className = "chips";
  EXAMPLES.forEach((q) => {
    const b = document.createElement("button");
    b.className = "chip";
    b.textContent = q;
    b.onclick = () => { if (!state.busy) send(q); };
    wrap.appendChild(b);
  });
  return wrap;
}

function addAssistant() {
  const chatEl = $("#chat-messages-container") || $("#chat");
  const wrap = document.createElement("div");
  wrap.className = "msg assistant";
  wrap.innerHTML = `<div class="avatar"><span class="material-symbols-outlined">neurology</span></div><div class="body"></div>`;
  chatEl.appendChild(wrap);
  const body = wrap.querySelector(".body");

  const status = document.createElement("div");
  status.className = "status hidden";
  body.appendChild(status);

  let textEl = null;
  let raw = "";
  const msg = {
    root: wrap,
    setStatus(t) {
      if (!t) { status.classList.add("hidden"); status.textContent = ""; }
      else { status.classList.remove("hidden"); status.textContent = t; }
      scrollBottom();
    },
    addToken(t) {
      if (!textEl) { textEl = document.createElement("div"); textEl.className = "text"; body.appendChild(textEl); }
      raw += t;
      textEl.innerHTML = md(raw);
      scrollBottom();
    },
    newBlock(node) {
      msg.setStatus("");
      body.appendChild(node);
      textEl = null; raw = "";
      scrollBottom();
    },
  };
  return msg;
}

/* ------------------------------------------------- event blocks */
function sqlBlock(ev) {
  const d = document.createElement("details");
  d.className = "sql-block";
  d.open = false;
  const meta = ev.rows != null ? ` · ${fmt(ev.rows)} rows` : "";
  d.innerHTML = `
    <summary><span class="sql-tag">SQL</span><span>DuckDB${meta}</span>
      <button class="copy">Copy</button></summary>
    <pre><code>${escapeHtml(ev.sql || "")}</code></pre>
    ${ev.export ? `<a class="export-link" href="${ev.export}" download>⬇ Download full result (${fmt(ev.rows)} rows)</a>` : ""}`;
  d.querySelector(".copy").onclick = (e) => {
    e.preventDefault();
    navigator.clipboard.writeText(ev.sql || "");
    toast("SQL copied to clipboard");
  };
  return d;
}

function chartBlock(spec) {
  const card = document.createElement("div");
  card.className = "chart-card";
  const plot = document.createElement("div");
  plot.className = "chart-plot";
  card.appendChild(plot);
  requestAnimationFrame(() => {
    try { Plotly.newPlot(plot, spec.data, spec.layout, { responsive: true, displayModeBar: false }); }
    catch (e) { card.innerHTML = `<div class="empty-note">Could not render chart: ${escapeHtml(e.message)}</div>`; }
  });
  return card;
}

function anomalyBlock(ev) {
  const card = document.createElement("div");
  card.className = "anomaly-card";
  const colsHtml = (ev.columns || []).map((c) => {
    const range = c.lower_bound != null
      ? `normal range ${fmt(Math.round(c.lower_bound))} – ${fmt(Math.round(c.upper_bound))} · mean ${fmt(Math.round(c.mean))} · σ ${fmt(Math.round(c.std))}`
      : `z-score > 3 · mean ${fmt(Math.round(c.mean))} · σ ${fmt(Math.round(c.std))}`;
    let table = "";
    if (c.count > 0 && c.sample && c.sample.length) {
      const keys = Object.keys(c.sample[0]).slice(0, 6);
      table = `<table class="a-sample">
        <tr>${keys.map((k) => `<th>${escapeHtml(k)}</th>`).join("")}</tr>
        ${c.sample.map((row) => `<tr>${keys.map((k) => {
          const hot = k === c.column;
          return `<td${hot ? ' style="color:#ff6b4a;font-weight:700"' : ""}>${escapeHtml(row[k])}</td>`;
        }).join("")}</tr>`).join("")}
      </table>`;
    }
    return `<div class="a-col">
      <div class="a-line"><span class="a-name">${escapeHtml(c.column)}</span>
        <span class="badge ${c.count ? "warn" : "good"}">${c.count} flagged</span>
        <span class="a-range">${escapeHtml(range)}</span></div>
      ${table}
    </div>`;
  }).join("");
  card.innerHTML = `<div class="a-head">Anomaly detection · ${escapeHtml(String(ev.method || "iqr").toUpperCase())} · ${escapeHtml(ev.table || "")}</div>
    ${colsHtml || '<div class="empty-note">No numeric columns to scan.</div>'}`;
  return card;
}

function errBlock(detail) {
  const d = document.createElement("div");
  d.className = "err-card";
  d.innerHTML = `<b>Something went wrong.</b><br>${escapeHtml(detail)}`;
  return d;
}

function handleEvent(ev, msg) {
  switch (ev.type) {
    case "token": msg.addToken(ev.text || ""); break;
    case "status": msg.setStatus(ev.detail || ""); break;
    case "sql": msg.newBlock(sqlBlock(ev)); break;
    case "sql_error": msg.newBlock(errBlock(`SQL failed: ${ev.error} — the agent will try to fix the query.`)); break;
    case "chart": msg.newBlock(chartBlock(ev.spec)); break;
    case "anomaly": msg.newBlock(anomalyBlock(ev)); break;
    case "schema":
      state.tables = ev.tables.map((t) => ({ ...t, quality: (state.tables.find((s) => s.table === t.table) || {}).quality }));
      saveSessionToStorage();
      renderTables();
      break;
    case "error": msg.newBlock(errBlock(ev.detail || "Unknown error")); break;
    case "done": break;
    default: break;
  }
}

/* ------------------------------------------------- send (SSE) */
async function send(text) {
  if (!state.sessionId) return toast("Load a dataset first", "warn");
  addUser(text);
  state.busy = true;
  const input = $("#input");
  input.disabled = true;
  const msg = addAssistant();
  msg.setStatus("Thinking…");

  try {
    const res = await fetch(getApiUrl("/api/chat"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: state.sessionId,
        message: text,
        provider: state.mode,
        model: state.model,
      }),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({ detail: res.statusText }));
      msg.newBlock(errBlock(e.detail || `Request failed (${res.status})`));
    } else {
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i);
          buf = buf.slice(i + 2);
          if (chunk.startsWith("data: ")) {
            let ev = null;
            try { ev = JSON.parse(chunk.slice(6)); } catch { }
            if (ev) handleEvent(ev, msg);
          }
        }
      }
    }
  } catch (e) {
    msg.newBlock(errBlock(String(e)));
  }
  msg.setStatus("");
  state.busy = false;
  input.disabled = false;
  input.focus();
  scrollBottom();
}

init();
