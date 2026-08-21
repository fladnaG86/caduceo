/**
 * ai.js — Modulo "Assistente IA" per la Console di Gestione Caduceo
 *
 * Fornisce un'interfaccia chat agenticica che consente all'operatore di
 * descrivere in linguaggio naturale operazioni da eseguire sui PC remoti.
 * L'IA (endpoint OpenAI-compatibile, configurabile) decide autonomamente
 * quali comandi shell eseguire su quali agenti, osserva gli output tramite
 * l'API del relay FastAPI e risponde con una sintesi in italiano.
 *
 * Esposizione globale: window.CaduceoAI = { init(container), render(container, ctx) }
 *
 * Dipendenze: window.App (definito in app.js) con metodi api(), toast(),
 * escapeHtml(), fmtTs().
 *
 * Sicurezza: tutti i testi non fidati (stdout/stderr remoti) vengono
 * inseriti tramite textContent — mai innerHTML — per prevenire XSS.
 * La configurazione API (endpoint, api_key) non viene mai inviata al relay.
 *
 * Autore: generato per Caduceo Relay
 */

(function () {
  'use strict';

  /* ────────────────────────────────────────────────────────────────────────
   * Costanti
   * ──────────────────────────────────────────────────────────────────────── */
  const LS_KEY        = 'caduceo_ai_config';
  const MAX_ITER      = 10;
  const FETCH_TIMEOUT = 60000; // ms

  /* ────────────────────────────────────────────────────────────────────────
   * Stato del modulo (condiviso tra init e render)
   * ──────────────────────────────────────────────────────────────────────── */
  let _state = {
    agents:        [],          // lista agenti dal relay
    selectedAgents: new Set(),  // agent_id selezionati
    messages:      [],          // storia conversazione (ruoli OpenAI)
    running:       false,       // ciclo in esecuzione
    abortFlag:     false,       // segnale di interruzione
    configVisible: true,        // pannello configurazione aperto/chiuso
  };

  /* ────────────────────────────────────────────────────────────────────────
   * CSS scoped (iniettato nel container, non in style.css)
   * ──────────────────────────────────────────────────────────────────────── */
  const SCOPED_CSS = `
.cai-root {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 12px;
  font-family: inherit;
  color: var(--text);
}

/* ── Pannello configurazione ── */
.cai-config-panel {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.cai-config-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;
  background: var(--card);
}
.cai-config-header:hover { background: rgba(255,255,255,.04); }
.cai-config-title {
  font-weight: 600;
  font-size: .9rem;
  display: flex;
  align-items: center;
  gap: 8px;
}
.cai-config-chevron { transition: transform .2s; font-size: .8rem; }
.cai-config-chevron.open { transform: rotate(180deg); }
.cai-config-body {
  padding: 14px;
  border-top: 1px solid var(--border);
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.cai-config-body.hidden { display: none; }
.cai-field { display: flex; flex-direction: column; gap: 4px; }
.cai-field.full { grid-column: 1 / -1; }
.cai-field label { font-size: .78rem; color: var(--muted); }
.cai-field input {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  padding: 7px 10px;
  font-size: .85rem;
  outline: none;
  transition: border-color .15s;
}
.cai-field input:focus { border-color: var(--accent); }
.cai-save-btn {
  grid-column: 1 / -1;
  justify-self: end;
}

/* ── Agenti ── */
.cai-agents-panel {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 180px;
  overflow: hidden;
}
.cai-agents-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}
.cai-agents-title {
  font-weight: 600;
  font-size: .9rem;
}
.cai-agents-list {
  overflow-y: auto;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.cai-agent-item {
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 5px 10px;
  cursor: pointer;
  user-select: none;
  font-size: .82rem;
  transition: border-color .15s, background .15s;
}
.cai-agent-item:hover { border-color: var(--accent); }
.cai-agent-item.selected {
  border-color: var(--accent);
  background: rgba(99,102,241,.15);
}
.cai-agent-item input[type=checkbox] { accent-color: var(--accent); cursor: pointer; }
.cai-agent-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.cai-agent-dot.online  { background: var(--success); }
.cai-agent-dot.offline { background: var(--danger); }
.cai-no-agents { color: var(--muted); font-size: .85rem; }

/* ── Chat ── */
.cai-chat-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  min-height: 300px;
}
.cai-chat-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.cai-chat-toolbar-title {
  font-weight: 600;
  font-size: .9rem;
}
.cai-chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
/* Bolle */
.cai-bubble {
  max-width: 82%;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: .88rem;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}
.cai-bubble.user {
  align-self: flex-end;
  background: var(--accent);
  color: #fff;
  border-bottom-right-radius: 2px;
}
.cai-bubble.assistant {
  align-self: flex-start;
  background: var(--bg);
  border: 1px solid var(--border);
  border-bottom-left-radius: 2px;
}
.cai-bubble.error {
  align-self: flex-start;
  background: rgba(239,68,68,.15);
  border: 1px solid var(--danger);
  color: var(--danger);
}
/* Step agentico */
.cai-step {
  align-self: flex-start;
  width: calc(100% - 2px);
  background: rgba(0,0,0,.25);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: .82rem;
  font-family: 'Courier New', monospace;
  overflow-x: auto;
}
.cai-step-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
  font-weight: 600;
  color: var(--accent);
}
.cai-step-label-queued { color: var(--warning); }
.cai-step-label-error  { color: var(--danger); }
.cai-step-section { margin-top: 6px; }
.cai-step-section-title { color: var(--muted); font-size: .78rem; margin-bottom: 2px; }
.cai-step-section-content {
  background: rgba(0,0,0,.3);
  border-radius: 4px;
  padding: 6px 8px;
  max-height: 120px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
/* Indicatore di pensiero */
.cai-thinking {
  align-self: flex-start;
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
  font-size: .85rem;
  padding: 6px 0;
}
.cai-dots span {
  display: inline-block;
  width: 6px; height: 6px;
  border-radius: 50%;
  background: var(--muted);
  animation: cai-pulse 1.2s ease-in-out infinite;
}
.cai-dots span:nth-child(2) { animation-delay: .2s; }
.cai-dots span:nth-child(3) { animation-delay: .4s; }
@keyframes cai-pulse {
  0%, 80%, 100% { opacity: .3; transform: scale(.8); }
  40%           { opacity: 1;  transform: scale(1);   }
}

/* ── Input area ── */
.cai-input-row {
  display: flex;
  gap: 8px;
  padding: 10px 14px;
  border-top: 1px solid var(--border);
  flex-shrink: 0;
  align-items: flex-end;
}
.cai-textarea {
  flex: 1;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text);
  padding: 9px 12px;
  font-size: .88rem;
  font-family: inherit;
  resize: none;
  outline: none;
  min-height: 38px;
  max-height: 140px;
  line-height: 1.4;
  overflow-y: auto;
  transition: border-color .15s;
}
.cai-textarea:focus { border-color: var(--accent); }
.cai-textarea:disabled { opacity: .5; cursor: not-allowed; }

/* ── Bottoni generici ── */
.cai-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: none;
  border-radius: 6px;
  padding: 7px 13px;
  font-size: .82rem;
  font-weight: 600;
  cursor: pointer;
  transition: opacity .15s, filter .15s;
  white-space: nowrap;
}
.cai-btn:disabled { opacity: .45; cursor: not-allowed; }
.cai-btn:not(:disabled):hover { filter: brightness(1.12); }
.cai-btn-primary   { background: var(--accent);  color: #fff; }
.cai-btn-secondary { background: var(--border);  color: var(--text); }
.cai-btn-danger    { background: var(--danger);  color: #fff; }
.cai-btn-sm { padding: 5px 10px; font-size: .78rem; }
`;

  /* ────────────────────────────────────────────────────────────────────────
   * Helpers
   * ──────────────────────────────────────────────────────────────────────── */

  /** Legge la configurazione da localStorage. */
  function loadConfig() {
    try {
      return JSON.parse(localStorage.getItem(LS_KEY) || '{}');
    } catch (_) {
      return {};
    }
  }

  /** Salva la configurazione in localStorage. */
  function saveConfig(cfg) {
    localStorage.setItem(LS_KEY, JSON.stringify(cfg));
  }

  /** Crea un elemento DOM con classe e testo opzionale. */
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls)  e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  /** Crea un bottone stilizzato. */
  function btn(label, cls, icon) {
    const b = el('button', 'cai-btn ' + (cls || ''));
    if (icon) {
      const s = el('span');
      s.textContent = icon;
      b.appendChild(s);
    }
    const t = el('span', '', label);
    b.appendChild(t);
    return b;
  }

  /** Promise con timeout su fetch. */
  async function fetchWithTimeout(url, opts, timeoutMs) {
    const ctrl = new AbortController();
    const tid   = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const r = await fetch(url, { ...opts, signal: ctrl.signal });
      return r;
    } finally {
      clearTimeout(tid);
    }
  }

  /** Scorre il container dei messaggi fino in fondo. */
  function scrollBottom(el) {
    el.scrollTop = el.scrollHeight;
  }

  /* ────────────────────────────────────────────────────────────────────────
   * Costruzione UI
   * ──────────────────────────────────────────────────────────────────────── */

  function buildUI(container) {
    // Inietta CSS scoped
    container.innerHTML = '';
    const styleTag = document.createElement('style');
    styleTag.textContent = SCOPED_CSS;
    container.appendChild(styleTag);

    const root = el('div', 'cai-root');
    container.appendChild(root);

    // ── Pannello configurazione ──────────────────────────────────────────
    const configPanel  = el('div', 'cai-config-panel');
    const configHeader = el('div', 'cai-config-header');
    const configTitle  = el('div', 'cai-config-title');
    const configIcon   = document.createElement('span');
    configIcon.textContent = '⚙️';
    const configTitleText = document.createTextNode('Configurazione LLM');
    configTitle.appendChild(configIcon);
    configTitle.appendChild(configTitleText);
    const chevron = el('span', 'cai-config-chevron open', '▼');
    configHeader.appendChild(configTitle);
    configHeader.appendChild(chevron);
    configPanel.appendChild(configHeader);

    const configBody = el('div', 'cai-config-body');

    // Campo Endpoint
    const fEndpoint = el('div', 'cai-field full');
    const lEndpoint = el('label', '', 'Endpoint URL');
    const iEndpoint = el('input');
    iEndpoint.type = 'text';
    iEndpoint.placeholder = 'https://api.openai.com/v1  oppure  http://192.168.x.x:11434/v1';
    iEndpoint.id = 'cai-endpoint';
    fEndpoint.appendChild(lEndpoint);
    fEndpoint.appendChild(iEndpoint);

    // Campo API Key
    const fKey = el('div', 'cai-field');
    const lKey = el('label', '', 'API Key');
    const iKey = el('input');
    iKey.type = 'password';
    iKey.placeholder = 'sk-… (lascia vuoto se non richiesta)';
    iKey.id = 'cai-apikey';
    fKey.appendChild(lKey);
    fKey.appendChild(iKey);

    // Campo Modello
    const fModel = el('div', 'cai-field');
    const lModel = el('label', '', 'Modello');
    const iModel = el('input');
    iModel.type = 'text';
    iModel.placeholder = 'gpt-4o-mini, llama3.2, mistral, …';
    iModel.id = 'cai-model';
    fModel.appendChild(lModel);
    fModel.appendChild(iModel);

    const saveBtn = btn('Salva configurazione', 'cai-btn-primary cai-save-btn', '💾');

    configBody.appendChild(fEndpoint);
    configBody.appendChild(fKey);
    configBody.appendChild(fModel);
    configBody.appendChild(saveBtn);
    configPanel.appendChild(configBody);
    root.appendChild(configPanel);

    // ── Pannello agenti ──────────────────────────────────────────────────
    const agentsPanel  = el('div', 'cai-agents-panel');
    const agentsHeader = el('div', 'cai-agents-header');
    const agentsTitle  = el('div', 'cai-agents-title', '🖥️ Agenti disponibili');
    const agentsBtns   = el('div');
    agentsBtns.style.display = 'flex';
    agentsBtns.style.gap = '6px';
    const selectAllBtn  = btn('Seleziona tutti',  'cai-btn-secondary cai-btn-sm');
    const refreshAgents = btn('Aggiorna',         'cai-btn-secondary cai-btn-sm', '🔄');
    agentsBtns.appendChild(selectAllBtn);
    agentsBtns.appendChild(refreshAgents);
    agentsHeader.appendChild(agentsTitle);
    agentsHeader.appendChild(agentsBtns);
    const agentsList = el('div', 'cai-agents-list');
    agentsPanel.appendChild(agentsHeader);
    agentsPanel.appendChild(agentsList);
    root.appendChild(agentsPanel);

    // ── Pannello chat ────────────────────────────────────────────────────
    const chatPanel   = el('div', 'cai-chat-panel');
    const chatToolbar = el('div', 'cai-chat-toolbar');
    const chatTitle   = el('div', 'cai-chat-toolbar-title', '💬 Assistente IA Agentico');
    const chatBtns    = el('div');
    chatBtns.style.display = 'flex';
    chatBtns.style.gap = '6px';
    const newConvBtn  = btn('Nuova conversazione', 'cai-btn-secondary cai-btn-sm', '🗑️');
    const stopBtn     = btn('Interrompi', 'cai-btn-danger cai-btn-sm', '⏹️');
    stopBtn.disabled  = true;
    chatBtns.appendChild(newConvBtn);
    chatBtns.appendChild(stopBtn);
    chatToolbar.appendChild(chatTitle);
    chatToolbar.appendChild(chatBtns);

    const chatMessages = el('div', 'cai-chat-messages');

    const inputRow = el('div', 'cai-input-row');
    const textarea = el('textarea', 'cai-textarea');
    textarea.placeholder = 'Scrivi un\'operazione in linguaggio naturale… (es. "controlla il disco su pc1 e dimmi se è ok")';
    textarea.rows = 2;
    const sendBtn = btn('Invia', 'cai-btn-primary', '➤');

    inputRow.appendChild(textarea);
    inputRow.appendChild(sendBtn);

    chatPanel.appendChild(chatToolbar);
    chatPanel.appendChild(chatMessages);
    chatPanel.appendChild(inputRow);
    root.appendChild(chatPanel);

    // ── Evento: toggle configurazione ────────────────────────────────────
    configHeader.addEventListener('click', () => {
      _state.configVisible = !_state.configVisible;
      if (_state.configVisible) {
        configBody.classList.remove('hidden');
        chevron.classList.add('open');
      } else {
        configBody.classList.add('hidden');
        chevron.classList.remove('open');
      }
    });

    // ── Evento: salva configurazione ─────────────────────────────────────
    saveBtn.addEventListener('click', () => {
      const cfg = {
        endpoint: iEndpoint.value.trim().replace(/\/$/, ''),
        api_key:  iKey.value.trim(),
        model:    iModel.value.trim(),
      };
      saveConfig(cfg);
      window.App.toast('Configurazione salvata', 'success');
    });

    // ── Evento: seleziona tutti agenti ───────────────────────────────────
    selectAllBtn.addEventListener('click', () => {
      const allSelected = _state.agents.length > 0 &&
        _state.agents.every(a => _state.selectedAgents.has(a.agent_id));
      if (allSelected) {
        _state.selectedAgents.clear();
      } else {
        _state.agents.forEach(a => _state.selectedAgents.add(a.agent_id));
      }
      renderAgentsList(agentsList);
    });

    // ── Evento: aggiorna lista agenti ────────────────────────────────────
    refreshAgents.addEventListener('click', () => {
      loadAgents(agentsList);
    });

    // ── Evento: nuova conversazione ──────────────────────────────────────
    newConvBtn.addEventListener('click', () => {
      if (_state.running) return;
      _state.messages = [];
      chatMessages.innerHTML = '';
      appendWelcome(chatMessages);
    });

    // ── Evento: interrompi ───────────────────────────────────────────────
    stopBtn.addEventListener('click', () => {
      if (_state.running) {
        _state.abortFlag = true;
        stopBtn.disabled = true;
      }
    });

    // ── Evento: invio messaggio ──────────────────────────────────────────
    const doSend = () => {
      const text = textarea.value.trim();
      if (!text || _state.running) return;
      textarea.value = '';
      autoResizeTextarea(textarea);
      runAgenticLoop(text, chatMessages, stopBtn, textarea, sendBtn);
    };

    sendBtn.addEventListener('click', doSend);
    textarea.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        doSend();
      }
    });
    textarea.addEventListener('input', () => autoResizeTextarea(textarea));

    // ── Popolamento iniziale ─────────────────────────────────────────────
    const cfg = loadConfig();
    iEndpoint.value = cfg.endpoint || '';
    iKey.value      = cfg.api_key  || '';
    iModel.value    = cfg.model    || '';

    appendWelcome(chatMessages);
    loadAgents(agentsList);

    return { chatMessages, stopBtn, textarea, sendBtn };
  }

  /* ────────────────────────────────────────────────────────────────────────
   * Lista agenti
   * ──────────────────────────────────────────────────────────────────────── */

  async function loadAgents(listEl) {
    listEl.innerHTML = '';
    const loading = el('span', 'cai-no-agents', '⏳ Caricamento agenti…');
    listEl.appendChild(loading);
    try {
      const data = await window.App.api('/api/agents');
      _state.agents = (data && data.agents) ? data.agents : [];
      renderAgentsList(listEl);
    } catch (err) {
      listEl.innerHTML = '';
      const errEl = el('span', 'cai-no-agents');
      errEl.textContent = '⚠️ Impossibile caricare gli agenti: ' + (err.message || err);
      listEl.appendChild(errEl);
    }
  }

  function renderAgentsList(listEl) {
    listEl.innerHTML = '';
    if (!_state.agents.length) {
      const none = el('span', 'cai-no-agents', 'Nessun agente connesso.');
      listEl.appendChild(none);
      return;
    }
    _state.agents.forEach(agent => {
      const item = el('div', 'cai-agent-item');
      if (_state.selectedAgents.has(agent.agent_id)) {
        item.classList.add('selected');
      }

      const chk = document.createElement('input');
      chk.type    = 'checkbox';
      chk.checked = _state.selectedAgents.has(agent.agent_id);

      const dot = el('span', 'cai-agent-dot ' + (agent.online ? 'online' : 'offline'));

      const label = el('span');
      const hostname = el('strong', '', agent.hostname || agent.agent_id);
      const idSpan   = el('small', '');
      idSpan.style.color  = 'var(--muted)';
      idSpan.style.marginLeft = '4px';
      idSpan.textContent  = '(' + agent.agent_id + ')';
      label.appendChild(hostname);
      label.appendChild(idSpan);

      item.appendChild(chk);
      item.appendChild(dot);
      item.appendChild(label);

      item.addEventListener('click', (e) => {
        // evita doppio toggle sul click direttamente sul checkbox
        if (e.target === chk) return;
        chk.checked = !chk.checked;
        toggleAgent(agent.agent_id, chk.checked, item);
      });
      chk.addEventListener('change', () => {
        toggleAgent(agent.agent_id, chk.checked, item);
      });

      listEl.appendChild(item);
    });
  }

  function toggleAgent(agentId, selected, itemEl) {
    if (selected) {
      _state.selectedAgents.add(agentId);
      itemEl.classList.add('selected');
    } else {
      _state.selectedAgents.delete(agentId);
      itemEl.classList.remove('selected');
    }
  }

  /* ────────────────────────────────────────────────────────────────────────
   * Chat helpers
   * ──────────────────────────────────────────────────────────────────────── */

  function autoResizeTextarea(ta) {
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 140) + 'px';
  }

  function appendWelcome(chatMessages) {
    const bubble = el('div', 'cai-bubble assistant');
    bubble.textContent =
      'Ciao! Sono l\'Assistente IA Agentico di Caduceo.\n\n' +
      'Descrivi in linguaggio naturale cosa vuoi fare sui PC remoti ' +
      '(es. "controlla lo spazio disco su pc1", "esegui whoami su tutti gli agenti selezionati").\n\n' +
      'Configura l\'endpoint LLM in alto, seleziona gli agenti e inizia a scrivere.';
    chatMessages.appendChild(bubble);
  }

  function appendUserBubble(chatMessages, text) {
    const bubble = el('div', 'cai-bubble user');
    bubble.textContent = text;
    chatMessages.appendChild(bubble);
    scrollBottom(chatMessages);
    return bubble;
  }

  function appendAssistantBubble(chatMessages, text) {
    const bubble = el('div', 'cai-bubble assistant');
    bubble.textContent = text;
    chatMessages.appendChild(bubble);
    scrollBottom(chatMessages);
    return bubble;
  }

  function appendErrorBubble(chatMessages, text) {
    const bubble = el('div', 'cai-bubble error');
    bubble.textContent = '⚠️ ' + text;
    chatMessages.appendChild(bubble);
    scrollBottom(chatMessages);
  }

  function appendThinking(chatMessages) {
    const row = el('div', 'cai-thinking');
    const ico = el('span', '', '🤖');
    const lbl = document.createTextNode(' L\'IA sta elaborando');
    const dots = el('span', 'cai-dots');
    [0, 1, 2].forEach(() => dots.appendChild(el('span')));
    row.appendChild(ico);
    row.appendChild(lbl);
    row.appendChild(dots);
    chatMessages.appendChild(row);
    scrollBottom(chatMessages);
    return row;
  }

  /**
   * Aggiunge un blocco "step agentico" con intestazione e sezioni stdout/stderr.
   * Usa solo textContent per sicurezza XSS.
   */
  function appendStepBlock(chatMessages, agentInfo, command, result) {
    const step = el('div', 'cai-step');

    const header = el('div', 'cai-step-header');
    const arrow  = el('span', '', '▶ ');
    const agentLbl = el('span', '', agentInfo + ':');
    agentLbl.style.marginRight = '4px';
    const cmdLbl  = el('span', '', command);
    header.appendChild(arrow);
    header.appendChild(agentLbl);
    header.appendChild(cmdLbl);

    step.appendChild(header);

    // Caso agente offline (queued)
    if (result && result.status === 'queued') {
      const q = el('div', 'cai-step-label-queued', '⏳ Agente offline — comando in coda');
      step.appendChild(q);
      chatMessages.appendChild(step);
      scrollBottom(chatMessages);
      return;
    }

    // Mostra exit code
    if (result && result.exit_code !== undefined) {
      const ec = el('div');
      ec.style.marginTop = '4px';
      ec.style.fontSize = '.78rem';
      ec.style.color = result.exit_code === 0 ? 'var(--success)' : 'var(--danger)';
      ec.textContent = 'Exit code: ' + result.exit_code +
        (result.duration_ms !== undefined ? '  |  Durata: ' + result.duration_ms + ' ms' : '');
      step.appendChild(ec);
    }

    // stdout
    if (result && result.stdout && result.stdout.trim()) {
      const sec = el('div', 'cai-step-section');
      const stitle = el('div', 'cai-step-section-title', 'stdout:');
      const scontent = el('div', 'cai-step-section-content');
      scontent.textContent = result.stdout; // textContent — NON innerHTML
      sec.appendChild(stitle);
      sec.appendChild(scontent);
      step.appendChild(sec);
    }

    // stderr
    if (result && result.stderr && result.stderr.trim()) {
      const sec = el('div', 'cai-step-section');
      const stitle = el('div', 'cai-step-section-title');
      stitle.style.color = 'var(--warning)';
      stitle.textContent = 'stderr:';
      const scontent = el('div', 'cai-step-section-content');
      scontent.style.color = 'var(--warning)';
      scontent.textContent = result.stderr; // textContent — NON innerHTML
      sec.appendChild(stitle);
      sec.appendChild(scontent);
      step.appendChild(sec);
    }

    // Errore generico (es. eccezione di rete sull'agente)
    if (!result || (result.exit_code === undefined && result.status !== 'queued')) {
      const errLbl = el('div', 'cai-step-label-error', '✗ Errore nell\'esecuzione del comando');
      step.appendChild(errLbl);
    }

    chatMessages.appendChild(step);
    scrollBottom(chatMessages);
  }

  /* ────────────────────────────────────────────────────────────────────────
   * Costruzione del system prompt
   * ──────────────────────────────────────────────────────────────────────── */

  function buildSystemPrompt() {
    const agentsDesc = _state.agents
      .filter(a => _state.selectedAgents.has(a.agent_id))
      .map(a => `  - agent_id="${a.agent_id}", hostname="${a.hostname || a.agent_id}", ` +
                `os="${a.os || 'sconosciuto'}", online=${a.online ? 'sì' : 'no'}`)
      .join('\n');

    return `Sei un assistente esperto di amministrazione di sistema per la piattaforma Caduceo.
Puoi eseguire comandi shell su PC Windows e Linux remoti tramite lo strumento "run_command".
Rispondi SEMPRE in italiano.

Agenti disponibili (selezionati dall'operatore):
${agentsDesc || '  (nessun agente selezionato)'}

Linee guida operative:
1. Analizza la richiesta dell'operatore e pianifica i comandi necessari.
2. Usa run_command per eseguire i comandi sugli agenti appropriati.
3. Osserva l'output e, se necessario, esegui altri comandi per approfondire.
4. Quando hai raccolto tutte le informazioni, fornisci una risposta chiara e sintetica all'operatore.
5. Usa comandi adeguati all'OS dell'agente (PowerShell/CMD su Windows, bash su Linux).
6. Non eseguire comandi distruttivi o pericolosi senza esplicita conferma.
7. Se un agente è offline, segnalalo nella risposta finale.
8. Sii conciso ma esaustivo nella risposta finale.`;
  }

  /* ────────────────────────────────────────────────────────────────────────
   * Tool definitions per OpenAI
   * ──────────────────────────────────────────────────────────────────────── */

  const TOOLS = [
    {
      type: 'function',
      function: {
        name: 'run_command',
        description: 'Esegue un comando shell su un agente remoto selezionato.',
        parameters: {
          type: 'object',
          properties: {
            agent_id: {
              type: 'string',
              description: 'L\'ID univoco dell\'agente su cui eseguire il comando.',
            },
            command: {
              type: 'string',
              description: 'Il comando shell da eseguire (bash, PowerShell, cmd).',
            },
            timeout: {
              type: 'integer',
              description: 'Timeout in secondi (default 30).',
            },
          },
          required: ['agent_id', 'command'],
        },
      },
    },
  ];

  /* ────────────────────────────────────────────────────────────────────────
   * Ciclo agentico principale
   * ──────────────────────────────────────────────────────────────────────── */

  async function runAgenticLoop(userText, chatMessages, stopBtn, textarea, sendBtn) {
    const cfg = loadConfig();

    // Validazione configurazione
    if (!cfg.endpoint) {
      appendErrorBubble(chatMessages,
        'Endpoint LLM non configurato. Apri il pannello "Configurazione LLM" e inserisci l\'URL.');
      return;
    }
    if (!cfg.model) {
      appendErrorBubble(chatMessages,
        'Modello non configurato. Inserisci il nome del modello (es. gpt-4o-mini) nella configurazione.');
      return;
    }

    // Validazione agenti selezionati
    if (_state.selectedAgents.size === 0) {
      appendErrorBubble(chatMessages,
        'Nessun agente selezionato. Seleziona almeno un agente dalla lista prima di inviare un comando.');
      return;
    }

    // Aggiorna stato UI
    _state.running   = true;
    _state.abortFlag = false;
    stopBtn.disabled  = false;
    textarea.disabled = true;
    sendBtn.disabled  = true;

    // Mostra bolla utente
    appendUserBubble(chatMessages, userText);

    // Aggiungi messaggio utente alla storia
    _state.messages.push({ role: 'user', content: userText });

    // Prepara la lista messaggi con system prompt
    const systemMsg = { role: 'system', content: buildSystemPrompt() };

    let iterCount = 0;
    let thinkingEl = null;

    try {
      while (iterCount < MAX_ITER) {
        if (_state.abortFlag) {
          appendErrorBubble(chatMessages, 'Ciclo interrotto dall\'operatore.');
          break;
        }

        iterCount++;

        // Mostra indicatore di pensiero
        thinkingEl = appendThinking(chatMessages);

        // Costruisce la lista messaggi completa: system + storia
        const msgs = [systemMsg, ..._state.messages];

        // Headers
        const headers = { 'Content-Type': 'application/json' };
        if (cfg.api_key) {
          headers['Authorization'] = 'Bearer ' + cfg.api_key;
        }

        // Chiamata al LLM
        let response;
        try {
          response = await fetchWithTimeout(
            cfg.endpoint + '/chat/completions',
            {
              method: 'POST',
              headers,
              body: JSON.stringify({
                model:       cfg.model,
                messages:    msgs,
                temperature: 0.2,
                tools:       TOOLS,
              }),
            },
            FETCH_TIMEOUT
          );
        } catch (fetchErr) {
          if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; }
          if (fetchErr.name === 'AbortError') {
            appendErrorBubble(chatMessages, 'Timeout: il server LLM non ha risposto entro ' + (FETCH_TIMEOUT / 1000) + ' secondi.');
          } else {
            appendErrorBubble(chatMessages, 'Errore di rete nella chiamata al LLM: ' + fetchErr.message);
          }
          break;
        }

        if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; }

        if (!response.ok) {
          let detail = 'Errore HTTP ' + response.status;
          try {
            const errBody = await response.json();
            if (errBody && errBody.error && errBody.error.message) {
              detail += ': ' + errBody.error.message;
            }
          } catch (_) { /* ignora */ }
          appendErrorBubble(chatMessages, detail);
          break;
        }

        let data;
        try {
          data = await response.json();
        } catch (_) {
          appendErrorBubble(chatMessages, 'Risposta del server LLM non valida (JSON non parsabile).');
          break;
        }

        const choice = data && data.choices && data.choices[0];
        if (!choice || !choice.message) {
          appendErrorBubble(chatMessages, 'Risposta LLM vuota o malformata.');
          break;
        }

        const assistantMsg = choice.message;

        // Aggiungi il messaggio dell'assistente alla storia
        _state.messages.push(assistantMsg);

        // ── Risposta finale (nessun tool_calls) ──────────────────────────
        if (!assistantMsg.tool_calls || assistantMsg.tool_calls.length === 0) {
          const finalText = assistantMsg.content || '(nessuna risposta)';
          appendAssistantBubble(chatMessages, finalText);
          break;
        }

        // ── Tool calls: esegui i comandi ─────────────────────────────────
        const toolResults = [];

        for (const toolCall of assistantMsg.tool_calls) {
          if (_state.abortFlag) break;

          if (toolCall.function.name !== 'run_command') {
            // Tool sconosciuto: rispondi con errore
            toolResults.push({
              role:         'tool',
              tool_call_id: toolCall.id,
              content:      JSON.stringify({ error: 'Tool non supportato: ' + toolCall.function.name }),
            });
            continue;
          }

          let args;
          try {
            args = JSON.parse(toolCall.function.arguments);
          } catch (_) {
            toolResults.push({
              role:         'tool',
              tool_call_id: toolCall.id,
              content:      JSON.stringify({ error: 'Argomenti non validi (JSON malformato)' }),
            });
            continue;
          }

          const { agent_id, command, timeout: cmdTimeout } = args;

          // Verifica che l'agente sia tra quelli selezionati
          if (!_state.selectedAgents.has(agent_id)) {
            const errMsg = 'Agente "' + agent_id + '" non trovato o non selezionato.';
            appendStepBlock(chatMessages, agent_id, command, { error: errMsg });
            toolResults.push({
              role:         'tool',
              tool_call_id: toolCall.id,
              content:      JSON.stringify({ error: errMsg }),
            });
            continue;
          }

          // Info agente per il display
          const agentInfo = (() => {
            const a = _state.agents.find(x => x.agent_id === agent_id);
            return a ? (a.hostname || agent_id) : agent_id;
          })();

          // Esegui il comando tramite App.api
          let cmdResult;
          try {
            cmdResult = await window.App.api(
              '/api/agents/' + encodeURIComponent(agent_id) + '/command',
              {
                method: 'POST',
                body: {
                  command: command,
                  timeout: cmdTimeout || 30,
                  shell:   '',
                },
              }
            );
          } catch (cmdErr) {
            cmdResult = { error: 'Errore nell\'esecuzione: ' + (cmdErr.message || String(cmdErr)) };
          }

          // Mostra lo step a video
          appendStepBlock(chatMessages, agentInfo, command, cmdResult);

          // Aggiunge il risultato come messaggio tool
          toolResults.push({
            role:         'tool',
            tool_call_id: toolCall.id,
            content:      JSON.stringify(cmdResult),
          });
        } // fine for toolCalls

        // Aggiungi tutti i risultati tool alla storia
        toolResults.forEach(tr => _state.messages.push(tr));

        // Se interrotto dopo tool calls
        if (_state.abortFlag) {
          appendErrorBubble(chatMessages, 'Ciclo interrotto dall\'operatore dopo l\'esecuzione dei comandi.');
          break;
        }

        // Se iterazione massima raggiunta
        if (iterCount >= MAX_ITER) {
          appendErrorBubble(chatMessages,
            'Raggiunto il limite massimo di ' + MAX_ITER + ' iterazioni. ' +
            'Il ciclo si è fermato per sicurezza.');
          break;
        }

        // Continua il loop (il LLM riceverà i risultati dei tool)
      }
    } finally {
      // Ripristina UI
      if (thinkingEl) { thinkingEl.remove(); }
      _state.running   = false;
      _state.abortFlag = false;
      stopBtn.disabled  = true;
      textarea.disabled = false;
      sendBtn.disabled  = false;
      textarea.focus();
    }
  }

  /* ────────────────────────────────────────────────────────────────────────
   * API pubblica: window.CaduceoAI
   * ──────────────────────────────────────────────────────────────────────── */

  window.CaduceoAI = {
    /**
     * init(container) — Prima chiamata. Costruisce l'UI nel container fornito.
     * @param {HTMLElement} container - Elemento DOM in cui iniettare il modulo.
     */
    init(container) {
      if (!container) {
        console.error('[CaduceoAI] init: container non valido.');
        return;
      }
      // Reset stato a ogni init
      _state = {
        agents:        [],
        selectedAgents: new Set(),
        messages:      [],
        running:       false,
        abortFlag:     false,
        configVisible: true,
      };
      buildUI(container);
    },

    /**
     * render(container, ctx) — Chiamata da App.registerView per ri-renderizzare la view.
     * Se il container è già inizializzato, esegue solo un aggiornamento leggero.
     * @param {HTMLElement} container
     * @param {Object}      ctx - Contesto fornito dal router (non utilizzato attualmente).
     */
    render(container, ctx) {
      if (!container) return;
      // Se il modulo non è ancora stato inizializzato in questo container, lo inizializza
      if (!container.querySelector('.cai-root')) {
        this.init(container);
      }
      // Aggiorna comunque la lista agenti per avere info fresche
      const listEl = container.querySelector('.cai-agents-list');
      if (listEl) {
        loadAgents(listEl);
      }
    },
  };

})();
