/* VeggieCare Dashboard — vanilla JS, no dependencies */

(() => {
  const API = '/api';
  const POLL_MS = 5000;

  // DOM references
  const els = {
    uptime: document.getElementById('uptime'),
    modeBadge: document.getElementById('mode-badge'),
    alertsList: document.querySelector('.alerts-list'),
    clearAlerts: document.getElementById('clear-alerts'),
    npk: { n: n('npk-n'), p: n('npk-p'), k: n('npk-k') },
    npkTh: { n: n('npk-th-n'), p: n('npk-th-p'), k: n('npk-th-k') },
    npkSt: { n: n('npk-st-n'), p: n('npk-st-p'), k: n('npk-st-k') },
    npkItems: { n: n('[data-nutrient="nitrogen"]'), p: n('[data-nutrient="phosphorus"]'), k: n('[data-nutrient="potassium"]') },
    btnR1: n('btn-relay1'),
    r1Dur: n('r1-dur'),
    npkMeta: n('npk-meta'),
    moistVal: n('moist-val'),
    moistBar: n('moist-bar'),
    moistThLine: n('moist-th-line'),
    moistThLabel: n('moist-th-label'),
    moistStatus: n('moist-status'),
    moistMeta: n('moist-meta'),
    relayGrid: n('relay-grid'),
    btnEmergency: n('btn-emergency'),
    pestStatus: n('pest-status'),
    pestDetails: n('pest-details'),
    pestUsed: n('pest-used'),
    pestMax: n('pest-max'),
    pestRemaining: n('pest-remaining'),
    pestSimControls: n('pest-sim-controls'),
    simPestClass: n('sim-pest-class'),
    simConfidence: n('sim-confidence'),
    simConfVal: n('sim-conf-val'),
    btnSimPest: n('btn-sim-pest'),
    sysUptime: n('sys-uptime'),
    sysDb: n('sys-db'),
    sysNpk: n('sys-npk'),
    sysMoist: n('sys-moist'),
    sysCam: n('sys-cam'),
    sysPest: n('sys-pest'),
    sysAuto: n('sys-auto'),
    btnPause: n('btn-pause'),
    btnResume: n('btn-resume'),
    settingsBanner: n('settings-banner'),
    settingsGroups: n('settings-groups'),
    btnSaveSettings: n('btn-save-settings'),
    settingsSaveNote: n('settings-save-note'),
    btnRestartServer: n('btn-restart-server'),
    restartStatus: n('restart-status'),
    navTabs: document.querySelectorAll('.nav-tab'),
    subTabs: document.querySelectorAll('.sub-tab'),
    logsLimit: n('logs-limit'),
    logsRefresh: n('logs-refresh'),
    logPanels: {
      readings: n('log-readings'),
      activations: n('log-activations'),
      events: n('log-events'),
      detections: n('log-detections'),
    },
  };

  function n(sel) {
  // '#'/'.'/'[' prefix = CSS selector; anything else = element ID
  return sel[0] === '#' || sel[0] === '.' || sel[0] === '['
    ? document.querySelector(sel)
    : document.getElementById(sel);
}

  // State
  let alertId = 0;
  let knownAlertKeys = new Set();
  let activeView = 'overview';

  // Helpers
  function fmtTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function fmtDuration(ms) {
    if (ms < 1000) return `${ms}ms`;
    const s = Math.floor(ms / 1000);
    const m = Math.floor(s / 60);
    const h = Math.floor(m / 60);
    if (h) return `${h}h ${m % 60}m`;
    if (m) return `${m}m ${s % 60}s`;
    return `${s}s`;
  }

  function uptimeStr(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    return `${h}h ${m}m ${s}s`;
  }

  function statusDot(ok) {
    const span = document.createElement('span');
    span.className = 'status ' + (ok ? 'ok' : 'error');
    return span;
  }

  // API
  async function fetchJson(url, options = {}) {
    const res = await fetch(url, { headers: { 'Accept': 'application/json' }, ...options });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  }

  async function postJson(url, body = {}) {
    return fetchJson(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }

  // Polling
  async function poll() {
    try {
      const data = await fetchJson(`${API}/state`);
      render(data);
    } catch (e) {
      console.error('Poll failed:', e);
      addAlert('error', 'Dashboard', `Failed to fetch state: ${e.message}`);
    }
    if (activeView === 'logs') loadLogs();
  }

  // Render
  function render(data) {
    // Header
    els.uptime.textContent = `Uptime: ${uptimeStr(data.system.uptime_seconds)}`;
    els.modeBadge.textContent = data.system.simulate_hardware ? 'SIMULATED' : 'LIVE';
    els.modeBadge.className = 'badge ' + (data.system.simulate_hardware ? 'badge-simulated' : 'badge-real');

    // NPK
    const npk = data.sensors.npk;
    const th = data.thresholds?.npk || {};
    const NPK_KEYS = { nitrogen: 'n', phosphorus: 'p', potassium: 'k' };
    ['nitrogen', 'phosphorus', 'potassium'].forEach(nut => {
      const key = NPK_KEYS[nut]; // n, p, k
      const val = npk[nut];
      const below = npk.below?.[nut];
      const threshold = th[nut];
      const item = els.npkItems[key];
      const valEl = els.npk[key];
      const thEl = els.npkTh[key];
      const stEl = els.npkSt[key];

      if (val !== undefined && val !== null) {
        valEl.textContent = val.toFixed(2);
      } else {
        valEl.textContent = '—';
      }
      thEl.textContent = threshold !== undefined ? `threshold: ${threshold} mg/kg` : 'threshold: —';
      if (below) {
        item.classList.add('below');
        stEl.textContent = 'BELOW';
        stEl.className = 'status below';
      } else {
        item.classList.remove('below');
        stEl.textContent = 'OK';
        stEl.className = 'status normal';
      }
    });

    // Relay 1 button
    const r1 = data.relays.find(r => r.id === 1);
    if (r1) {
      els.btnR1.disabled = !r1.available || r1.state === 'on';
      els.r1Dur.textContent = r1.activation_duration_seconds || '—';
    }
    els.npkMeta.textContent = `Last reading: ${fmtTime(npk.timestamp)}`;

    // Moisture
    const moist = data.sensors.moisture;
    if (moist.value !== null && moist.value !== undefined) {
      els.moistVal.textContent = moist.value.toFixed(1);
      els.moistVal.className = 'value large' + (moist.below ? ' below' : '');
      const pct = Math.max(0, Math.min(100, moist.value));
      els.moistBar.style.width = `${pct}%`;
      const th = data.thresholds?.moisture || 30;
      els.moistThLabel.textContent = `Threshold: ${th}%`;
      els.moistThLine.style.left = `${Math.max(0, Math.min(100, th))}%`;
      if (moist.below) {
        els.moistStatus.textContent = 'BELOW THRESHOLD';
        els.moistStatus.className = 'status below';
      } else {
        els.moistStatus.textContent = 'Normal';
        els.moistStatus.className = 'status normal';
      }
    } else {
      els.moistVal.textContent = '—';
      els.moistVal.className = 'value large';
      els.moistBar.style.width = '0%';
      els.moistThLine.style.left = '0%';
    }

    // Usage (pest response monthly limit)
    renderUsage(data.usage);

    els.moistMeta.textContent = `Last reading: ${fmtTime(moist.timestamp)} | Last watering: —`;

    // Relays
    renderRelays(data.relays);

    // Pest
    renderPest(data.pest);

    // System
    renderSystem(data);

    // Automation
    renderAutomation(data.automation);

    // Alerts
    renderAlerts(data.alerts);
  }

  function renderRelays(relays) {
    els.relayGrid.innerHTML = '';
    relays.forEach(r => {
      const card = document.createElement('div');
      card.className = `relay-card ${r.state} ${r.available ? '' : 'unavailable'}`;
      const dur = r.activated_at && r.duration
        ? Math.max(0, Math.floor(r.duration - (Date.now() / 1000 - new Date(r.activated_at).getTime() / 1000)))
        : 0;
      card.innerHTML = `
        <div class="relay-header">
          <span class="relay-name">${r.label || r.name} (Relay ${r.id})</span>
          <span class="relay-pin">GPIO ${r.pin}</span>
        </div>
        <span class="relay-state ${r.state}">${r.state.toUpperCase()}</span>
        ${r.state === 'on' ? `<div class="relay-duration">Auto-off in ${fmtDuration(dur * 1000)}</div>` : ''}
        <div class="relay-actions">
          <button class="btn btn-primary ${r.state === 'on' || !r.available ? 'hidden' : ''}" data-on="${r.id}">ON</button>
          <button class="btn btn-secondary ${r.state === 'off' ? 'hidden' : ''}" data-off="${r.id}">OFF</button>
        </div>
      `;
      els.relayGrid.appendChild(card);
    });
  }

  function renderPest(pest) {
    if (!pest.enabled || !pest.configured) {
      els.pestStatus.textContent = 'Not configured / Camera not installed';
      els.pestStatus.style.display = 'block';
      els.pestDetails.classList.add('hidden');
      els.pestSimControls.style.display = 'none';
      return;
    }
    els.pestStatus.style.display = 'none';
    els.pestDetails.classList.remove('hidden');
    els.pestSimControls.style.display = 'flex';
    els.pestDetails.innerHTML = `
      <div class="pest-detail-row"><span class="label">Status</span><span class="value ${pest.detected ? 'detected' : 'not-detected'}">${pest.detected ? 'DETECTED' : 'None'}</span></div>
      <div class="pest-detail-row"><span class="label">Class</span><span class="value">${pest.pest_class || '—'}</span></div>
      <div class="pest-detail-row"><span class="label">Confidence</span><span class="value">${pest.confidence !== null ? (pest.confidence * 100).toFixed(1) + '%' : '—'}</span></div>
      <div class="pest-detail-row"><span class="label">Model</span><span class="value">${pest.model || '—'}</span></div>
      <div class="pest-detail-row"><span class="label">Last detection</span><span class="value">${fmtTime(pest.timestamp)}</span></div>
    `;
  }

  function renderUsage(usage) {
    if (!usage || !usage.max) {
      els.pestUsed.textContent = '—';
      els.pestMax.textContent = '—';
      els.pestRemaining.textContent = 'Remaining: —';
      els.pestRemaining.className = 'remaining';
      return;
    }
    els.pestUsed.textContent = usage.used;
    els.pestMax.textContent = usage.max;
    const rem = usage.remaining;
    els.pestRemaining.textContent = `Remaining: ${rem}`;
    els.pestRemaining.className = 'remaining' +
      (rem <= 0 ? ' critical' : rem <= 1 ? ' low' : '');
  }

  function renderSystem(data) {
    els.sysUptime.textContent = uptimeStr(data.system.uptime_seconds);
    // DB
    els.sysDb.innerHTML = '';
    els.sysDb.appendChild(statusDot(data.database.ok));
    els.sysDb.append(data.database.ok ? ' Connected' : ` Error: ${data.database.error || 'unknown'}`);

    // NPK sensor
    const npk = data.sensors.npk;
    const npkOk = !npk.error && npk.last_success;
    els.sysNpk.innerHTML = '';
    els.sysNpk.appendChild(statusDot(npkOk));
    els.sysNpk.append(npkOk ? ' OK' : ` Error: ${npk.error || 'no reading'}`);

    // Moisture sensor
    const moist = data.sensors.moisture;
    const moistOk = !moist.error && moist.last_success;
    els.sysMoist.innerHTML = '';
    els.sysMoist.appendChild(statusDot(moistOk));
    els.sysMoist.append(moistOk ? ' OK' : ` Error: ${moist.error || 'no reading'}`);

    // Camera
    els.sysCam.innerHTML = '';
    const cam = data.camera;
    if (cam.configured) {
      els.sysCam.appendChild(statusDot(!cam.error));
      els.sysCam.append(cam.error ? ` Error: ${cam.error}` : ' Connected');
    } else {
      els.sysCam.appendChild(statusDot(false));
      els.sysCam.append(' Not installed');
    }

    // Pest model
    els.sysPest.innerHTML = '';
    const pest = data.pest;
    if (pest.enabled && pest.configured) {
      els.sysPest.appendChild(statusDot(!pest.error));
      els.sysPest.append(pest.error ? ` Error: ${pest.error}` : ` Loaded (${pest.model})`);
    } else {
      els.sysPest.appendChild(statusDot(false));
      els.sysPest.append(' Not configured');
    }

    // Automation
    els.sysAuto.innerHTML = '';
    const auto = data.automation;
    if (auto.running) {
      els.sysAuto.appendChild(statusDot(!auto.paused));
      els.sysAuto.append(auto.paused ? ' Paused' : ' Running');
    } else {
      els.sysAuto.appendChild(statusDot(false));
      els.sysAuto.append(' Stopped');
    }
  }

  function renderAutomation(auto) {
    if (auto.running && !auto.paused) {
      els.btnPause.classList.remove('hidden');
      els.btnResume.classList.add('hidden');
    } else if (auto.running && auto.paused) {
      els.btnPause.classList.add('hidden');
      els.btnResume.classList.remove('hidden');
    } else {
      els.btnPause.classList.add('hidden');
      els.btnResume.classList.add('hidden');
    }
  }

  function renderAlerts(alerts) {
    alerts.forEach(a => {
      const key = `${a.timestamp}-${a.source}-${a.message}`;
      if (knownAlertKeys.has(key)) return;
      knownAlertKeys.add(key);
      addAlertElement(a.level, a.message, a.source, a.timestamp);
    });
    // Keep only last 30
    while (els.alertsList.children.length > 30) {
      els.alertsList.removeChild(els.alertsList.lastChild);
    }
  }

  function addAlertElement(level, message, source, timestamp) {
    const div = document.createElement('div');
    div.className = `alert-item ${level}`;
    div.innerHTML = `
      <span class="alert-level">${level.toUpperCase()}</span>
      <span class="alert-message">${escapeHtml(message)}</span>
      <span class="alert-meta">${source} • ${fmtTime(timestamp)}</span>
    `;
    els.alertsList.insertBefore(div, els.alertsList.firstChild);
  }

  function addAlert(level, source, message) {
    const timestamp = new Date().toISOString().replace('T', ' ').slice(0, 19);
    addAlertElement(level, message, source, timestamp);
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // ------------------------------------------------------------------
  // Logs view
  // ------------------------------------------------------------------

  function logLimit() { return parseInt(els.logsLimit.value, 10) || 50; }

  async function loadLogs() {
    const limit = logLimit();
    const [npk, moist, act, evt, det] = await Promise.allSettled([
      fetchJson(`${API}/readings/npk?limit=${limit}`),
      fetchJson(`${API}/readings/moisture?limit=${limit}`),
      fetchJson(`${API}/activations?limit=${limit}`),
      fetchJson(`${API}/events?limit=${limit}`),
      fetchJson(`${API}/detections?limit=${limit}`),
    ]);

    if (npk.status === 'fulfilled' && moist.status === 'fulfilled') {
      renderReadings(mergeReadings(npk.value, moist.value));
    } else {
      renderLogError(els.logPanels.readings, 'Failed to load readings');
    }

    act.status === 'fulfilled'
      ? renderActivations(act.value)
      : renderLogError(els.logPanels.activations, 'Failed to load activations');

    evt.status === 'fulfilled'
      ? renderEvents(evt.value)
      : renderLogError(els.logPanels.events, 'Failed to load events');

    det.status === 'fulfilled'
      ? renderDetections(det.value)
      : renderLogError(els.logPanels.detections, 'Failed to load detections');
  }

  function mergeReadings(npkRows, moistRows) {
    const byTs = new Map();
    for (const r of npkRows) {
      byTs.set(r.timestamp, { timestamp: r.timestamp, nitrogen: r.nitrogen, phosphorus: r.phosphorus, potassium: r.potassium });
    }
    for (const r of moistRows) {
      const row = byTs.get(r.timestamp);
      if (row) row.moisture = r.moisture;
      else byTs.set(r.timestamp, { timestamp: r.timestamp, moisture: r.moisture });
    }
    return [...byTs.values()].sort((a, b) => b.timestamp.localeCompare(a.timestamp));
  }

  function logTable(headers, rowsHtml, emptyText) {
    if (!rowsHtml.length) return `<p class="log-empty">${emptyText}</p>`;
    const thead = headers.map(h => `<th>${h}</th>`).join('');
    return `<div class="log-table-wrap"><table class="log-table"><thead><tr>${thead}</tr></thead><tbody>${rowsHtml.join('')}</tbody></table></div>`;
  }

  function fmtNum(v) {
    if (v === null || v === undefined) return '—';
    return Number.isInteger(v) ? String(v) : Number(v).toFixed(1);
  }

  function renderReadings(rows) {
    const html = rows.map(r => `<tr>
      <td class="time">${fmtTime(r.timestamp)}</td>
      <td class="num">${fmtNum(r.nitrogen)}</td>
      <td class="num">${fmtNum(r.phosphorus)}</td>
      <td class="num">${fmtNum(r.potassium)}</td>
      <td class="num">${fmtNum(r.moisture)}</td>
    </tr>`);
    els.logPanels.readings.innerHTML = logTable(
      ['Time', 'N (mg/kg)', 'P (mg/kg)', 'K (mg/kg)', 'Moisture (%)'],
      html, 'No readings recorded yet.',
    );
  }

  function renderActivations(rows) {
    const html = rows.map(r => `<tr>
      <td class="time">${fmtTime(r.timestamp)}</td>
      <td>${escapeHtml(r.relay_name || '—')}</td>
      <td><span class="log-badge ${escapeHtml(r.trigger_type)}">${escapeHtml(r.trigger_type)}</span></td>
      <td class="num">${r.duration_seconds != null ? r.duration_seconds + 's' : '—'}</td>
      <td>${escapeHtml(r.source || '—')}</td>
    </tr>`);
    els.logPanels.activations.innerHTML = logTable(
      ['Time', 'Relay', 'Trigger', 'Duration', 'Source'],
      html, 'No relay activations logged yet.',
    );
  }

  function renderEvents(rows) {
    const html = rows.map(r => `<tr>
      <td class="time">${fmtTime(r.timestamp)}</td>
      <td><span class="log-badge ${escapeHtml(String(r.level).toLowerCase())}">${escapeHtml(r.level)}</span></td>
      <td>${escapeHtml(r.source || '—')}</td>
      <td>${escapeHtml(r.message)}</td>
    </tr>`);
    els.logPanels.events.innerHTML = logTable(
      ['Time', 'Level', 'Source', 'Message'],
      html, 'No system events logged yet.',
    );
  }

  function renderDetections(rows) {
    const html = rows.map(r => `<tr>
      <td class="time">${fmtTime(r.timestamp)}</td>
      <td><span class="log-badge ${r.detected ? 'detected' : 'none'}">${r.detected ? 'Detected' : 'None'}</span></td>
      <td>${r.pest_class ? escapeHtml(r.pest_class) : '—'}</td>
      <td class="num">${r.confidence != null ? (r.confidence * 100).toFixed(0) + '%' : '—'}</td>
      <td>${r.model ? escapeHtml(r.model) : '—'}</td>
    </tr>`);
    els.logPanels.detections.innerHTML = logTable(
      ['Time', 'Result', 'Pest', 'Confidence', 'Model'],
      html, 'No pest detections logged yet.',
    );
  }

  function renderLogError(panel, message) {
    panel.innerHTML = `<p class="log-error">${escapeHtml(message)}</p>`;
  }

  function switchView(view) {
    activeView = view;
    els.navTabs.forEach(t => t.classList.toggle('active', t.dataset.view === view));
    document.getElementById('view-overview').classList.toggle('hidden', view !== 'overview');
    document.getElementById('view-logs').classList.toggle('hidden', view !== 'logs');
    document.getElementById('view-settings').classList.toggle('hidden', view !== 'settings');
    if (view === 'logs') loadLogs();
    if (view === 'settings') loadSettings();
  }

  function switchLogTab(log) {
    els.subTabs.forEach(t => t.classList.toggle('active', t.dataset.log === log));
    for (const [key, panel] of Object.entries(els.logPanels)) {
      panel.classList.toggle('hidden', key !== log);
    }
  }

  // ------------------------------------------------------------------
  // Settings view
  // ------------------------------------------------------------------

  function showSettingsBanner(kind, message) {
    els.settingsBanner.textContent = message;
    els.settingsBanner.className = 'settings-banner ' + kind;
  }

  function hideSettingsBanner() {
    els.settingsBanner.className = 'settings-banner hidden';
    els.settingsBanner.textContent = '';
  }

  function uiValueFor(field) {
    const stored = field.value == null ? field.min : field.value;
    return field.factor ? Math.round((stored / field.factor) * 100) / 100 : stored;
  }

  function fmtSliderValue(field, ui) {
    const n = field.integer ? Math.round(ui) : Math.round(ui * 100) / 100;
    return `${n}${field.unit ? ' ' + field.unit : ''}`;
  }

  async function loadSettings() {
    try {
      const data = await fetchJson(`${API}/config`);
      renderSettings(data);
    } catch (e) {
      showSettingsBanner('error', `Failed to load settings: ${e.message}`);
    }
  }

  function renderSettings(data) {
    els.settingsGroups.innerHTML = '';
    hideSettingsBanner();

    if (data.pending) {
      const fields = data.pending.fields.join(', ');
      showSettingsBanner('warning', `Settings saved at ${fmtTime(data.pending.saved_at)} — restart the server to apply them. (${fields})`);
    }

    data.groups.forEach(group => {
      const section = document.createElement('section');
      section.className = 'card settings-group';
      const header = document.createElement('header');
      header.className = 'card-header';
      const h2 = document.createElement('h2');
      h2.textContent = group.title;
      header.appendChild(h2);
      section.appendChild(header);
      if (group.description) {
        const desc = document.createElement('p');
        desc.className = 'group-desc';
        desc.textContent = group.description;
        section.appendChild(desc);
      }
      const list = document.createElement('div');
      list.className = 'setting-list';
      group.fields.forEach(field => list.appendChild(buildFieldRow(field)));
      section.appendChild(list);
      els.settingsGroups.appendChild(section);
    });

    els.btnSaveSettings.classList.add('hidden');
    els.settingsSaveNote.textContent = 'Changes apply after a restart.';
    els.btnRestartServer.disabled = !data.restart_supported;
    els.restartStatus.textContent = data.restart_supported
      ? 'Restart switches all pumps off for about 30 seconds.'
      : 'Restart is only available when running as a systemd service.';
  }

  function buildFieldRow(field) {
    const row = document.createElement('div');
    row.className = 'setting-row';
    row.dataset.key = field.key;

    const info = document.createElement('div');
    info.className = 'setting-info';
    const label = document.createElement('span');
    label.className = 'setting-label';
    label.textContent = field.label;
    const desc = document.createElement('span');
    desc.className = 'setting-desc';
    desc.textContent = field.description || '';
    info.appendChild(label);
    info.appendChild(desc);

    const control = document.createElement('div');
    control.className = 'setting-control';
    let input;
    if (field.type === 'toggle') {
      input = document.createElement('input');
      input.type = 'checkbox';
      input.className = 'settings-toggle';
      input.checked = !!field.value;
    } else if (field.type === 'slider') {
      input = document.createElement('input');
      input.type = 'range';
      input.className = 'settings-slider';
      input.min = field.min;
      input.max = field.max;
      input.step = field.step != null ? field.step : 1;
      const ui = uiValueFor(field);
      input.value = Math.max(field.min, Math.min(field.max, ui));
      const val = document.createElement('span');
      val.className = 'slider-val';
      val.textContent = fmtSliderValue(field, ui);
      control.appendChild(val);
      input.addEventListener('input', () => {
        val.textContent = fmtSliderValue(field, parseFloat(input.value));
        markSettingsDirty();
      });
    } else if (field.type === 'select') {
      input = document.createElement('select');
      input.className = 'settings-select';
      (field.options || []).forEach(opt => {
        const o = document.createElement('option');
        o.value = opt;
        o.textContent = opt;
        input.appendChild(o);
      });
      input.value = field.value != null ? field.value : (field.options || [])[0];
    } else {
      input = document.createElement('input');
      input.type = 'text';
      input.className = 'settings-text';
      input.value = field.value != null ? field.value : '';
    }
    input.dataset.fieldKey = field.key;
    input.dataset.fieldType = field.type;
    input.addEventListener('change', markSettingsDirty);
    control.appendChild(input);
    row.appendChild(info);
    row.appendChild(control);
    return row;
  }

  function markSettingsDirty() {
    els.btnSaveSettings.classList.remove('hidden');
    els.settingsSaveNote.textContent = 'Unsaved changes — save to apply them after a restart.';
  }

  function collectSettings() {
    const settings = {};
    els.settingsGroups.querySelectorAll('[data-field-key]').forEach(el => {
      const key = el.dataset.fieldKey;
      const type = el.dataset.fieldType;
      if (type === 'toggle') settings[key] = el.checked;
      else if (type === 'slider') settings[key] = parseFloat(el.value);
      else settings[key] = el.value;
    });
    return settings;
  }

  function markFieldErrors(errors) {
    els.settingsGroups.querySelectorAll('.setting-row').forEach(r => r.classList.remove('field-error'));
    (errors || []).forEach(err => {
      const labelPart = err.split(':')[0].trim();
      els.settingsGroups.querySelectorAll('.setting-row').forEach(row => {
        const lbl = row.querySelector('.setting-label');
        if (lbl && lbl.textContent.trim() === labelPart) row.classList.add('field-error');
      });
    });
  }

  async function saveSettings() {
    els.btnSaveSettings.disabled = true;
    try {
      const res = await fetch(`${API}/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings: collectSettings() }),
      });
      let body = {};
      try { body = await res.json(); } catch (_) { /* non-JSON error */ }
      if (!res.ok) {
        const errs = (body.errors || []).map(e => `• ${e}`).join(' ');
        showSettingsBanner('error', `${body.error || 'Could not save settings.'}${errs ? ' ' + errs : ''}`);
        markFieldErrors(body.errors);
        return;
      }
      showSettingsBanner('success', body.message || 'Settings saved. Restart the server to apply them.');
      els.btnSaveSettings.classList.add('hidden');
      els.settingsSaveNote.textContent = 'Saved — restart the server to apply.';
    } catch (e) {
      showSettingsBanner('error', `Could not save settings: ${e.message}`);
    } finally {
      els.btnSaveSettings.disabled = false;
    }
  }

  async function restartServer() {
    if (!confirm('Restart the server now? This takes about 30 seconds. All pumps are switched off during the restart.')) return;
    els.btnRestartServer.disabled = true;
    els.restartStatus.textContent = 'Restarting…';
    try {
      const res = await fetch(`${API}/system/restart`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      let body = {};
      try { body = await res.json(); } catch (_) { /* non-JSON error */ }
      if (!res.ok) {
        els.restartStatus.textContent = body.error || `Restart failed (${res.status}).`;
        els.btnRestartServer.disabled = false;
        return;
      }
      els.restartStatus.textContent = body.message || 'Restarting — the dashboard will reconnect in about 30 seconds.';
      await waitForServerBack();
    } catch (e) {
      els.restartStatus.textContent = `Restart failed: ${e.message}`;
      els.btnRestartServer.disabled = false;
    }
  }

  async function waitForServerBack() {
    const deadline = Date.now() + 90 * 1000;
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 3000));
      try {
        const res = await fetch(`${API}/state`, { headers: { 'Accept': 'application/json' } });
        if (res.ok) {
          location.reload();
          return;
        }
      } catch (_) { /* server still down — keep polling */ }
    }
    els.restartStatus.textContent = 'Server did not come back after restart. Check the service status.';
    els.btnRestartServer.disabled = false;
  }

  // Actions
  async function activateRelay(id) {
    try {
      const res = await postJson(`${API}/relays/${id}/activate`);
      if (!res.ok) addAlert('error', 'Dashboard', res.message);
    } catch (e) { addAlert('error', 'Dashboard', e.message); }
  }

  async function deactivateRelay(id) {
    try {
      await postJson(`${API}/relays/${id}/off`);
    } catch (e) { addAlert('error', 'Dashboard', e.message); }
  }

  async function emergencyStop() {
    if (!confirm('Force ALL relays OFF? This is an emergency stop.')) return;
    try {
      await postJson(`${API}/relays/all-off`);
    } catch (e) { addAlert('error', 'Dashboard', e.message); }
  }

  async function pauseAutomation() {
    try { await postJson(`${API}/automation/pause`); } catch (e) { addAlert('error', 'Dashboard', e.message); }
  }

  async function resumeAutomation() {
    try { await postJson(`${API}/automation/resume`); } catch (e) { addAlert('error', 'Dashboard', e.message); }
  }

  async function simulatePest() {
    try {
      const res = await postJson(`${API}/pest/simulate`, {
        detected: true,
        pest_class: els.simPestClass.value,
        confidence: parseFloat(els.simConfidence.value),
      });
      if (res.ok) addAlert('info', 'Dashboard', `Simulated ${res.pest_class} (${(res.confidence*100).toFixed(0)}%)`);
      else addAlert('error', 'Dashboard', res.message);
    } catch (e) { addAlert('error', 'Dashboard', e.message); }
  }

  // Event listeners
  els.btnR1.addEventListener('click', () => activateRelay(1));
  els.btnEmergency.addEventListener('click', emergencyStop);
  els.btnPause.addEventListener('click', pauseAutomation);
  els.btnResume.addEventListener('click', resumeAutomation);
  els.btnSimPest.addEventListener('click', simulatePest);
  els.btnSaveSettings.addEventListener('click', saveSettings);
  els.btnRestartServer.addEventListener('click', restartServer);
  els.simConfidence.addEventListener('input', () => {
    els.simConfVal.textContent = parseFloat(els.simConfidence.value).toFixed(2);
  });

  els.relayGrid.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-on],[data-off]');
    if (!btn) return;
    const id = parseInt(btn.dataset.on || btn.dataset.off, 10);
    if (btn.dataset.on) await activateRelay(id);
    else await deactivateRelay(id);
  });
  els.clearAlerts.addEventListener('click', () => {
    els.alertsList.innerHTML = '';
    knownAlertKeys.clear();
  });
  els.navTabs.forEach(t => t.addEventListener('click', () => switchView(t.dataset.view)));
  els.subTabs.forEach(t => t.addEventListener('click', () => switchLogTab(t.dataset.log)));
  els.logsRefresh.addEventListener('click', loadLogs);
  els.logsLimit.addEventListener('change', loadLogs);

  // Start
  poll();
  setInterval(poll, POLL_MS);
})();