// ═══════════════════════════════════════════════════════════════════════════
// Caduceo Console — app.js
// Helper di base + router della console di gestione.
// Espone `window.App` con: api(), currentAgent, setView(), helpers (fmt*).
// I moduli (dashboard.js, terminal.js, audit.js, files.js, remote.js) si
// registrano via window.App.registerView(nome, {render, ...}).
// ═══════════════════════════════════════════════════════════════════════════
(function () {
  "use strict";

  let JWT = localStorage.getItem("caduceo_jwt") || "";
  let currentAgent = null;

  const $ = (id) => document.getElementById(id);

  function toast(msg, type = "info") {
    const t = $("toast");
    if (!t) return;
    t.textContent = msg;
    t.className = type;
    t.classList.remove("hidden");
    clearTimeout(t._t);
    t._t = setTimeout(() => t.classList.add("hidden"), 3500);
  }

  async function api(path, opts = {}) {
    const headers = { ...(opts.headers || {}) };
    if (JWT) headers["Authorization"] = `Bearer ${JWT}`;
    if (opts.body && typeof opts.body === "object" && !(opts.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      opts = { ...opts, body: JSON.stringify(opts.body) };
    }
    const res = await fetch(path, { ...opts, headers });
    if (res.status === 401) {
      toast("Token scaduto o non valido. Accedi di nuovo.", "error");
      logout();
      throw new Error("Unauthorized");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || res.statusText; } catch (e) {}
      throw new Error(detail);
    }
    return res.status === 204 ? null : res.json();
  }

  // ── Autenticazione ──────────────────────────────────────────────────────
  // Decodifica il payload del JWT (parte centrale) per estrarre l'utente.
  function jwtPayload() {
    try {
      const p = JWT.split(".")[1];
      if (!p) return {};
      let b64 = p.replace(/-/g, "+").replace(/_/g, "/");
      while (b64.length % 4) b64 += "=";
      return JSON.parse(atob(b64));
    } catch (e) { return {}; }
  }

  // Mostra/nasconde la barra utente in alto con nome ed email.
  function updateUserBar() {
    const bar = $("user-bar");
    const nameEl = $("user-name");
    if (!bar) return;
    if (!JWT) { bar.classList.add("hidden"); return; }
    const claims = jwtPayload();
    const display = claims.username || claims.email || claims.sub || "utente";
    if (nameEl) nameEl.textContent = display;
    bar.classList.remove("hidden");
  }

  function logout() {
    JWT = "";
    localStorage.removeItem("caduceo_jwt");
    currentAgent = null;
    updateUserBar();
    showLogin();
  }

  function showLogin() {
    $("login-screen").classList.remove("hidden");
    $("view").classList.add("hidden");
    updateStatus(false);
    // ferma auto-refresh moduli
    window.App.clearTimers && window.App.clearTimers();
  }

  function login(token) {
    JWT = token.trim();
    localStorage.setItem("caduceo_jwt", JWT);
    $("login-screen").classList.add("hidden");
    $("view").classList.remove("hidden");
    updateStatus(true);
    updateUserBar();
    setView("dashboard");
    toast("Accesso effettuato ✓", "success");
  }

  $("oidc-btn") && $("oidc-btn").addEventListener("click", () => {
    window.location.href = "/api/auth/oidc/start";
  });

  $("logout-btn") && $("logout-btn").addEventListener("click", () => {
    logout();
    toast("Sessione terminata", "info");
  });

  $("token-btn") && $("token-btn").addEventListener("click", () => {
    const v = $("token-input").value.trim();
    if (!v) { toast("Inserisci un token", "error"); return; }
    login(v);
  });
  $("token-input") && $("token-input").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") $("token-btn").click();
  });

  // ═══ Router (SPA) ═══
  const views = {};
  let timers = [];

  function registerView(name, def) {
    views[name] = def;
  }

  const viewMeta = {
    dashboard: { title: "Dashboard", icon: "📊" },
    agents:    { title: "PC connessi", icon: "🖥️" },
    ai:        { title: "Assistente IA", icon: "🤖" },
    audit:     { title: "Registro attività", icon: "🧾" },
  };

  // ═══ Registrazione viste ═══
  // I moduli (dashboard.js, audit.js, remote.js, ...) sono caricati DOPO app.js,
  // quindi alcune viste vengono registrate in modo LAZY: il router le risolve
  // al momento di setView() guardando i moduli globali esposti (window.Caduceo*).
  function resolveLazyView(name) {
    switch (name) {
      case "dashboard":
        if (window.CaduceoDashboard) return { render: (body) => window.CaduceoDashboard.init(body) };
        break;
      case "audit":
        if (window.CaduceoAudit) return { render: (body) => window.CaduceoAudit.init(body) };
        break;
      case "ai":
        if (window.CaduceoAI) return { render: (body, ctx) => window.CaduceoAI.render(body, ctx) };
        break;
      case "agents":
        if (window.CaduceoRemote) return { render: (body, ctx) => window.CaduceoRemote.render(body, ctx) };
        break;
    }
    return null;
  }

  async function setView(name) {
    clearTimers();
    // aggiorna sidebar
    document.querySelectorAll(".nav-item").forEach((b) =>
      b.classList.toggle("active", b.dataset.view === name)
    );
    const meta = viewMeta[name] || {};
    const container = $("view");
    container.innerHTML = `<div class="view-header">
        <h2>${meta.icon || ""} ${meta.title || name}</h2>
        <div class="view-actions"></div>
      </div><div class="view-body"></div>`;

    let def = views[name];
    if (!def) def = resolveLazyView(name);
    if (!def) { container.innerHTML += "<p>Vista non disponibile.</p>"; return; }
    const body = container.querySelector(".view-body");
    const actions = container.querySelector(".view-actions");

    try {
      if (typeof def.render === "function") {
        const r = def.render(body, { actions, setView, toast });
        if (r && r.timer) timers.push(r.timer);
      }
      if (typeof def.onEnter === "function") {
        const r = def.onEnter({ body, actions, setView, toast });
        if (r && r.timer) timers.push(r.timer);
      }
    } catch (e) {
      body.innerHTML = `<div class="card"><p style="color:var(--danger)">Errore vista: ${e.message}</p></div>`;
      console.error(e);
    }
  }

  function clearTimers() {
    timers.forEach((t) => clearInterval(t));
    timers = [];
  }

  document.querySelectorAll(".nav-item").forEach((b) =>
    b.addEventListener("click", () => setView(b.dataset.view))
  );

  // ── Stato connessione ───────────────────────────────────────────────────
  function updateStatus(online) {
    const dot = $("conn-status");
    const lbl = $("conn-label");
    if (dot) dot.className = "status-dot " + (online ? "online" : "offline");
    if (lbl) lbl.textContent = online ? "Connesso" : "Non connesso";
  }

  async function checkHealth() {
    try {
      const h = await fetch("/api/health").then((r) => r.json());
      updateStatus(h.status === "ok");
    } catch (e) { updateStatus(false); }
  }

  // ── Helpers formato ─────────────────────────────────────────────────────
  function fmtBytes(n) {
    if (!n) return "?";
    const u = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < 4) { n /= 1024; i++; }
    return n.toFixed(1) + " " + u[i];
  }
  function fmtUp(s) {
    if (!s) return "?";
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h + "h " + m + "m";
  }
  function fmtTs(ts) {
    if (!ts) return "";
    return new Date(ts * 1000).toLocaleString("it-IT", {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    });
  }
  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  const ACTION_LABELS = {
    command: "Comando", screenshot: "Screenshot", file_upload: "Upload file",
    file_download: "Download file", stream_start: "Streaming avvio",
    stream_stop: "Streaming stop", input: "Input remoto", agent_error: "Errore agente",
    agent_register: "Agente registrato", agent_disconnect: "Agente disconnesso",
    agent_connect: "Agente connesso",
  };
  const ACTION_CLASS = {
    command: "command", screenshot: "screenshot", file_upload: "file",
    file_download: "file", stream_start: "stream", stream_stop: "stream",
    input: "input", agent_error: "error",
    agent_register: "input", agent_disconnect: "input", agent_connect: "input",
  };

  // ═══ Esposizione pubblica ═══
  window.App = {
    api, toast, setView, registerView, clearTimers, checkHealth, logout,
    get currentAgent() { return currentAgent; },
    set currentAgent(v) { currentAgent = v; },
    get jwt() { return JWT; },
    fmtBytes, fmtUp, fmtTs, escapeHtml, ACTION_LABELS, ACTION_CLASS,
  };

  // ═══ Avvio ═══
  // Ritarda l'avvio fino a window.load: in questo modo TUTTI i moduli
  // (dashboard.js, audit.js, remote.js, ...) sono già stati caricati e
  // il router lazy trova i loro global (window.Caduceo*).
  window.addEventListener("load", () => {
    checkHealth();
    const params = new URLSearchParams(window.location.search);
    const callbackToken = params.get("token");
    const callbackError = params.get("error");

    if (callbackToken || callbackError) {
      history.replaceState({}, document.title, window.location.pathname + window.location.hash);
    }

    if (callbackToken) {
      login(callbackToken);
    } else if (JWT) {
      $("token-input") && ($("token-input").value = JWT);
      $("login-screen").classList.add("hidden");
      $("view").classList.remove("hidden");
      updateStatus(true);
      updateUserBar();
      setView("dashboard");
    } else {
      showLogin();
    }
    if (callbackError) toast(`Errore di autenticazione: ${callbackError}`, "error");
  });
  setInterval(checkHealth, 15000);
})();
