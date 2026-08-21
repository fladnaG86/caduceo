// Dashboard principale di Caduceo: mostra riepilogo agenti (online/offline/totali),
// metriche aggregate CPU/RAM/disco, ultime 5 attività dall'audit log, e griglia
// degli agenti con stato, hostname, OS e barre di carico. Usa solo JS puro + DOM.
// Dipende da window.App.api(path, opts) definita in app.js.

(function (global) {
  'use strict';

  // ─── Traduzione delle azioni audit ────────────────────────────────────────
  const AZIONI = {
    command:        'Comando',
    screenshot:     'Screenshot',
    file_upload:    'Upload file',
    file_download:  'Download file',
    stream_start:   'Streaming avviato',
    stream_stop:    'Streaming fermato',
    input:          'Input remoto',
    agent_error:    'Errore agente',
    agent_register: 'Agente registrato',
    agent_disconnect: 'Agente disconnesso',
    agent_connect:  'Agente connesso',
  };

  /**
   * Restituisce l'etichetta italiana di un'azione, o l'originale se sconosciuta.
   * @param {string} azione
   * @returns {string}
   */
  function traduciAzione(azione) {
    return AZIONI[azione] || azione || '—';
  }

  /**
   * Formatta un timestamp ISO in data/ora leggibile in italiano.
   * @param {string|null} ts
   * @returns {string}
   */
  function formatTimestamp(ts) {
    if (!ts) return '—';
    try {
      // timestamp in secondi epoch (< 1e12) → converti in millisecondi
      const n = Number(ts);
      const ms = (typeof n === 'number' && !isNaN(n) && n < 1e12) ? n * 1000 : n;
      return new Date(ms).toLocaleString('it-IT', {
        day:    '2-digit',
        month:  '2-digit',
        year:   'numeric',
        hour:   '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });
    } catch (_) {
      return ts;
    }
  }

  /**
   * Legge un valore numerico in modo sicuro (null/undefined → 0).
   * @param {*} val
   * @returns {number}
   */
  function safeNum(val) {
    const n = parseFloat(val);
    return isNaN(n) ? 0 : n;
  }

  // ─── Costruzione HTML delle card numeriche ─────────────────────────────────

  /**
   * Crea un elemento card con titolo e valore numerico.
   * @param {string} titolo
   * @param {string|number} valore
   * @param {string} [classeExtra]  classe aggiuntiva per colorare il valore
   * @returns {HTMLElement}
   */
  function creaCardNumerica(titolo, valore, classeExtra) {
    const card = document.createElement('div');
    card.className = 'card stat';

    const h = document.createElement('div');
    h.className = 'stat-label';
    h.textContent = titolo;

    const v = document.createElement('div');
    v.className = 'stat-value' + (classeExtra ? ' ' + classeExtra : '');
    v.textContent = valore;

    card.appendChild(h);
    card.appendChild(v);
    return card;
  }

  // ─── Barra di carico percentuale ──────────────────────────────────────────

  /**
   * Crea una barra di avanzamento con etichetta e percentuale.
   * @param {string} etichetta
   * @param {number} percentuale  valore 0-100
   * @returns {HTMLElement}
   */
  function creaBarra(etichetta, percentuale) {
    const pct = Math.min(100, Math.max(0, safeNum(percentuale)));
    const colore = pct >= 90 ? 'var(--danger)' : pct >= 70 ? 'var(--warning)' : 'var(--accent)';

    const wrapper = document.createElement('div');
    wrapper.className = 'meter-wrapper';
    wrapper.style.cssText = 'margin-bottom:6px;';

    const header = document.createElement('div');
    header.style.cssText = 'display:flex;justify-content:space-between;font-size:0.78em;color:var(--muted);margin-bottom:2px;';

    const lbl = document.createElement('span');
    lbl.textContent = etichetta;

    const val = document.createElement('span');
    val.textContent = pct.toFixed(1) + '%';

    header.appendChild(lbl);
    header.appendChild(val);

    const track = document.createElement('div');
    track.style.cssText =
      'background:var(--border);border-radius:4px;height:6px;overflow:hidden;';

    const fill = document.createElement('div');
    fill.style.cssText =
      `width:${pct}%;height:100%;background:${colore};border-radius:4px;` +
      'transition:width 0.4s ease;';

    track.appendChild(fill);
    wrapper.appendChild(header);
    wrapper.appendChild(track);
    return wrapper;
  }

  // ─── Sezione "Ultima attività" ─────────────────────────────────────────────

  /**
   * Costruisce la sezione con le ultime 5 voci di audit.
   * @param {Array} records  array di record audit
   * @returns {HTMLElement}
   */
  function creaSezioneAudit(records) {
    const sezione = document.createElement('div');
    sezione.className = 'dashboard-section';
    sezione.style.cssText = 'margin-bottom:28px;';

    const titolo = document.createElement('h3');
    titolo.className = 'section-title';
    titolo.textContent = 'Ultima attività';
    sezione.appendChild(titolo);

    const ultimi5 = (records || []).slice(0, 5);

    if (ultimi5.length === 0) {
      const vuoto = document.createElement('p');
      vuoto.style.cssText = 'color:var(--muted);font-size:0.9em;';
      vuoto.textContent = 'Nessuna attività registrata.';
      sezione.appendChild(vuoto);
      return sezione;
    }

    const lista = document.createElement('div');
    lista.className = 'audit-list';
    lista.style.cssText = 'display:flex;flex-direction:column;gap:6px;';

    ultimi5.forEach(function (rec) {
      const riga = document.createElement('div');
      riga.className = 'card audit-row';
      riga.style.cssText =
        'display:flex;align-items:baseline;gap:10px;padding:8px 12px;' +
        'font-size:0.85em;flex-wrap:wrap;';

      const ts = document.createElement('span');
      ts.style.cssText = 'color:var(--muted);white-space:nowrap;min-width:130px;';
      ts.textContent = formatTimestamp(rec.timestamp);

      const agente = document.createElement('span');
      agente.style.cssText = 'color:var(--accent);font-family:monospace;flex:0 0 auto;';
      agente.textContent = rec.agent_id ? rec.agent_id.slice(0, 12) + '…' : '—';
      agente.title = rec.agent_id || '';

      const azione = document.createElement('span');
      azione.style.cssText = 'font-weight:600;flex:0 0 auto;';
      azione.textContent = traduciAzione(rec.action);

      const dettaglio = document.createElement('span');
      dettaglio.style.cssText = 'color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1 1 0;';
      dettaglio.textContent = rec.detail || rec.subject || '';

      riga.appendChild(ts);
      riga.appendChild(agente);
      riga.appendChild(azione);
      riga.appendChild(dettaglio);
      lista.appendChild(riga);
    });

    sezione.appendChild(lista);
    return sezione;
  }

  /**
   * Estrae gli indirizzi IPv4 "reali" (non loopback) da un oggetto network.
   * @param {Object} network  struttura { interfaccia: { ipv4: [], ipv6: [] } }
   * @returns {string[]}
   */
  function estraiIpv4(network) {
    const ip = [];
    if (!network || typeof network !== 'object') return ip;
    Object.values(network).forEach(function (iface) {
      const v4 = (iface && Array.isArray(iface.ipv4)) ? iface.ipv4 : [];
      v4.forEach(function (addr) {
        if (!addr) return;
        if (addr === '127.0.0.1' || addr.startsWith('fe80') || addr.startsWith('::')) return;
        // escludi APIPA/link-local (169.254.x) — rumore da interfacce senza DHCP
        if (addr.startsWith('169.254.')) return;
        if (!ip.includes(addr)) ip.push(addr);
      });
    });
    return ip;
  }

  // ─── Griglia agenti ────────────────────────────────────────────────────────

  /**
   * Crea una card per un singolo agente.
   * @param {Object} agente
   * @param {string[]} [ips]  indirizzi IPv4 dell'agente
   * @returns {HTMLElement}
   */
  function creaCardAgente(agente, ips) {
    const card = document.createElement('div');
    card.className = 'agent-card card';
    card.style.cssText = 'padding:14px 16px;display:flex;flex-direction:column;gap:8px;';

    // Intestazione: hostname + badge stato
    const header = document.createElement('div');
    header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;gap:8px;';

    const hostname = document.createElement('div');
    hostname.className = 'agent-hostname';
    hostname.style.cssText = 'font-weight:700;font-size:0.95em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;';
    hostname.textContent = agente.hostname || agente.agent_id || '—';
    hostname.title = agente.agent_id || '';

    const badge = document.createElement('span');
    badge.className = 'badge ' + (agente.online ? 'online' : 'offline');
    badge.textContent = agente.online ? 'Online' : 'Offline';

    header.appendChild(hostname);
    header.appendChild(badge);
    card.appendChild(header);

    // Info OS / arch
    const info = document.createElement('div');
    info.style.cssText = 'font-size:0.78em;color:var(--muted);';
    const osTesto = [agente.os, agente.os_version, agente.arch]
      .filter(Boolean).join(' · ') || 'OS sconosciuto';
    info.textContent = osTesto;
    card.appendChild(info);

    // Indirizzi IP (se disponibili)
    if (Array.isArray(ips) && ips.length > 0) {
      const ipWrap = document.createElement('div');
      ipWrap.style.cssText =
        'display:flex;flex-wrap:wrap;gap:4px;font-size:0.78em;';
      ips.forEach(function (ip) {
        const chip = document.createElement('span');
        chip.style.cssText =
          'background:rgba(79,70,229,0.12);border:1px solid var(--border);' +
          'border-radius:4px;padding:1px 6px;font-family:monospace;color:var(--text);';
        chip.textContent = ip;
        chip.title = 'Indirizzo IP';
        ipWrap.appendChild(chip);
      });
      card.appendChild(ipWrap);
    } else if (agente.online) {
      const ipVuoto = document.createElement('div');
      ipVuoto.style.cssText = 'font-size:0.78em;color:var(--muted);';
      ipVuoto.textContent = 'IP non disponibile';
      card.appendChild(ipVuoto);
    }

    // Tags (se presenti)
    const tags = agente.tags;
    if (Array.isArray(tags) && tags.length > 0) {
      const tagWrap = document.createElement('div');
      tagWrap.style.cssText = 'display:flex;flex-wrap:wrap;gap:4px;';
      tags.forEach(function (t) {
        const chip = document.createElement('span');
        chip.style.cssText =
          'background:var(--border);border-radius:99px;padding:1px 8px;' +
          'font-size:0.72em;color:var(--muted);';
        chip.textContent = t;
        tagWrap.appendChild(chip);
      });
      card.appendChild(tagWrap);
    }

    // Barre di carico (solo se online e stats disponibili)
    const stats = agente.stats || {};
    const haStats =
      agente.online &&
      (stats.cpu_percent != null || stats.memory_percent != null || stats.disk_percent != null);

    if (haStats) {
      const barreWrap = document.createElement('div');
      barreWrap.style.cssText = 'margin-top:4px;';
      if (stats.cpu_percent != null)    barreWrap.appendChild(creaBarra('CPU',   stats.cpu_percent));
      if (stats.memory_percent != null) barreWrap.appendChild(creaBarra('RAM',   stats.memory_percent));
      if (stats.disk_percent != null)   barreWrap.appendChild(creaBarra('Disco', stats.disk_percent));
      card.appendChild(barreWrap);
    } else if (!agente.online) {
      const ultimoHb = document.createElement('div');
      ultimoHb.style.cssText = 'font-size:0.78em;color:var(--muted);';
      ultimoHb.textContent = agente.last_heartbeat
        ? 'Ultimo segnale: ' + formatTimestamp(agente.last_heartbeat)
        : 'Mai connesso';
      card.appendChild(ultimoHb);
    }

    return card;
  }

  /**
   * Costruisce la sezione griglia agenti.
   * @param {Array} agenti
   * @param {Object} [ipsMap]  mappa { agent_id: [ip,...] }
   * @returns {HTMLElement}
   */
  function creaSezioneAgenti(agenti, ipsMap) {
    const sezione = document.createElement('div');
    sezione.className = 'dashboard-section';

    const titolo = document.createElement('h3');
    titolo.className = 'section-title';
    titolo.textContent = 'Agenti';
    sezione.appendChild(titolo);

    if (!agenti || agenti.length === 0) {
      const vuoto = document.createElement('p');
      vuoto.style.cssText = 'color:var(--muted);font-size:0.9em;';
      vuoto.textContent = 'Nessun agente registrato.';
      sezione.appendChild(vuoto);
      return sezione;
    }

    const griglia = document.createElement('div');
    griglia.className = 'grid';
    // Griglia responsiva via CSS: 3 colonne su desktop, 1 su mobile
    griglia.style.cssText =
      'display:grid;' +
      'grid-template-columns:repeat(auto-fill,minmax(260px,1fr));' +
      'gap:14px;';

    // Agenti online prima, poi offline
    const ordinati = agenti.slice().sort(function (a, b) {
      return (b.online ? 1 : 0) - (a.online ? 1 : 0);
    });

    ordinati.forEach(function (agente) {
      const ips = (ipsMap && ipsMap[agente.agent_id]) || [];
      griglia.appendChild(creaCardAgente(agente, ips));
    });

    sezione.appendChild(griglia);
    return sezione;
  }

  // ─── Indicatore di caricamento ─────────────────────────────────────────────

  function creaSpinner() {
    const wrap = document.createElement('div');
    wrap.style.cssText =
      'display:flex;align-items:center;justify-content:center;' +
      'padding:40px;color:var(--muted);font-size:0.95em;';
    wrap.textContent = 'Caricamento…';
    return wrap;
  }

  function creaErrore(messaggio) {
    const wrap = document.createElement('div');
    wrap.style.cssText =
      'padding:16px;background:rgba(var(--danger-rgb,220,53,69),0.12);' +
      'border:1px solid var(--danger);border-radius:6px;color:var(--danger);' +
      'font-size:0.9em;';
    wrap.textContent = '⚠ ' + (messaggio || 'Errore nel caricamento dei dati.');
    return wrap;
  }

  // ─── Barra "Scarica client" ───────────────────────────────────────────────
  // Mostra un pulsante per scaricare il pacchetto client (caduceo-local.zip)
  // da installare su altri PC. Usa l'endpoint protetto da JWT.

  function creaBarraDownloadClient() {
    const barra = document.createElement('div');
    barra.className = 'card';
    barra.style.cssText =
      'display:flex;align-items:center;justify-content:space-between;' +
      'gap:14px;flex-wrap:wrap;margin-bottom:24px;padding:16px 18px;';

    const info = document.createElement('div');
    info.style.cssText = 'flex:1;min-width:200px;';
    const titolo = document.createElement('div');
    titolo.style.cssText = 'font-weight:600;font-size:0.95em;';
    titolo.textContent = '🖥️ Installa Caduceo su un nuovo PC';
    const sottotitolo = document.createElement('div');
    sottotitolo.style.cssText = 'color:var(--muted);font-size:0.82em;margin-top:2px;';
    sottotitolo.textContent =
      'Scarica il client e installalo sul PC da gestire. Il PC si collegherà automaticamente al relay.';
    info.appendChild(titolo);
    info.appendChild(sottotitolo);
    barra.appendChild(info);

    const btn = document.createElement('button');
    btn.className = 'primary';
    btn.textContent = '⬇️ Scarica client';
    btn.style.cssText = 'white-space:nowrap;';
    btn.addEventListener('click', function () {
      downloadClient(btn);
    });
    barra.appendChild(btn);

    return barra;
  }

  function downloadClient(btn) {
    const token = window.App && window.App.jwt ? window.App.jwt : '';
    if (!token) {
      if (window.App && window.App.toast) window.App.toast('Token mancante. Accedi di nuovo.', 'error');
      return;
    }
    const testoOriginale = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⬇️ Scaricamento…';
    fetch('/api/client/download', {
      method: 'GET',
      headers: { 'Authorization': 'Bearer ' + token },
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().catch(function () { return {}; })
            .then(function (err) { throw new Error(err.detail || ('Errore HTTP ' + res.status)); });
        }
        return res.blob();
      })
      .then(function (blob) {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'caduceo-local.zip';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(function () { URL.revokeObjectURL(url); }, 3000);
        if (window.App && window.App.toast) window.App.toast('Client scaricato ✓', 'success');
      })
      .catch(function (err) {
        if (window.App && window.App.toast) window.App.toast('Errore download: ' + err.message, 'error');
      })
      .finally(function () {
        btn.disabled = false;
        btn.textContent = testoOriginale;
      });
  }

  // ─── Funzione di render principale ────────────────────────────────────────

  /**
   * Recupera dati dagli endpoint e popola il container con la dashboard.
   * @param {HTMLElement} container
   * @returns {Promise<void>}
   */
  async function render(container) {
    if (!container) return;

    // Svuota e mostra spinner
    container.innerHTML = '';
    container.appendChild(creaSpinner());

    let agenti = [];
    let recordAudit = [];
    let errori = [];
    let ipsMap = {};   // { agent_id: [ip, ...] }

    // Chiamate parallele agli endpoint
    await Promise.all([
      window.App.api('/api/agents')
        .then(function (data) {
          agenti = (data && Array.isArray(data.agents)) ? data.agents : [];
        })
        .catch(function (err) {
          console.error('[CaduceoDashboard] /api/agents:', err);
          errori.push('Impossibile caricare gli agenti.');
        }),

      window.App.api('/api/audit')
        .then(function (data) {
          recordAudit = (data && Array.isArray(data.records)) ? data.records : [];
        })
        .catch(function (err) {
          console.error('[CaduceoDashboard] /api/audit:', err);
          errori.push('Impossibile caricare il registro attività.');
        }),
    ]);

    // Recupera gli indirizzi IP per ogni agente online (via /info).
    // Non bloccante: se fallisce, la card mostra "IP non disponibile".
    if (agenti.length > 0) {
      await Promise.all(agenti.filter(function (a) { return a.online; }).map(function (a) {
        return window.App.api('/api/agents/' + encodeURIComponent(a.agent_id) + '/info')
          .then(function (info) {
            if (info && info.network) {
              ipsMap[a.agent_id] = estraiIpv4(info.network);
            }
          })
          .catch(function (err) {
            console.error('[CaduceoDashboard] /info per ' + a.agent_id + ':', err);
          });
      }));
    }

    // Svuota (rimuove spinner)
    container.innerHTML = '';

    // Errori (non bloccanti: mostra comunque il resto)
    if (errori.length > 0) {
      errori.forEach(function (e) {
        container.appendChild(creaErrore(e));
      });
    }

    // ── 0. Barra "Scarica client" ───────────────────────────────────────────
    container.appendChild(creaBarraDownloadClient());

    // ── 1. Card numeriche ────────────────────────────────────────────────────
    const nOnline  = agenti.filter(function (a) { return a.online; }).length;
    const nOffline = agenti.length - nOnline;
    const nAzioni  = recordAudit.length;

    const rigaCard = document.createElement('div');
    rigaCard.style.cssText =
      'display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));' +
      'gap:14px;margin-bottom:28px;';

    rigaCard.appendChild(creaCardNumerica('Online',         nOnline,        'text-success'));
    rigaCard.appendChild(creaCardNumerica('Offline',        nOffline,       nOffline > 0 ? 'text-danger' : ''));
    rigaCard.appendChild(creaCardNumerica('Totale agenti',  agenti.length,  ''));
    rigaCard.appendChild(creaCardNumerica('Azioni recenti', nAzioni,        ''));

    container.appendChild(rigaCard);

    // ── 2. Ultima attività ────────────────────────────────────────────────────
    container.appendChild(creaSezioneAudit(recordAudit));

    // ── 3. Griglia agenti ────────────────────────────────────────────────────
    container.appendChild(creaSezioneAgenti(agenti, ipsMap));
  }

  // ─── Esposizione pubblica ──────────────────────────────────────────────────

  /**
   * Inizializza la dashboard nel container indicato.
   * Equivalente a render() — esposto per coerenza con l'interfaccia.
   * @param {HTMLElement} container
   */
  function init(container) {
    render(container);
  }

  global.CaduceoDashboard = {
    init:   init,
    render: render,
  };

}(window));
