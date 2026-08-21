/**
 * audit.js — Modulo Log di Audit per la console di gestione Caduceo.
 *
 * Mostra una tabella paginata e filtrabile di tutte le operazioni eseguite
 * dagli operatori sugli agenti remoti. I dati vengono recuperati tramite
 * window.App.api() e aggiornati automaticamente ogni 5 secondi.
 *
 * Espone: window.CaduceoAudit = { init(container), render(container, filter) }
 *
 * Dipendenze: window.App.api (definita in app.js)
 * Tema: variabili CSS da style.css (--card, --border, --text, --muted,
 *       --accent, --success, --danger, --warning)
 */

(function () {
  'use strict';

  /* ------------------------------------------------------------------ */
  /* Costanti                                                             */
  /* ------------------------------------------------------------------ */

  const REFRESH_INTERVAL_MS = 5000;
  const DEFAULT_LIMIT = 100;

  /** Mappa azione → { label italiano, colore badge } */
  const ACTION_META = {
    command:       { label: 'Comando',         color: '#4f46e5' }, // blu accent
    screenshot:    { label: 'Screenshot',      color: '#7c3aed' }, // viola
    file_upload:   { label: 'Upload file',     color: '#16a34a' }, // verde
    file_download: { label: 'Download file',   color: '#22c55e' }, // verde chiaro
    stream_start:  { label: 'Streaming avvio', color: '#f59e0b' }, // arancio warning
    stream_stop:   { label: 'Streaming stop',  color: '#d97706' }, // arancio scuro
    input:         { label: 'Input remoto',    color: '#64748b' }, // grigio
    agent_error:   { label: 'Errore',          color: '#ef4444' }, // rosso danger
    agent_register:   { label: 'Agente registrato',   color: '#64748b' }, // grigio
    agent_disconnect: { label: 'Agente disconnesso',  color: '#64748b' }, // grigio
    agent_connect:    { label: 'Agente connesso',     color: '#22c55e' }, // verde
  };

  /* ------------------------------------------------------------------ */
  /* Stato del modulo                                                     */
  /* ------------------------------------------------------------------ */

  let _container = null;
  let _timer = null;
  let _filter = { action: '', agent_id: '', limit: DEFAULT_LIMIT };
  let _tbodyEl = null;
  let _countEl = null;
  let _errorEl = null;

  /* ------------------------------------------------------------------ */
  /* CSS locale (scoped nel container, non modifica style.css)           */
  /* ------------------------------------------------------------------ */

  const SCOPED_CSS = `
.ca-audit-wrap {
  font-family: inherit;
  color: var(--text, #e2e8f0);
}
.ca-audit-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin-bottom: 1.25rem;
}
.ca-audit-title {
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--text, #e2e8f0);
  margin: 0;
}
.ca-audit-count {
  font-size: 0.82rem;
  color: var(--muted, #94a3b8);
}
.ca-audit-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  margin-bottom: 1rem;
  padding: 0.85rem 1rem;
  background: var(--card, #1e293b);
  border: 1px solid var(--border, #334155);
  border-radius: 0.5rem;
}
.ca-audit-filters label {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.78rem;
  color: var(--muted, #94a3b8);
  min-width: 140px;
}
.ca-audit-filters select,
.ca-audit-filters input[type="text"],
.ca-audit-filters input[type="number"] {
  background: var(--bg, #0f172a);
  border: 1px solid var(--border, #334155);
  border-radius: 0.375rem;
  color: var(--text, #e2e8f0);
  padding: 0.3rem 0.5rem;
  font-size: 0.85rem;
}
.ca-audit-filters select:focus,
.ca-audit-filters input:focus {
  outline: 2px solid var(--accent, #4f46e5);
  outline-offset: 1px;
}
.ca-audit-btn {
  align-self: flex-end;
  padding: 0.35rem 0.9rem;
  border: none;
  border-radius: 0.375rem;
  background: var(--accent, #4f46e5);
  color: #fff;
  font-size: 0.85rem;
  cursor: pointer;
  transition: opacity 0.15s;
}
.ca-audit-btn:hover { opacity: 0.85; }
.ca-audit-error {
  display: none;
  padding: 0.75rem 1rem;
  margin-bottom: 0.75rem;
  background: rgba(239,68,68,0.12);
  border: 1px solid var(--danger, #ef4444);
  border-radius: 0.5rem;
  color: var(--danger, #ef4444);
  font-size: 0.88rem;
}
.ca-audit-error.visible { display: block; }
.ca-audit-table-wrap {
  overflow-x: auto;
  border: 1px solid var(--border, #334155);
  border-radius: 0.5rem;
}
.ca-audit-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.84rem;
}
.ca-audit-table thead th {
  text-align: left;
  padding: 0.65rem 0.85rem;
  background: var(--card, #1e293b);
  color: var(--muted, #94a3b8);
  font-weight: 600;
  border-bottom: 1px solid var(--border, #334155);
  white-space: nowrap;
}
.ca-audit-table tbody tr {
  border-bottom: 1px solid var(--border, #334155);
  transition: background 0.1s;
}
.ca-audit-table tbody tr:last-child { border-bottom: none; }
.ca-audit-table tbody tr:hover {
  background: rgba(255,255,255,0.03);
}
.ca-audit-table td {
  padding: 0.55rem 0.85rem;
  vertical-align: top;
  color: var(--text, #e2e8f0);
}
.ca-audit-table td.ca-muted { color: var(--muted, #94a3b8); }
.ca-badge {
  display: inline-block;
  padding: 0.18rem 0.55rem;
  border-radius: 0.25rem;
  font-size: 0.75rem;
  font-weight: 600;
  color: #fff;
  white-space: nowrap;
}
.ca-empty {
  text-align: center;
  padding: 2rem;
  color: var(--muted, #94a3b8);
  font-size: 0.9rem;
}
.ca-refresh-indicator {
  font-size: 0.75rem;
  color: var(--muted, #94a3b8);
  text-align: right;
  margin-top: 0.5rem;
}
`;

  /* ------------------------------------------------------------------ */
  /* Utilità                                                              */
  /* ------------------------------------------------------------------ */

  /**
   * Formatta un timestamp in secondi epoch come stringa leggibile in italiano.
   * Esempio: "20/08/2026, 14:33"
   */
  function formatTimestamp(epochSeconds) {
    if (!epochSeconds && epochSeconds !== 0) return '—';
    const d = new Date(epochSeconds * 1000);
    return d.toLocaleString('it-IT', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  }

  /** Costruisce la query string per GET /api/audit */
  function buildQueryString(filter) {
    const params = new URLSearchParams();
    if (filter.action)   params.set('action',   filter.action);
    if (filter.agent_id) params.set('agent_id', filter.agent_id.trim());
    if (filter.limit)    params.set('limit',    String(filter.limit));
    const qs = params.toString();
    return qs ? '?' + qs : '';
  }

  /* ------------------------------------------------------------------ */
  /* Rendering DOM                                                        */
  /* ------------------------------------------------------------------ */

  /** Inietta il CSS scoped nel container (una sola volta). */
  function injectStyle(container) {
    if (container.querySelector('style[data-ca-audit]')) return;
    const styleEl = document.createElement('style');
    styleEl.setAttribute('data-ca-audit', '1');
    styleEl.textContent = SCOPED_CSS;
    container.appendChild(styleEl);
  }

  /** Crea la struttura HTML statica (filtri + tabella) nel container. */
  function buildSkeleton(container) {
    injectStyle(container);

    const wrap = document.createElement('div');
    wrap.className = 'ca-audit-wrap';

    /* Intestazione */
    const header = document.createElement('div');
    header.className = 'ca-audit-header';

    const title = document.createElement('h2');
    title.className = 'ca-audit-title';
    title.textContent = 'Log di Audit';

    const count = document.createElement('span');
    count.className = 'ca-audit-count';
    count.textContent = '';
    _countEl = count;

    header.appendChild(title);
    header.appendChild(count);
    wrap.appendChild(header);

    /* Barra filtri */
    const filters = document.createElement('div');
    filters.className = 'ca-audit-filters';

    /* Filtro: Azione */
    const lblAction = document.createElement('label');
    const lblActionText = document.createTextNode('Azione');
    lblAction.appendChild(lblActionText);

    const selAction = document.createElement('select');
    selAction.id = 'ca-filter-action';

    const optAll = document.createElement('option');
    optAll.value = '';
    optAll.textContent = 'Tutte le azioni';
    selAction.appendChild(optAll);

    Object.entries(ACTION_META).forEach(([val, meta]) => {
      const opt = document.createElement('option');
      opt.value = val;
      opt.textContent = meta.label;
      if (val === _filter.action) opt.selected = true;
      selAction.appendChild(opt);
    });

    selAction.addEventListener('change', function () {
      _filter.action = this.value;
    });

    lblAction.appendChild(selAction);
    filters.appendChild(lblAction);

    /* Filtro: Agent ID */
    const lblAgent = document.createElement('label');
    lblAgent.appendChild(document.createTextNode('ID Agente'));

    const inpAgent = document.createElement('input');
    inpAgent.type = 'text';
    inpAgent.id = 'ca-filter-agent';
    inpAgent.placeholder = 'es. agent-001';
    inpAgent.value = _filter.agent_id;
    inpAgent.addEventListener('input', function () {
      _filter.agent_id = this.value;
    });

    lblAgent.appendChild(inpAgent);
    filters.appendChild(lblAgent);

    /* Filtro: Limit */
    const lblLimit = document.createElement('label');
    lblLimit.appendChild(document.createTextNode('Num. righe'));

    const inpLimit = document.createElement('input');
    inpLimit.type = 'number';
    inpLimit.id = 'ca-filter-limit';
    inpLimit.min = '1';
    inpLimit.max = '1000';
    inpLimit.value = String(_filter.limit);
    inpLimit.style.width = '80px';
    inpLimit.addEventListener('input', function () {
      const v = parseInt(this.value, 10);
      _filter.limit = (v > 0) ? v : DEFAULT_LIMIT;
    });

    lblLimit.appendChild(inpLimit);
    filters.appendChild(lblLimit);

    /* Pulsante Aggiorna */
    const btnRefresh = document.createElement('button');
    btnRefresh.className = 'ca-audit-btn';
    btnRefresh.textContent = '🔄 Aggiorna';
    btnRefresh.addEventListener('click', function () {
      loadData();
    });
    filters.appendChild(btnRefresh);

    wrap.appendChild(filters);

    /* Messaggio errore */
    const errDiv = document.createElement('div');
    errDiv.className = 'ca-audit-error';
    errDiv.setAttribute('role', 'alert');
    _errorEl = errDiv;
    wrap.appendChild(errDiv);

    /* Tabella */
    const tableWrap = document.createElement('div');
    tableWrap.className = 'ca-audit-table-wrap';

    const table = document.createElement('table');
    table.className = 'ca-audit-table';

    const thead = document.createElement('thead');
    const headerRow = document.createElement('tr');
    const columns = ['Data/Ora', 'Agente', 'Azione', 'Soggetto/File', 'IP Sorgente', 'Dettaglio'];
    columns.forEach(function (col) {
      const th = document.createElement('th');
      th.textContent = col;
      headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    _tbodyEl = tbody;
    table.appendChild(tbody);

    tableWrap.appendChild(table);
    wrap.appendChild(tableWrap);

    /* Indicatore refresh automatico */
    const refreshInfo = document.createElement('div');
    refreshInfo.className = 'ca-refresh-indicator';
    refreshInfo.textContent = 'Aggiornamento automatico ogni 5 secondi';
    wrap.appendChild(refreshInfo);

    container.appendChild(wrap);
  }

  /** Mostra un messaggio di errore in italiano. */
  function showError(msg) {
    if (!_errorEl) return;
    _errorEl.textContent = '⚠ ' + msg;
    _errorEl.classList.add('visible');
  }

  /** Nasconde il messaggio di errore. */
  function hideError() {
    if (!_errorEl) return;
    _errorEl.textContent = '';
    _errorEl.classList.remove('visible');
  }

  /** Costruisce un badge colorato per l'azione. */
  function createBadge(action) {
    const meta = ACTION_META[action] || { label: action, color: '#64748b' };
    const badge = document.createElement('span');
    badge.className = 'ca-badge';
    badge.style.backgroundColor = meta.color;
    badge.textContent = meta.label;
    return badge;
  }

  /** Popola il tbody con i record (dal più recente). */
  function populateTable(records) {
    if (!_tbodyEl) return;

    /* Svuota le righe esistenti senza innerHTML */
    while (_tbodyEl.firstChild) {
      _tbodyEl.removeChild(_tbodyEl.firstChild);
    }

    if (!records || records.length === 0) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 6;
      td.className = 'ca-empty';
      td.textContent = 'Nessun record trovato.';
      tr.appendChild(td);
      _tbodyEl.appendChild(tr);
      return;
    }

    /* Ordina dalla più recente (timestamp decrescente) */
    const sorted = records.slice().sort(function (a, b) {
      return (b.timestamp || 0) - (a.timestamp || 0);
    });

    sorted.forEach(function (rec) {
      const tr = document.createElement('tr');

      /* Data/Ora */
      const tdTime = document.createElement('td');
      tdTime.className = 'ca-muted';
      tdTime.textContent = formatTimestamp(rec.timestamp);
      tr.appendChild(tdTime);

      /* Agente */
      const tdAgent = document.createElement('td');
      tdAgent.textContent = rec.agent_id || '—';
      tr.appendChild(tdAgent);

      /* Azione badge */
      const tdAction = document.createElement('td');
      tdAction.appendChild(createBadge(rec.action));
      tr.appendChild(tdAction);

      /* Soggetto/File */
      const tdSubject = document.createElement('td');
      tdSubject.textContent = rec.subject || '—';
      tr.appendChild(tdSubject);

      /* IP Sorgente */
      const tdIp = document.createElement('td');
      tdIp.className = 'ca-muted';
      tdIp.textContent = rec.source_ip || '—';
      tr.appendChild(tdIp);

      /* Dettaglio */
      const tdDetail = document.createElement('td');
      tdDetail.className = 'ca-muted';
      tdDetail.textContent = rec.detail || '—';
      tr.appendChild(tdDetail);

      _tbodyEl.appendChild(tr);
    });
  }

  /* ------------------------------------------------------------------ */
  /* Caricamento dati                                                     */
  /* ------------------------------------------------------------------ */

  function loadData() {
    if (!window.App || typeof window.App.api !== 'function') {
      showError('window.App.api non disponibile. Impossibile caricare i dati.');
      return;
    }

    const qs = buildQueryString(_filter);
    window.App.api('/api/audit' + qs)
      .then(function (data) {
        hideError();
        const records = (data && Array.isArray(data.records)) ? data.records : [];
        const total = (data && typeof data.count === 'number') ? data.count : records.length;

        if (_countEl) {
          _countEl.textContent = total + ' record totali';
        }

        populateTable(records);
      })
      .catch(function (err) {
        const msg = (err && err.message) ? err.message : 'Errore sconosciuto';
        showError('Errore nel caricamento del log di audit: ' + msg);
      });
  }

  /* ------------------------------------------------------------------ */
  /* Auto-refresh                                                         */
  /* ------------------------------------------------------------------ */

  function startAutoRefresh() {
    stopAutoRefresh();
    _timer = setInterval(function () {
      loadData();
    }, REFRESH_INTERVAL_MS);
  }

  function stopAutoRefresh() {
    if (_timer !== null) {
      clearInterval(_timer);
      _timer = null;
    }
  }

  /* ------------------------------------------------------------------ */
  /* API pubblica                                                          */
  /* ------------------------------------------------------------------ */

  /**
   * init(container)
   * Inizializza il modulo nel container fornito: costruisce la UI,
   * carica i dati iniziali e avvia il refresh automatico.
   *
   * @param {HTMLElement} container
   */
  function init(container) {
    if (!container) {
      console.error('[CaduceoAudit] init: container non valido.');
      return;
    }

    /* Ferma eventuale timer precedente */
    stopAutoRefresh();

    /* Pulisce il container */
    while (container.firstChild) {
      container.removeChild(container.firstChild);
    }

    _container = container;
    _filter = { action: '', agent_id: '', limit: DEFAULT_LIMIT };
    _tbodyEl = null;
    _countEl = null;
    _errorEl = null;

    buildSkeleton(container);
    loadData();
    startAutoRefresh();
  }

  /**
   * render(container, filter)
   * Ri-renderizza il modulo applicando un filtro esterno.
   *
   * @param {HTMLElement} container
   * @param {Object} [filter] - { action, agent_id, limit }
   */
  function render(container, filter) {
    if (!container) {
      console.error('[CaduceoAudit] render: container non valido.');
      return;
    }

    if (container !== _container) {
      init(container);
    }

    if (filter && typeof filter === 'object') {
      if (filter.action    !== undefined) _filter.action    = filter.action;
      if (filter.agent_id  !== undefined) _filter.agent_id  = filter.agent_id;
      if (filter.limit     !== undefined) _filter.limit     = filter.limit;

      /* Sincronizza i controlli UI con il filtro esterno */
      const selAction = container.querySelector('#ca-filter-action');
      if (selAction) selAction.value = _filter.action;

      const inpAgent = container.querySelector('#ca-filter-agent');
      if (inpAgent) inpAgent.value = _filter.agent_id;

      const inpLimit = container.querySelector('#ca-filter-limit');
      if (inpLimit) inpLimit.value = String(_filter.limit);
    }

    loadData();
  }

  /* ------------------------------------------------------------------ */
  /* Esposizione globale                                                  */
  /* ------------------------------------------------------------------ */

  window.CaduceoAudit = {
    init: init,
    render: render,
  };

})();
