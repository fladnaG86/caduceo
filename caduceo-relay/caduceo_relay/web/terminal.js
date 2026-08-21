/**
 * terminal.js — Modulo frontend per il Terminale Remoto di Caduceo
 *
 * Espone window.CaduceoTerminal = { open(agentId, container), renderCommand(agentId) }
 *
 * Funzionalità:
 *   - Mostra l'agent_id corrente nella barra del terminale
 *   - Campo di input stile command-line con invio tramite Enter
 *   - Cronologia comandi navigabile con freccia-su / freccia-giù
 *   - Area di output monospaziata e scrollabile (textContent per sicurezza XSS)
 *   - Ogni blocco di output mostra: prompt [user@agent], comando, stdout/stderr colorati, exit code
 *   - Supporto agenti offline (risposta {status:'queued'}) con messaggio dedicato
 *   - Pulsante per pulire la console
 *   - Gestione errori HTTP con messaggi in italiano
 *   - Stile scoped iniettato nel container, compatibile con le variabili CSS del tema scuro
 *
 * Dipendenze globali:
 *   - window.App.api(path, opts)  — fetch autenticato con JWT (definito in app.js)
 *   - window.App.currentAgent     — agent_id attualmente selezionato
 *
 * Endpoint utilizzato:
 *   POST /api/agents/{agentId}/command
 *   Body:    { command: string, timeout: 30, shell: '' }
 *   Rispost: { exit_code, stdout, stderr, duration_ms }
 *          | { status: 'queued' }   (agente offline)
 *
 * Autore: generato per la piattaforma Caduceo
 */

(function (global) {
  'use strict';

  /* ───────────────────────────── CSS scoped ───────────────────────────── */

  const SCOPED_CSS = `
.cdt-terminal {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 320px;
  background: var(--bg, #0f172a);
  border: 1px solid var(--border, #334155);
  border-radius: 8px;
  overflow: hidden;
  font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
}

.cdt-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  background: var(--card, #1e293b);
  border-bottom: 1px solid var(--border, #334155);
  gap: 8px;
}

.cdt-agent-label {
  font-size: 0.78rem;
  color: var(--muted, #94a3b8);
  letter-spacing: 0.04em;
}

.cdt-agent-id {
  font-size: 0.85rem;
  color: var(--accent, #4f46e5);
  font-weight: 600;
  word-break: break-all;
}

.cdt-btn-clear {
  padding: 3px 10px;
  font-size: 0.75rem;
  background: transparent;
  border: 1px solid var(--border, #334155);
  border-radius: 4px;
  color: var(--muted, #94a3b8);
  cursor: pointer;
  white-space: nowrap;
  transition: border-color 0.15s, color 0.15s;
}

.cdt-btn-clear:hover {
  border-color: var(--danger, #ef4444);
  color: var(--danger, #ef4444);
}

.cdt-output {
  flex: 1;
  overflow-y: auto;
  padding: 12px 14px;
  background: var(--bg, #0f172a);
  color: var(--text, #e2e8f0);
  font-size: 0.82rem;
  line-height: 1.55;
  scroll-behavior: smooth;
}

.cdt-output:empty::before {
  content: 'Nessun output. Digita un comando e premi Invio.';
  color: var(--muted, #94a3b8);
  font-style: italic;
}

.cdt-block {
  margin-bottom: 16px;
  border-left: 2px solid var(--border, #334155);
  padding-left: 10px;
}

.cdt-prompt {
  color: var(--accent, #4f46e5);
  font-weight: 700;
  user-select: none;
}

.cdt-cmd-text {
  color: var(--text, #e2e8f0);
  margin-left: 6px;
  word-break: break-all;
}

.cdt-section-label {
  font-size: 0.70rem;
  color: var(--muted, #94a3b8);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-top: 4px;
}

.cdt-stdout {
  color: var(--text, #e2e8f0);
  white-space: pre-wrap;
  word-break: break-word;
}

.cdt-stderr {
  color: var(--danger, #ef4444);
  white-space: pre-wrap;
  word-break: break-word;
}

.cdt-exit-ok {
  color: var(--success, #22c55e);
  font-size: 0.75rem;
  margin-top: 4px;
}

.cdt-exit-err {
  color: var(--danger, #ef4444);
  font-size: 0.75rem;
  margin-top: 4px;
}

.cdt-queued {
  color: var(--muted, #94a3b8);
  font-style: italic;
  margin-top: 4px;
}

.cdt-error-msg {
  color: var(--danger, #ef4444);
  font-style: italic;
  margin-top: 4px;
}

.cdt-duration {
  font-size: 0.70rem;
  color: var(--muted, #94a3b8);
  margin-left: 8px;
}

.cdt-input-row {
  display: flex;
  align-items: center;
  padding: 8px 14px;
  background: var(--card, #1e293b);
  border-top: 1px solid var(--border, #334155);
  gap: 8px;
}

.cdt-input-prompt {
  color: var(--accent, #4f46e5);
  font-weight: 700;
  font-size: 0.88rem;
  user-select: none;
  white-space: nowrap;
}

.cdt-input {
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  color: var(--text, #e2e8f0);
  font-family: inherit;
  font-size: 0.88rem;
  caret-color: var(--accent, #4f46e5);
}

.cdt-input::placeholder {
  color: var(--muted, #94a3b8);
  font-style: italic;
}

.cdt-btn-send {
  padding: 4px 12px;
  font-size: 0.80rem;
  background: var(--accent, #4f46e5);
  border: none;
  border-radius: 4px;
  color: #fff;
  cursor: pointer;
  white-space: nowrap;
  transition: opacity 0.15s;
}

.cdt-btn-send:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.cdt-btn-send:not(:disabled):hover {
  opacity: 0.85;
}

.cdt-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid var(--muted, #94a3b8);
  border-top-color: var(--accent, #4f46e5);
  border-radius: 50%;
  animation: cdt-spin 0.7s linear infinite;
  vertical-align: middle;
  margin-right: 6px;
}

@keyframes cdt-spin {
  to { transform: rotate(360deg); }
}
`;

  /* ──────────────────────────── Stato interno ─────────────────────────── */

  // Mappa agentId → { outputEl, inputEl, sendBtn, agentIdEl, history, histIdx, busy }
  const _state = new Map();

  /* ─────────────────────────── Utility DOM ──────────────────────────── */

  function _el(tag, cls, attrs) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (attrs) Object.assign(e, attrs);
    return e;
  }

  function _injectStyle(container) {
    if (container.querySelector('style[data-cdt]')) return;
    const s = document.createElement('style');
    s.setAttribute('data-cdt', '1');
    s.textContent = SCOPED_CSS;
    container.insertBefore(s, container.firstChild);
  }

  function _scrollBottom(el) {
    el.scrollTop = el.scrollHeight;
  }

  /* ─────────────────────── Costruzione UI ─────────────────────────── */

  function _buildUI(agentId, container) {
    _injectStyle(container);
    container.innerHTML = '';
    // Ri-inietta lo style dopo innerHTML = ''
    _injectStyle(container);

    const wrap = _el('div', 'cdt-terminal');

    /* Header */
    const header = _el('div', 'cdt-header');
    const labelWrap = _el('div');
    const lbl = _el('span', 'cdt-agent-label');
    lbl.textContent = 'Agente: ';
    const agentIdEl = _el('span', 'cdt-agent-id');
    agentIdEl.textContent = agentId || '—';
    labelWrap.appendChild(lbl);
    labelWrap.appendChild(agentIdEl);

    const clearBtn = _el('button', 'cdt-btn-clear');
    clearBtn.textContent = 'Pulisci';
    clearBtn.type = 'button';
    clearBtn.title = 'Pulisci la console';

    header.appendChild(labelWrap);
    header.appendChild(clearBtn);

    /* Output */
    const outputEl = _el('div', 'cdt-output');
    outputEl.setAttribute('role', 'log');
    outputEl.setAttribute('aria-live', 'polite');

    /* Input row */
    const inputRow = _el('div', 'cdt-input-row');
    const promptSpan = _el('span', 'cdt-input-prompt');
    promptSpan.textContent = '$';

    const inputEl = _el('input', 'cdt-input', {
      type: 'text',
      placeholder: 'Digita un comando…',
      autocomplete: 'off',
      spellcheck: false,
    });

    const sendBtn = _el('button', 'cdt-btn-send', { type: 'button' });
    sendBtn.textContent = 'Invia';

    inputRow.appendChild(promptSpan);
    inputRow.appendChild(inputEl);
    inputRow.appendChild(sendBtn);

    wrap.appendChild(header);
    wrap.appendChild(outputEl);
    wrap.appendChild(inputRow);
    container.appendChild(wrap);

    /* Stato locale */
    const st = { outputEl, inputEl, sendBtn, agentIdEl, clearBtn, history: [], histIdx: -1, busy: false };
    _state.set(agentId, st);

    /* Event: Pulisci */
    clearBtn.addEventListener('click', () => {
      outputEl.innerHTML = '';
    });

    /* Event: Invia */
    function _submitCommand() {
      const cmd = inputEl.value.trim();
      if (!cmd || st.busy) return;
      _execCommand(agentId, cmd, st);
    }

    sendBtn.addEventListener('click', _submitCommand);

    inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        _submitCommand();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (st.history.length === 0) return;
        if (st.histIdx === -1) st.histIdx = st.history.length - 1;
        else if (st.histIdx > 0) st.histIdx--;
        inputEl.value = st.history[st.histIdx];
        // Porta il cursore alla fine
        setTimeout(() => inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length), 0);
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (st.histIdx === -1) return;
        if (st.histIdx < st.history.length - 1) {
          st.histIdx++;
          inputEl.value = st.history[st.histIdx];
        } else {
          st.histIdx = -1;
          inputEl.value = '';
        }
      }
    });

    inputEl.focus();
    return st;
  }

  /* ──────────────────── Esecuzione comando remoto ─────────────────── */

  async function _execCommand(agentId, cmd, st) {
    st.busy = true;
    st.sendBtn.disabled = true;
    st.inputEl.disabled = true;

    // Aggiorna cronologia
    if (st.history[st.history.length - 1] !== cmd) {
      st.history.push(cmd);
      if (st.history.length > 200) st.history.shift();
    }
    st.histIdx = -1;
    st.inputEl.value = '';

    /* Blocco output: riga di prompt + comando */
    const block = _el('div', 'cdt-block');

    const promptLine = _el('div');
    const promptSpan = _el('span', 'cdt-prompt');
    promptSpan.textContent = '[operatore@' + agentId + ']';
    const cmdSpan = _el('span', 'cdt-cmd-text');
    cmdSpan.textContent = '$ ' + cmd;
    promptLine.appendChild(promptSpan);
    promptLine.appendChild(cmdSpan);
    block.appendChild(promptLine);

    /* Spinner di attesa */
    const waitLine = _el('div');
    const spinner = _el('span', 'cdt-spinner');
    const waitTxt = _el('span', 'cdt-queued');
    waitTxt.textContent = 'In esecuzione…';
    waitLine.appendChild(spinner);
    waitLine.appendChild(waitTxt);
    block.appendChild(waitLine);

    st.outputEl.appendChild(block);
    _scrollBottom(st.outputEl);

    try {
      const body = JSON.stringify({ command: cmd, timeout: 30, shell: '' });
      const resp = await global.App.api('/api/agents/' + encodeURIComponent(agentId) + '/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
      });

      // Rimuovi spinner
      block.removeChild(waitLine);

      /* Agente offline → risposta queued */
      if (resp && resp.status === 'queued') {
        const qLine = _el('div', 'cdt-queued');
        qLine.textContent = '⏳ Comando messo in coda: l\'agente è attualmente offline. Verrà eseguito alla prossima connessione.';
        block.appendChild(qLine);
      } else {
        /* Risposta normale */
        const { exit_code, stdout, stderr, duration_ms } = resp || {};

        // stdout
        if (stdout && stdout.length > 0) {
          const sLabel = _el('div', 'cdt-section-label');
          sLabel.textContent = 'stdout';
          const sOut = _el('pre', 'cdt-stdout');
          sOut.textContent = stdout; // textContent per sicurezza XSS
          block.appendChild(sLabel);
          block.appendChild(sOut);
        }

        // stderr
        if (stderr && stderr.length > 0) {
          const eLabel = _el('div', 'cdt-section-label');
          eLabel.textContent = 'stderr';
          const sErr = _el('pre', 'cdt-stderr');
          sErr.textContent = stderr; // textContent per sicurezza XSS
          block.appendChild(eLabel);
          block.appendChild(sErr);
        }

        // exit code + durata
        const footLine = _el('div');
        const exitEl = _el('span', exit_code === 0 ? 'cdt-exit-ok' : 'cdt-exit-err');
        exitEl.textContent = exit_code === 0
          ? '✔ Completato (exit 0)'
          : '✖ Uscito con codice ' + exit_code;
        footLine.appendChild(exitEl);

        if (duration_ms !== undefined && duration_ms !== null) {
          const dur = _el('span', 'cdt-duration');
          dur.textContent = '(' + duration_ms + ' ms)';
          footLine.appendChild(dur);
        }
        block.appendChild(footLine);
      }
    } catch (err) {
      // Rimuovi spinner se ancora presente
      if (waitLine.parentNode === block) block.removeChild(waitLine);

      const errLine = _el('div', 'cdt-error-msg');
      let msg;
      if (err && err.status) {
        switch (err.status) {
          case 400: msg = 'Errore 400: Richiesta non valida. Controlla il comando.'; break;
          case 401: msg = 'Errore 401: Non autorizzato. Effettua nuovamente il login.'; break;
          case 403: msg = 'Errore 403: Accesso negato.'; break;
          case 404: msg = 'Errore 404: Agente non trovato.'; break;
          case 408:
          case 504: msg = 'Errore ' + err.status + ': Timeout del comando. L\'agente non ha risposto in tempo.'; break;
          case 500: msg = 'Errore 500: Errore interno del server.'; break;
          case 502: msg = 'Errore 502: Gateway non raggiungibile.'; break;
          case 503: msg = 'Errore 503: Servizio temporaneamente non disponibile.'; break;
          default: msg = 'Errore HTTP ' + err.status + ': ' + (err.statusText || 'sconosciuto') + '.';
        }
      } else if (err instanceof TypeError) {
        msg = 'Errore di rete: impossibile raggiungere il relay. Verifica la connessione.';
      } else {
        msg = 'Errore imprevisto: ' + (err && err.message ? err.message : String(err));
      }
      errLine.textContent = '⚠ ' + msg;
      block.appendChild(errLine);
    } finally {
      st.busy = false;
      st.sendBtn.disabled = false;
      st.inputEl.disabled = false;
      _scrollBottom(st.outputEl);
      st.inputEl.focus();
    }
  }

  /* ──────────────────────── API pubblica ─────────────────────────── */

  /**
   * open(agentId, container)
   * Costruisce e monta il terminale nel container fornito.
   * Sostituisce qualsiasi contenuto precedente.
   *
   * @param {string} agentId    — ID dell'agente target
   * @param {HTMLElement} container — Elemento DOM in cui montare il terminale
   */
  function open(agentId, container) {
    if (!agentId || !container) {
      console.warn('[CaduceoTerminal] open(): agentId e container sono obbligatori.');
      return;
    }
    _buildUI(agentId, container);
  }

  /**
   * renderCommand(agentId)
   * Aggiorna l'intestazione del terminale già montato con il nuovo agentId.
   * Utile quando l'utente cambia agente senza rimontare l'intera vista.
   *
   * @param {string} agentId — Nuovo ID agente da visualizzare
   */
  function renderCommand(agentId) {
    const st = _state.get(agentId);
    if (st) {
      st.agentIdEl.textContent = agentId;
      st.inputEl.focus();
    }
  }

  /* ─────────────────── Esposizione globale ──────────────────────── */

  global.CaduceoTerminal = { open, renderCommand };

})(window);
