// ═══════════════════════════════════════════════════════════════════════════
// Caduceo Console — files.js
// Gestione file remota: navigazione delle directory dell'agente via comando
// remoto (ls/dir), download e upload di file.
// ═══════════════════════════════════════════════════════════════════════════
(function () {
  "use strict";
  const App = window.App;

  let agentId = null;
  let cwd = null;        // directory corrente (null = home/root)

  // Determina il comando di listazione in base all'OS dell'agente (best effort).
  // Se fallisce, l'operatore può digitare la directory a mano.
  function listCommand() {
    return { command: "cmd /c dir", timeout: 15 };
  }

  // Apre la vista file manager per un agente.
  async function open(agentId_, container) {
    agentId = agentId_;
    cwd = null;
    render(container);
    await load(container);
  }

  async function render(container) {
    const view = document.getElementById("view");
    view.innerHTML = `
      <div class="view-header">
        <button class="ghost" id="files-back">← Elenco PC</button>
        <h2>📁 Gestione file — <span id="files-agent"></span></h2>
        <div class="view-actions">
          <button id="files-up">⬆ Cartella superiore</button>
          <button id="files-home">🏠 Home</button>
        </div>
      </div>
      <div class="filter-bar">
        <label>Percorso:</label>
        <input type="text" id="files-path" style="flex:1;min-width:300px" placeholder="Directory da listare (es. C:\\Users\\user o /home/user)">
        <button id="files-go" class="primary">Vai</button>
        <button id="files-upload">⬆ Upload file</button>
        <input type="file" id="files-upload-input" class="hidden">
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Nome</th><th>Tipo</th><th>Azioni</th></tr></thead>
          <tbody id="files-tbody"><tr><td colspan="3" style="color:var(--muted)">Caricamento…</td></tr></tbody>
        </table>
      </div>`;

    document.getElementById("files-agent").textContent = agentId;
    document.getElementById("files-path").value = cwd || "";

    document.getElementById("files-back").onclick = () => App.setView("agents");
    document.getElementById("files-go").onclick = () => { cwd = document.getElementById("files-path").value.trim() || null; load(container); };
    document.getElementById("files-path").addEventListener("keydown", (e) => {
      if (e.key === "Enter") document.getElementById("files-go").click();
    });
    document.getElementById("files-up").onclick = () => {
      if (!cwd) return;
      const parts = cwd.replace(/\\/g, "/").split("/").filter(Boolean);
      parts.pop();
      cwd = parts.length ? (cwd.startsWith("/") ? "/" + parts.join("/") : parts.join("\\")) : "";
      document.getElementById("files-path").value = cwd;
      load(container);
    };
    document.getElementById("files-home").onclick = () => { cwd = null; document.getElementById("files-path").value = ""; load(container); };
    document.getElementById("files-upload").onclick = () => document.getElementById("files-upload-input").click();
    document.getElementById("files-upload-input").addEventListener("change", (e) => {
      const file = e.target.files[0];
      if (file) uploadFile(container, file);
    });
  }

  // Carica l'elenco della directory corrente via comando remoto.
  async function load(container) {
    const tbody = document.getElementById("files-tbody");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="3" style="color:var(--muted)">Caricamento…</td></tr>`;
    const dir = cwd || "";
    const cmd = dir ? (dir.includes("\\") || dir.includes(":"))
      ? `dir "${dir}"`
      : `ls -la "${dir}"`
      : "ls -la";
    try {
      const res = await App.api(`/api/agents/${encodeURIComponent(agentId)}/command`, {
        method: "POST", body: { command: cmd, timeout: 20, shell: "" },
      });
      if (res.status === "queued") {
        tbody.innerHTML = `<tr><td colspan="3" style="color:var(--warning)">Agente offline: comando accodato.</td></tr>`;
        return;
      }
      // Analizza l'output: per ls/dir estraiamo i nomi. Fallback: mostra l'output crudo.
      const output = (res.stdout || "") + (res.stderr ? "\n[stderr]\n" + res.stderr : "");
      if (!output.trim()) {
        tbody.innerHTML = `<tr><td colspan="3" style="color:var(--muted)">Nessun output (agent: ${res.exit_code}).</td></tr>`;
        return;
      }
      // Prova a estrarre righe: per ls -la classico
      const lines = output.split("\n").map((l) => l.trim()).filter(Boolean);
      if (lines.length && (lines[0].startsWith("total") || lines.some((l) => /^[-dlrwx]{10}/.test(l)))) {
        renderLsLines(lines, tbody);
      } else {
        // dir di Windows o output non parsato: mostra crudo
        renderRaw(output, tbody);
      }
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="3" style="color:var(--danger)">Errore: ${App.escapeHtml(e.message)}</td></tr>`;
    }
  }

  function renderLsLines(lines, tbody) {
    const rows = [];
    for (const line of lines) {
      // riga tipo "drwxr-xr-x  user group  size  date  name" (Linux/macOS)
      const m = line.match(/^([dl-][rwxsStT-]{9})\s+\S+\s+\S+\s+(\S+)\s+\S+\s+\S+\s+\S+\s+(.+)$/);
      if (m) {
        const isDir = m[1][0] === "d";
        const name = m[3];
        if (name === "." || name === "..") continue;
        rows.push({ name, isDir, size: m[2] });
        continue;
      }
      // riga tipo Windows "MM/DD/YYYY  HH:MM  <DIR>  Name" oppure "MM/DD/YYYY  HH:MM  12345  Name"
      const w = line.match(/^(\d{1,2}\/\d{1,2}\/\d{4})\s+([\d:]+)\s+(<DIR>|[0-9,]+)\s+(.+)$/);
      if (w) {
        const isDir = w[3] === "<DIR>";
        const name = w[4];
        if (name === "." || name === "..") continue;
        rows.push({ name, isDir, size: isDir ? "" : w[3] });
        continue;
      }
    }
    if (!rows.length) {
      renderRaw(lines.join("\n"), tbody);
      return;
    }
    tbody.innerHTML = rows.map((r) => `
      <tr>
        <td style="cursor:pointer" class="fname" data-name="${App.escapeHtml(r.name)}" data-dir="${r.isDir}">
          ${r.isDir ? "📁" : "📄"} ${App.escapeHtml(r.name)}
        </td>
        <td>${r.isDir ? "Cartella" : App.fmtBytes(parseFloat(String(r.size).replace(/,/g, ""))) || "—"}</td>
        <td>${r.isDir ? "" : `<button class="ghost fdl" data-name="${App.escapeHtml(r.name)}">⬇ Download</button>`}</td>
      </tr>`).join("");
    tbody.querySelectorAll(".fname").forEach((td) => td.addEventListener("click", () => {
      if (td.dataset.dir === "true") {
        cwd = joinPath(cwd, td.dataset.name);
        document.getElementById("files-path").value = cwd;
        load(container);
      }
    }));
    tbody.querySelectorAll(".fdl").forEach((b) => b.addEventListener("click", () => downloadFile(b.dataset.name)));
  }

  function renderRaw(output, tbody) {
    const esc = App.escapeHtml(output);
    tbody.innerHTML = `<tr><td colspan="3"><pre style="white-space:pre-wrap;color:var(--muted);font-size:0.8rem;max-height:300px;overflow:auto">${esc}</pre></td></tr>`;
  }

  function joinPath(base, name) {
    if (!base) return name;
    if (base.includes("\\")) return base + "\\" + name;
    return base.replace(/\/$/, "") + "/" + name;
  }

  async function downloadFile(name) {
    const path = joinPath(cwd, name);
    try {
      const res = await App.api(`/api/agents/${encodeURIComponent(agentId)}/file/download`, {
        method: "POST", body: { path },
      });
      if (res.content_b64) {
        const blob = new Blob([Uint8Array.from(atob(res.content_b64), (c) => c.charCodeAt(0))]);
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = name;
        a.click();
        URL.revokeObjectURL(a.href);
        App.toast(`Download: ${name} ✓`, "success");
      } else {
        App.toast("File non disponibile (path non consentito o errore).", "error");
      }
    } catch (e) { App.toast("Errore download: " + e.message, "error"); }
  }

  async function uploadFile(container, file) {
    const path = joinPath(cwd || "", file.name);
    try {
      const reader = new FileReader();
      reader.onload = async () => {
        const b64 = reader.result.split(",")[1];
        const res = await App.api(`/api/agents/${encodeURIComponent(agentId)}/file/upload`, {
          method: "POST", body: { path, content_b64: b64, overwrite: true },
        });
        if (res && res.success) App.toast(`Upload ${file.name} ✓`, "success");
        else App.toast("Upload rifiutato (verifica path consentito).", "error");
        load(container);
      };
      reader.readAsDataURL(file);
    } catch (e) { App.toast("Errore upload: " + e.message, "error"); }
  }

  // ═══ Esposizione pubblica ═══
  window.CaduceoFiles = { open };
})();
