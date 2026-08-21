// ═══════════════════════════════════════════════════════════════════════════
// Caduceo Console — remote.js
// Vista "PC connessi" + controllo remoto (streaming video, mouse, tastiera,
// screenshot, info di sistema). Rifattorizzato dalla web UI originale.
// ═══════════════════════════════════════════════════════════════════════════
(function () {
  "use strict";
  const App = window.App;
  let frameMeta = { width: 0, height: 0, streaming: false };
  let refreshTimer = null;
  const pressedMods = new Set();

  // ── Lista PC connessi ────────────────────────────────────────────────────
  async function renderAgentList(container, { actions, setView, toast }) {
    const refresh = document.createElement("button");
    refresh.textContent = "🔄 Aggiorna";
    refresh.onclick = () => renderAgentList(container, { actions, setView, toast });
    actions.appendChild(refresh);

    const wrap = document.createElement("div");
    wrap.innerHTML = `<div id="agent-grid" class="grid grid-agents"></div>`;
    container.appendChild(wrap);

    async function load() {
      try {
        const data = await App.api("/api/agents");
        const agents = data.agents || [];
        const grid = wrap.querySelector("#agent-grid");
        grid.innerHTML = agents.map((a) => `
          <div class="agent-card" data-id="${App.escapeHtml(a.agent_id)}">
            <span class="badge ${a.online ? 'online' : 'offline'}">${a.online ? '● Online' : '○ Offline'}</span>
            <div class="name">${App.escapeHtml(a.hostname || a.agent_id)}</div>
            <div class="meta">
              ${App.escapeHtml(a.os || '')} ${App.escapeHtml(a.os_version || '')}<br>
              ID: ${App.escapeHtml(a.agent_id)}<br>
              CPU ${a.stats?.cpu_percent ?? '?'}% · RAM ${a.stats?.memory_percent ?? '?'}% · DISCO ${a.stats?.disk_percent ?? '?'}%
            </div>
          </div>`).join("");
        grid.querySelectorAll(".agent-card").forEach((card) =>
          card.addEventListener("click", () => openRemote(card.dataset.id, toast)));
        if (!agents.length) {
          grid.innerHTML = `<div class="card"><p style="color:var(--muted)">Nessun agente registrato.</p></div>`;
        }
      } catch (e) {
        if (e.message !== "Unauthorized") toast("Errore: " + e.message, "error");
      }
    }
    load();
    return { timer: setInterval(load, 5000) };
  }

  // ── Controllo remoto ─────────────────────────────────────────────────────
  async function openRemote(agentId, toast) {
    App.currentAgent = agentId;
    frameMeta = { width: 0, height: 0, streaming: false };
    const view = document.getElementById("view");
    view.innerHTML = `
      <div class="view-header">
        <button class="ghost" id="back-btn">← Elenco PC</button>
        <h2 id="current-agent"></h2>
        <div class="view-actions">
          <button id="stream-btn" class="primary">▶ Avvia streaming</button>
          <button id="stop-stream-btn" class="danger hidden">■ Ferma</button>
          <button id="screenshot-btn">📷 Screenshot</button>
          <button id="refresh-info-btn">🔄 Info</button>
          <button id="term-btn">💻 Terminale</button>
          <button id="files-btn">📁 File</button>
        </div>
      </div>
      <div id="stream-container" style="position:relative">
        <img id="stream-img" alt="Streaming remoto" style="width:100%;max-height:65vh;object-fit:contain;background:#000;border-radius:12px;border:1px solid var(--border);cursor:crosshair;display:none">
        <div id="stream-placeholder" style="padding:60px;text-align:center;color:var(--muted);border:2px dashed var(--border);border-radius:12px">
          Avvia lo streaming per vedere il desktop del PC remoto<br>
          <span style="font-size:0.8rem">(clicca un PC dalla lista o usa Terminale/File per operare)</span>
        </div>
      </div>
      <div id="info-panel" style="margin-top:16px;background:var(--card);border-radius:12px;border:1px solid var(--border);padding:16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px"></div>
      <div id="input-tools" class="hidden" style="margin-top:20px;background:var(--card);border-radius:12px;border:1px solid var(--border);padding:20px">
        <h3 style="margin:12px 0 8px;font-size:0.9rem;color:var(--muted)">⌨️ Tastiera</h3>
        <input type="text" id="text-input" placeholder="Digita testo da inviare" style="padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);width:300px;margin-right:8px">
        <button id="text-send">Invia testo</button>
        <h3 style="margin:12px 0 8px;font-size:0.9rem;color:var(--muted)">🖱️ Azioni mouse</h3>
        <button id="btn-left">Sinistro</button>
        <button id="btn-double">Doppio clic</button>
        <button id="btn-right">Destro</button>
        <button id="btn-middle">Centro</button>
        <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
          <button data-keys="ctrl+alt+del">Ctrl+Alt+Canc</button>
          <button data-keys="win+d">Win+D</button>
          <button data-keys="alt+tab">Alt+Tab</button>
        </div>
      </div>`;

    const $ = (id) => document.getElementById(id);
    $("current-agent").textContent = "PC: " + agentId;

    const back = $("back-btn");
    back.onclick = () => { stopStream(); App.setView("agents"); };

    $("stream-btn").addEventListener("click", startStream);
    $("stop-stream-btn").addEventListener("click", stopStream);
    $("screenshot-btn").addEventListener("click", doScreenshot);
    $("refresh-info-btn").addEventListener("click", () => refreshInfo(agentId, $));
    $("term-btn").addEventListener("click", () => {
      if (window.CaduceoTerminal) CaduceoTerminal.open(agentId, document.getElementById("view"));
    });
    $("files-btn").addEventListener("click", () => {
      if (window.CaduceoFiles) CaduceoFiles.open(agentId, document.getElementById("view"));
    });

    refreshInfo(agentId, $);
    clearInterval(refreshTimer);
    refreshTimer = setInterval(() => refreshInfo(agentId, $), 10000);
    bindInputEvents($);

    // ripristina input tools se agent è full/streaming (ricontrolla su start)
  }

  // ── Streaming ────────────────────────────────────────────────────────────
  async function startStream() {
    const id = App.currentAgent;
    const $ = (i) => document.getElementById(i);
    try {
      await App.api(`/api/agents/${encodeURIComponent(id)}/stream/start`, { method: "POST", body: {} });
      const url = `/api/agents/${encodeURIComponent(id)}/stream.mjpeg?token=${encodeURIComponent(App.jwt)}`;
      $("stream-img").src = url;
      $("stream-img").style.display = "block";
      $("stream-placeholder").style.display = "none";
      $("stream-btn").classList.add("hidden");
      $("stop-stream-btn").classList.remove("hidden");
      $("input-tools").classList.remove("hidden");
      frameMeta.streaming = true;
      App.toast("Streaming avviato ✓", "success");
    } catch (e) { App.toast("Errore: " + e.message, "error"); }
  }

  async function stopStream() {
    const id = App.currentAgent;
    const $ = (i) => document.getElementById(i);
    if (id) { try { await App.api(`/api/agents/${encodeURIComponent(id)}/stream/stop`, { method: "POST", body: {} }); } catch (e) {} }
    const img = $("stream-img");
    if (img) { img.removeAttribute("src"); img.style.display = "none"; }
    const ph = $("stream-placeholder");
    if (ph) ph.style.display = "block";
    const sb = $("stream-btn"), ss = $("stop-stream-btn"), it = $("input-tools");
    if (sb) sb.classList.remove("hidden");
    if (ss) ss.classList.add("hidden");
    if (it) it.classList.add("hidden");
    frameMeta.streaming = false;
    frameMeta.width = 0; frameMeta.height = 0;
    resetMods();
  }

  // ── Screenshot & info ────────────────────────────────────────────────────
  async function doScreenshot() {
    const id = App.currentAgent;
    const $ = (i) => document.getElementById(i);
    try {
      const data = await App.api(`/api/agents/${encodeURIComponent(id)}/screenshot`, { method: "POST" });
      if (data.content_b64) {
        const img = `data:image/png;base64,${data.content_b64}`;
        window.open(img, "_blank");
        App.toast("Screenshot acquisito ✓", "success");
      } else App.toast("Nessuno screenshot (check error)", "error");
    } catch (e) { App.toast("Errore: " + e.message, "error"); }
  }

  async function refreshInfo(id, $) {
    try {
      const d = await App.api(`/api/agents/${encodeURIComponent(id)}/info`);
      const panel = $("info-panel");
      if (!panel) return;
      panel.innerHTML = `
        <div class="stat"><b>${App.escapeHtml(d.hostname || '?')}</b>Host</div>
        <div class="stat"><b>${App.escapeHtml(d.os || '?')} ${App.escapeHtml(d.os_version || '')}</b>Sistema</div>
        <div class="stat"><b>${d.cpu_count ?? '?'}</b>Core CPU</div>
        <div class="stat"><b>${App.fmtBytes(d.memory_total)}</b>RAM · ${d.memory_percent ?? '?'}%</div>
        <div class="stat"><b>${App.fmtBytes(d.disk_total)}</b>Disco · ${d.disk_percent ?? '?'}%</div>
        <div class="stat"><b>${App.fmtUp(d.uptime_seconds)}</b>Uptime</div>`;
    } catch (e) {}
  }

  // ── Input events ─────────────────────────────────────────────────────────
  function sendInput(payload) {
    const id = App.currentAgent;
    if (!id) return;
    App.api(`/api/agents/${encodeURIComponent(id)}/input`, { method: "POST", body: payload })
      .catch((e) => { if (e.message !== "Unauthorized") App.toast("Input errato: " + e.message, "error"); });
  }

  function eventToRemoteXY(ev) {
    const img = document.getElementById("stream-img");
    if (!img) return null;
    const rect = img.getBoundingClientRect();
    const dispW = rect.width, dispH = rect.height;
    const realW = frameMeta.width, realH = frameMeta.height;
    if (!realW || !realH) return null;
    const scale = Math.min(dispW / realW, dispH / realH);
    const shownW = realW * scale, shownH = realH * scale;
    const offX = (dispW - shownW) / 2, offY = (dispH - shownH) / 2;
    return { x: Math.round((ev.clientX - rect.left - offX) / scale), y: Math.round((ev.clientY - rect.top - offY) / scale) };
  }

  function buttonName(b) { return b === 2 ? "right" : b === 1 ? "middle" : "left"; }

  function bindInputEvents($) {
    const img = $("stream-img");
    img.addEventListener("load", () => {
      if (frameMeta.streaming && !frameMeta.width) {
        const probe = new Image();
        probe.onload = () => { frameMeta.width = probe.naturalWidth; frameMeta.height = probe.naturalHeight; };
        probe.src = img.src;
      }
    });
    img.addEventListener("mousemove", (ev) => {
      if (!frameMeta.streaming) return;
      const pt = eventToRemoteXY(ev);
      if (pt) sendInput({ event_type: "move", ...pt });
    });
    img.addEventListener("mousedown", (ev) => {
      if (!frameMeta.streaming) return;
      const pt = eventToRemoteXY(ev);
      if (pt) sendInput({ event_type: "down", ...pt, button: buttonName(ev.button) });
    });
    img.addEventListener("mouseup", (ev) => {
      if (!frameMeta.streaming) return;
      const pt = eventToRemoteXY(ev);
      if (pt) sendInput({ event_type: "up", ...pt, button: buttonName(ev.button) });
    });
    img.addEventListener("dblclick", (ev) => {
      if (!frameMeta.streaming) return;
      const pt = eventToRemoteXY(ev);
      if (pt) sendInput({ event_type: "doubleclick", ...pt, button: buttonName(ev.button) });
    });
    img.addEventListener("wheel", (ev) => {
      if (!frameMeta.streaming) return;
      ev.preventDefault();
      sendInput({ event_type: "scroll", scroll_delta: ev.deltaY < 0 ? 3 : -3 });
    });

    // tastiera manuale (fallback)
    $("text-send").addEventListener("click", () => {
      const t = $("text-input").value;
      if (t) { sendInput({ event_type: "text", text: t }); $("text-input").value = ""; }
    });
    $("text-input").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); $("text-send").click(); }
    });
    $("btn-left").addEventListener("click", () => sendInput({ event_type: "click", button: "left" }));
    $("btn-double").addEventListener("click", () => sendInput({ event_type: "doubleclick", button: "left" }));
    $("btn-right").addEventListener("click", () => sendInput({ event_type: "click", button: "right" }));
    $("btn-middle").addEventListener("click", () => sendInput({ event_type: "click", button: "middle" }));
    document.querySelectorAll(".hotkey-row button").forEach((b) =>
      b.addEventListener("click", () => sendInput({ event_type: "hotkey", keys: b.dataset.keys })));

    // tastiera diretta globale
    document.addEventListener("keydown", globalKeydown);
    document.addEventListener("keyup", globalKeyup);
  }

  const KEY_MAP = {
    "Enter": "enter", "Tab": "tab", "Backspace": "backspace", "Delete": "delete",
    "Escape": "esc", "CapsLock": "capslock", " ": "space",
    "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right",
    "Home": "home", "End": "end", "PageUp": "pageup", "PageDown": "pagedown",
    "Insert": "insert", "Shift": "shift", "Control": "ctrl", "Alt": "alt",
    "Meta": "win", "ContextMenu": "appscmd",
    "F1": "f1","F2": "f2","F3": "f3","F4": "f4","F5": "f5","F6": "f6",
    "F7": "f7","F8": "f8","F9": "f9","F10": "f10","F11": "f11","F12": "f12",
    "+": "plus", "-": "minus", ".": "period", ",": "comma", "/": "slash",
    "\\": "backslash", ";": "semicolon", "'": "apostrophe", "[": "leftbracket", "]": "rightbracket",
  };
  const MODIFIER_MAP = { "Control": "ctrl", "Shift": "shift", "Alt": "alt", "Meta": "win" };

  function globalKeydown(ev) {
    if (!frameMeta.streaming || !App.currentAgent) return;
    if (ev.target && (ev.target.id === "text-input" || ev.target.id === "token-input")) return;
    if (ev.ctrlKey && ["r","w","t","c","v","a","z","s","l"].includes(ev.key)) return;
    const mod = MODIFIER_MAP[ev.key];
    if (mod) { pressedMods.add(mod); ev.preventDefault(); sendInput({ event_type: "key_down", key: mod }); return; }
    ev.preventDefault();
    const pyKey = KEY_MAP[ev.key] || ev.key.toLowerCase();
    if (pressedMods.size) sendInput({ event_type: "hotkey", keys: [...pressedMods, pyKey].join("+") });
    else if (ev.key.length === 1) sendInput({ event_type: "text", text: ev.key });
    else sendInput({ event_type: "press", key: pyKey });
  }
  function globalKeyup(ev) {
    const mod = MODIFIER_MAP[ev.key];
    if (mod && frameMeta.streaming) {
      pressedMods.delete(mod);
      ev.preventDefault();
      sendInput({ event_type: "key_up", key: mod });
    }
  }
  function resetMods() {
    if (pressedMods.size && App.currentAgent) {
      for (const m of pressedMods) sendInput({ event_type: "key_up", key: m });
      pressedMods.clear();
    }
  }
  window.addEventListener("blur", resetMods);

  // ═══ Registrazione vista ═══
  if (window.App) {
    App.registerView("agents", {
      render: renderAgentList,
    });
  }
})();
