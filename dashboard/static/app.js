/* VeggieCare Dashboard — vanilla JS, no dependencies */

(() => {
  const API = '/api';
  const POLL_MS = 5000;

  // Pump names in plain language, kept next to the engineering relay id.
  const PUMP_NAMES = { 1: 'Plant food pump', 2: 'Watering pump', 3: 'Pest sprayer' };

  // DOM references
  const els = {
    uptime: document.getElementById('uptime'),
    modeBadge: document.getElementById('mode-badge'),
    alertsList: document.querySelector('.alerts-list'),
    clearAlerts: document.getElementById('clear-alerts'),
    // At-a-glance chips
    chipSoilValue: document.getElementById('chip-soil-value'),
    chipSoilDot: document.querySelector('#chip-soil .chip-dot'),
    chipWateringValue: document.getElementById('chip-watering-value'),
    chipWateringDot: document.querySelector('#chip-watering .chip-dot'),
    chipPestsValue: document.getElementById('chip-pests-value'),
    chipPestsDot: document.querySelector('#chip-pests .chip-dot'),
    chipCameraValue: document.getElementById('chip-camera-value'),
    chipCameraDot: document.querySelector('#chip-camera .chip-dot'),
    chipAlertsValue: document.getElementById('chip-alerts-value'),
    chipAlertsDot: document.querySelector('#chip-alerts .chip-dot'),
    // Notification bell & panel
    notifBell: n('notif-bell'),
    notifBadge: n('notif-badge'),
    notifPanel: n('notif-panel'),
    notifList: n('notif-list'),
    notifMarkAll: n('notif-mark-all'),
    notifClear: n('notif-clear'),
    npk: { n: n('npk-n'), p: n('npk-p'), k: n('npk-k') },
    npkTh: { n: n('npk-th-n'), p: n('npk-th-p'), k: n('npk-th-k') },
    npkSt: { n: n('npk-st-n'), p: n('npk-st-p'), k: n('npk-st-k') },
    npkState: { n: n('npk-state-n'), p: n('npk-state-p'), k: n('npk-state-k') },
    npkItems: { n: n('[data-nutrient="nitrogen"]'), p: n('[data-nutrient="phosphorus"]'), k: n('[data-nutrient="potassium"]') },
    btnR1: n('btn-relay1'),
    r1Dur: n('r1-dur'),
    npkMeta: n('npk-meta'),
    npkConnectionDot: n('npk-connection-dot'),
    npkConnectionText: n('npk-connection-text'),
    btnNpkReadNow: n('btn-npk-read-now'),
    // Recommendation
    recStatus: n('rec-status'),
    recPriority: n('rec-priority'),
    recModel: n('rec-model'),
    recFertilizerName: n('rec-fertilizer-name'),
    recDosage: n('rec-dosage'),
    recReasoning: n('rec-reasoning'),
    recDeficits: n('rec-deficits'),
    recDur: n('rec-dur'),
    recMeta: n('rec-meta'),
    btnApplyFertilizer: n('btn-apply-fertilizer'),
    moistVal: n('moist-val'),
    moistBar: n('moist-bar'),
    moistThLine: n('moist-th-line'),
    moistThLabel: n('moist-th-label'),
    moistStatus: n('moist-status'),
    moistState: n('moist-state'),
    moistMeta: n('moist-meta'),
    // Garden area awareness
    gardenArea: n('garden-area'),
    gardenAreaState: n('garden-area-state'),
    gardenTarget: n('garden-target'),
    gardenTargetTh: n('garden-target-th'),
    gardenBudgetMode: n('garden-budget-mode'),
    gardenBudgetState: n('garden-budget-state'),
    gardenMeta: n('garden-meta'),
    // Watering closed-loop (fault banner + daily budget + cooldown)
    wateringFault: n('watering-fault'),
    wateringFaultReason: n('watering-fault-reason'),
    btnWateringReset: n('btn-watering-reset'),
    wateringUsage: n('watering-usage'),
    wateringUsed: n('watering-used'),
    wateringMax: n('watering-max'),
    wateringRemaining: n('watering-remaining'),
    wateringCooldown: n('watering-cooldown'),
    relayGrid: n('relay-grid'),
    btnEmergency: n('btn-emergency'),
    pestStatus: n('pest-status'),
    pestDetails: n('pest-details'),
    pestTreatment: n('pest-treatment'),
    pestUsed: n('pest-used'),
    pestMax: n('pest-max'),
    pestRemaining: n('pest-remaining'),
    pestSimControls: n('pest-sim-controls'),
    pestScanActions: n('pest-scan-actions'),
    btnScanStepper: n('btn-scan-stepper'),
    btnScanCamera: n('btn-scan-camera'),
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
    btnSystemTest: n('btn-system-test'),
    btnStepperTest: n('btn-stepper-test'),
    stepperSteps: n('stepper-steps'),
    stepperDelay: n('stepper-delay'),
    stepperTestResult: n('stepper-test-result'),
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
  let lastTanks = {};

  // Notification panel state — read/dismissed keys persist in localStorage
  const NOTIF_READ_KEY = 'veggiecare.notif.read';
  const NOTIF_DISMISSED_KEY = 'veggiecare.notif.dismissed';
  const NOTIF_CAP = 100;
  let notifRead = new Set(loadNotifKeys(NOTIF_READ_KEY));
  let notifDismissed = new Set(loadNotifKeys(NOTIF_DISMISSED_KEY));
  let notifSig = null;
  let lastNotifAlerts = [];
  let lastNotifShown = [];

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

  function stateSpan(text, kind) {
    const span = document.createElement('span');
    span.className = 'state ' + kind;
    span.textContent = text;
    return span;
  }

  // Chip dot classes by severity.
  function chipDotKind(kind) {
    return { good: 'dot-good', warn: 'dot-warn', bad: 'dot-bad', unknown: 'dot-unknown' }[kind] || 'dot-unknown';
  }

  function setChip(chipValue, chipDot, text, kind) {
    if (chipValue) chipValue.textContent = text;
    if (chipDot) {
      chipDot.className = 'chip-dot ' + chipDotKind(kind);
      chipDot.setAttribute('aria-label', text);
    }
  }

  // NPK connection status rendering
  function renderNpkConnection(npk) {
    const dot = els.npkConnectionDot;
    const text = els.npkConnectionText;
    if (!dot || !text) return;

    if (npk.connected === true) {
      dot.className = 'connection-dot connected';
      text.textContent = 'NPK sensor connected';
      text.className = 'connection-text connected';
    } else if (npk.available === false) {
      dot.className = 'connection-dot error';
      text.textContent = 'NPK sensor error: ' + (npk.error || 'unknown');
      text.className = 'connection-text error';
    } else {
      dot.className = 'connection-dot unknown';
      text.textContent = 'NPK sensor not detected';
      text.className = 'connection-text unknown';
    }
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
    // Refresh collapsible card heights after content updates
    refreshCollapsibleCards();
  }

  // Render
  function render(data) {
    // Header
    els.uptime.textContent = `Running for ${uptimeStr(data.system.uptime_seconds)}`;
    els.modeBadge.textContent = data.system.simulate_hardware ? 'Demo mode' : 'Live hardware';
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
      const stateEl = els.npkState[key];

      if (val !== undefined && val !== null) {
        valEl.textContent = val.toFixed(2);
      } else {
        valEl.textContent = '—';
      }
      thEl.textContent = threshold !== undefined ? `Alert below: ${threshold} mg/kg` : 'Alert below: —';
      if (below) {
        item.classList.add('below');
        stEl.textContent = 'LOW';
        stEl.className = 'status below';
        stateEl.textContent = `${nut.charAt(0).toUpperCase() + nut.slice(1)} is low`;
        stateEl.className = 'state state-warn';
      } else {
        item.classList.remove('below');
        stEl.textContent = 'OK';
        stEl.className = 'status normal';
        stateEl.textContent = `${nut.charAt(0).toUpperCase() + nut.slice(1)} is fine`;
        stateEl.className = 'state state-good';
      }
    });

    // Relay 1 button
    const r1 = data.relays.find(r => r.id === 1);
    if (r1) {
      els.btnR1.disabled = !r1.available || r1.state === 'on';
      els.r1Dur.textContent = r1.activation_duration_seconds || '—';
    }
    els.npkMeta.textContent = `Last reading: ${fmtTime(npk.timestamp)}`;

    // NPK connection status
    renderNpkConnection(npk);

    // Moisture
    const moist = data.sensors.moisture;
    if (moist.value !== null && moist.value !== undefined) {
      els.moistVal.textContent = moist.value.toFixed(1);
      els.moistVal.className = 'value large' + (moist.below ? ' below' : '');
      const pct = Math.max(0, Math.min(100, moist.value));
      els.moistBar.style.width = `${pct}%`;
      const th = data.thresholds?.moisture || 30;
      els.moistThLabel.textContent = `Water when below: ${th}%`;
      els.moistThLine.style.left = `${Math.max(0, Math.min(100, th))}%`;
      if (moist.below) {
        els.moistStatus.textContent = 'DRY';
        els.moistStatus.className = 'status below';
        els.moistState.textContent = 'Soil is dry — plants need water';
        els.moistState.className = 'state state-warn';
      } else {
        els.moistStatus.textContent = 'OK';
        els.moistStatus.className = 'status normal';
        els.moistState.textContent = 'Soil moisture is fine';
        els.moistState.className = 'state state-good';
      }
    } else {
      els.moistVal.textContent = '—';
      els.moistVal.className = 'value large';
      els.moistBar.style.width = '0%';
      els.moistThLine.style.left = '0%';
      els.moistStatus.textContent = '—';
      els.moistStatus.className = 'status';
      els.moistState.textContent = 'No reading';
      els.moistState.className = 'state state-unknown';
    }

    // Usage (pest response monthly limit)
    renderUsage(data.usage);

    // Watering closed-loop (fault + daily budget + settle cooldown)
    renderWatering(data.watering);

    // Garden area awareness
    renderGarden(data);

    els.moistMeta.textContent = `Last reading: ${fmtTime(moist.timestamp)}`;

    // At-a-glance strip
    renderAtAGlance(data);

    // Tanks (fertilizer + pesticide)
    renderTanks(data.tanks, data.relays);
    // Relays
    renderRelays(data.relays);

    // Pest — pass the configured confidence threshold so the "possible"
    // state can show the real confirmation cutoff.
    renderPest(data.pest, data.thresholds?.pest ?? 0.70);

    // System
    renderSystem(data);

    // Automation
    renderAutomation(data.automation);

    // Recommendation
    renderRecommendation(data.recommendation, data.relays);

    // Alerts
    renderAlerts(data.alerts);

    // Notification bell (same alerts feed, separate read/dismissed state)
    renderNotifications(data.alerts);
  }

  function renderAtAGlance(data) {
    const moist = data.sensors.moisture;
    const moistVal = moist.value;
    const moistBelow = moist.below;
    if (moistVal !== null && moistVal !== undefined) {
      if (moistBelow) {
        setChip(els.chipSoilValue, els.chipSoilDot, 'Dry — needs water', 'bad');
      } else {
        setChip(els.chipSoilValue, els.chipSoilDot, 'Moisture is fine', 'good');
      }
    } else {
      setChip(els.chipSoilValue, els.chipSoilDot, 'No reading', 'unknown');
    }

    // Watering chip: is any pump running / is watering active?
    const relays = data.relays || [];
    const wateringPump = relays.find(r => r.id === 2);
    const anyOn = relays.some(r => r.state === 'on');
    if (wateringPump && wateringPump.state === 'on') {
      setChip(els.chipWateringValue, els.chipWateringDot, 'Watering now', 'warn');
    } else if (anyOn) {
      setChip(els.chipWateringValue, els.chipWateringDot, 'A pump is running', 'warn');
    } else {
      setChip(els.chipWateringValue, els.chipWateringDot, 'Pumps off', 'good');
    }

    // Pests chip
    const pest = data.pest;
    if (!pest.enabled || !pest.configured) {
      setChip(els.chipPestsValue, els.chipPestsDot, 'Pest watch off', 'unknown');
    } else if (pest.detected) {
      setChip(els.chipPestsValue, els.chipPestsDot, `Pest found: ${pest.pest_class || 'unknown'}`, 'bad');
    } else if (pest.status === 'possible') {
      setChip(els.chipPestsValue, els.chipPestsDot, `Possible pest: ${pest.pest_class || 'unknown'}`, 'warn');
    } else if (pest.status === 'error') {
      setChip(els.chipPestsValue, els.chipPestsDot, 'Pest scan failed', 'bad');
    } else {
      setChip(els.chipPestsValue, els.chipPestsDot, 'No pests found', 'good');
    }

    // Camera chip
    const cam = data.camera;
    if (!cam.configured) {
      setChip(els.chipCameraValue, els.chipCameraDot, 'Not connected', 'bad');
    } else if (cam.error) {
      setChip(els.chipCameraValue, els.chipCameraDot, 'Error', 'bad');
    } else {
      setChip(els.chipCameraValue, els.chipCameraDot, 'Working', 'good');
    }

    // Alerts chip
    const alerts = Array.isArray(data.alerts) ? data.alerts : [];
    const serious = alerts.some(a => a.level === 'error' || a.level === 'critical');
    if (alerts.length === 0) {
      setChip(els.chipAlertsValue, els.chipAlertsDot, 'None', 'good');
    } else if (serious) {
      setChip(els.chipAlertsValue, els.chipAlertsDot, `${alerts.length} need attention`, 'bad');
    } else {
      setChip(els.chipAlertsValue, els.chipAlertsDot, `${alerts.length} new`, 'warn');
    }
  }

  function renderTanks(tanks, relays) {
    if (!tanks) return;
    lastTanks = tanks;
    const fmtDate = (d) => d ? new Date(d).toLocaleString() : '-';
    const fmtLeft = (sec) => {
      if (sec === null || sec === undefined || isNaN(sec) || sec < 0) return '-';
      const h = Math.floor(sec / 3600);
      const m = Math.floor((sec % 3600) / 60);
      const s = Math.floor(sec % 60);
      if (h > 0) return h + 'h ' + m + 'm ' + s + 's';
      if (m > 0) return m + 'm ' + s + 's';
      return s + 's';
    };
    const mlToSec = (ml, lph) => (lph > 0 ? (ml * 3.6) / lph : 0);
    const coolFmt = (until) => {
      if (!until) return 'Ready';
      const cd = new Date(until).getTime() - Date.now();
      return cd > 0 ? fmtLeft(cd / 1000) : 'Ready';
    };
    const tankStatus = (t) => {
      if (t.empty) return 'Empty';
      if (t.low) return 'Low — refill soon';
      if (t.blocked_reason) return 'Blocked';
      return 'Ready';
    };
    // Dose per use in litres: pump_lph × dose_cycle_seconds / 3600.
    // Falls back to the server-provided per-cycle amount when cycle
    // seconds aren't (yet) available in the snapshot.
    const dosePerUse = (tank, relay) => {
      const lph = tank.pump_lph || 0;
      const cycleSec = tank.dose_cycle_seconds != null ? tank.dose_cycle_seconds
        : (relay ? relay.activation_duration_seconds : null);
      if (cycleSec != null && lph > 0) {
        return { litres: (lph * cycleSec) / 3600, hint: lph.toFixed(0) + ' L/h × ' + cycleSec.toFixed(0) + ' s cycle' };
      }
      const perCycleMl = tank.fert_ml_per_cycle != null ? tank.fert_ml_per_cycle
        : (tank.ml_per_activation != null ? tank.ml_per_activation : 0);
      if (perCycleMl > 0) {
        return { litres: perCycleMl / 1000, hint: perCycleMl.toFixed(0) + ' mL per use (server-set)' };
      }
      return { litres: 0, hint: '' };
    };

    const renderTank = (prefix, tank, relay, typeKey, lastUsedKey, extraRows) => {
      const status = tankStatus(tank);
      const statusEl = document.getElementById(`tank-${prefix}-status`);
      statusEl.textContent = status;
      statusEl.className = 'state ' +
        (status === 'Ready' ? 'state-good' : status === 'Empty' ? 'state-bad' : 'state-warn');

      const typeEl = document.getElementById(`tank-${prefix}-type`);
      typeEl.textContent = tank[typeKey] || '-';

      const remMl = tank.ml_remaining_est || 0;
      const remL = remMl / 1000;
      document.getElementById(`tank-${prefix}-rem`).textContent = remL.toFixed(1) + ' L';

      // Fill level bar: remaining / capacity
      const capacity = tank.tank_capacity_ml || 0;
      const fillPct = capacity > 0 ? Math.min(100, Math.max(0, (remMl / capacity) * 100)) : 0;
      const fillBar = document.getElementById(`tank-${prefix}-fill`);
      const fillPctEl = document.getElementById(`tank-${prefix}-fill-pct`);
      if (fillBar) {
        fillBar.style.width = fillPct + '%';
        fillBar.className = 'tank-fill-bar' +
          (fillPct <= 10 ? ' fill-empty' : fillPct <= 30 ? ' fill-low' : '');
      }
      if (fillPctEl) fillPctEl.textContent = capacity > 0 ? fillPct.toFixed(0) + '%' : '—';

      document.getElementById(`tank-${prefix}-rt`).textContent =
        '~' + mlToSec(remMl, tank.pump_lph).toFixed(1) + 's of run time left';
      document.getElementById(`tank-${prefix}-lastref`).textContent = fmtDate(tank.last_refill_at);
      document.getElementById(`tank-${prefix}-lastfert`).textContent = fmtDate(tank[lastUsedKey]);
      document.getElementById(`tank-${prefix}-cool`).textContent = coolFmt(tank.cooldown_until_at);
      document.getElementById(`tank-${prefix}-auto`).textContent = tank.auto_enabled ? 'On' : 'Off';

      // Amount each use — derived, shown in L with the derivation inline
      const dose = dosePerUse(tank, relay);
      const doseEl = document.getElementById(`tank-${prefix}-dose`);
      const doseHintEl = document.getElementById(`tank-${prefix}-dose-hint`);
      doseEl.textContent = dose.litres > 0 ? dose.litres.toFixed(1) + ' L per use' : '—';
      if (doseHintEl) doseHintEl.textContent = dose.hint ? '(' + dose.hint + ')' : '';

      const blockedEl = document.getElementById(`tank-${prefix}-blocked`);
      blockedEl.textContent = tank.blocked_reason || '—';
      blockedEl.classList.toggle('is-blocked', Boolean(tank.blocked_reason));

      if (extraRows) extraRows(tank);
    };

    const fert = tanks.fertilizer || {};
    renderTank('fert', fert, relays?.find(r => r.id === 1),
      'fertilizer_type', 'last_fertilized_at',
      (t) => {
        document.getElementById('tank-fert-npk').textContent =
          (t.npk_n || 0) + '/' + (t.npk_p || 0) + '/' + (t.npk_k || 0);
        document.getElementById('tank-fert-mode').textContent = t.mode === 'separate' ? 'Separate from watering' : 'Mixes with watering';
      });

    const pest = tanks.pesticide || {};
    renderTank('pest', pest, relays?.find(r => r.id === 3),
      'pesticide_type', 'last_pesticided_at',
      (t) => {
        document.getElementById('tank-pest-month').textContent = t.max_activations_per_month || '-';
      });
  }

  function renderRelays(relays) {
    els.relayGrid.innerHTML = '';
    relays.forEach(r => {
      const card = document.createElement('div');
      card.className = `relay-card ${r.state} ${r.available ? '' : 'unavailable'}`;
      const dur = r.activated_at && r.duration
        ? Math.max(0, Math.floor(r.duration - (Date.now() / 1000 - new Date(r.activated_at).getTime() / 1000)))
        : 0;
      const pumpName = PUMP_NAMES[r.id] || (r.label || r.name || `Relay ${r.id}`);
      const isOn = r.state === 'on';
      card.innerHTML = `
        <div class="relay-header">
          <span class="relay-name">${pumpName}</span>
          <span class="relay-pin">Relay ${r.id}</span>
        </div>
        <span class="relay-state ${r.state}">${isOn ? 'Running' : r.state === 'unavailable' ? 'Unavailable' : 'Off'}</span>
        ${r.state === 'on' ? `<div class="relay-duration">Off in ${fmtDuration(dur * 1000)}</div>` : ''}
        <div class="relay-actions">
          <button class="btn btn-primary ${isOn || !r.available ? 'hidden' : ''}" data-on="${r.id}">Turn on</button>
          <button class="btn btn-secondary ${!isOn ? 'hidden' : ''}" data-off="${r.id}">Turn off</button>
        </div>
      `;
      els.relayGrid.appendChild(card);
    });
  }

  // IPM treatment tiers — fixed step numbers so the escalation ladder
  // (cultural → organic → chemical-as-last-resort) always reads the same way,
  // even when a tier's list is empty and gets skipped.
  const TREATMENT_TIERS = [
    { key: 'cultural', step: '1', title: 'Cultural controls', tag: 'Try this first' },
    { key: 'organic',  step: '2', title: 'Organic options',   tag: 'If needed' },
    { key: 'chemical', step: '3', title: 'Chemical treatment', tag: 'Last resort' },
  ];

  // Cache the last-rendered suggestion so the panel isn't rebuilt (and its
  // entrance animation re-triggered) on every 5s poll.
  let treatmentSig = null;

  function tEl(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function renderPestTreatment(suggestion) {
    const panel = els.pestTreatment;
    if (!panel) return;
    const sig = JSON.stringify(suggestion || null);
    if (sig === treatmentSig) return;
    treatmentSig = sig;
    panel.replaceChildren();
    if (!suggestion) {
      panel.classList.add('hidden');
      return;
    }
    panel.classList.remove('hidden');

    const unmatched = suggestion.matched === false;
    panel.classList.toggle('is-generic', unmatched);

    // Heading: matched → the pest's name; unmatched → framed as general advice
    panel.appendChild(tEl('div', 'treatment-eyebrow', 'Suggested treatment'));
    const header = tEl('div', 'treatment-header');
    const title = tEl('h3', 'treatment-title', unmatched
      ? 'General guidance'
      : (suggestion.title
          || (suggestion.pest_class ? suggestion.pest_class.replace(/^./, c => c.toUpperCase()) : 'Treatment steps')));
    header.appendChild(title);
    if (suggestion.severity) {
      const known = ['low', 'moderate', 'high'].includes(suggestion.severity);
      header.appendChild(tEl('span',
        'treatment-severity ' + (known ? suggestion.severity : 'unknown'),
        (known ? suggestion.severity : String(suggestion.severity)) + ' severity'));
    }
    panel.appendChild(header);

    if (unmatched) {
      panel.appendChild(tEl('p', 'treatment-fallback-note',
        'This pest is not in the treatment guide yet, so the steps below are general advice rather than guidance for that specific pest.'));
    }
    if (suggestion.summary) {
      panel.appendChild(tEl('p', 'treatment-summary', suggestion.summary));
    }

    const tiersWrap = tEl('div', 'treatment-tiers');
    TREATMENT_TIERS.forEach(tier => {
      const items = Array.isArray(suggestion[tier.key]) ? suggestion[tier.key] : [];
      if (!items.length) return; // never render an empty heading
      const block = tEl('div', 'treatment-tier tier-' + tier.key);
      const heading = tEl('h4', 'treatment-tier-heading');
      heading.appendChild(tEl('span', 'tier-step', tier.step));
      heading.appendChild(tEl('span', 'tier-title', tier.title));
      heading.appendChild(tEl('span', 'tier-tag', tier.tag));
      block.appendChild(heading);
      const list = tEl('ul', 'tier-list');
      items.forEach(item => list.appendChild(tEl('li', null, String(item))));
      block.appendChild(list);
      tiersWrap.appendChild(block);
    });
    if (tiersWrap.children.length) panel.appendChild(tiersWrap);

    // Notes — always after the tiers, muted
    const notes = Array.isArray(suggestion.notes) ? suggestion.notes : [];
    if (notes.length) {
      const notesBlock = tEl('div', 'treatment-notes');
      const notesHeading = tEl('h4', 'treatment-tier-heading');
      notesHeading.appendChild(tEl('span', 'tier-title', 'Notes'));
      notesBlock.appendChild(notesHeading);
      const notesList = tEl('ul', 'tier-list');
      notes.forEach(item => notesList.appendChild(tEl('li', null, String(item))));
      notesBlock.appendChild(notesList);
      panel.appendChild(notesBlock);
    }

    // Disclaimer — always shown when present, small and muted
    if (suggestion.disclaimer) {
      panel.appendChild(tEl('p', 'treatment-disclaimer', suggestion.disclaimer));
    }
  }

  function renderPest(pest, pestThreshold) {
    const thresholdPct = pestThreshold != null ? Math.round(pestThreshold * 100) : 70;

    if (!pest || !pest.enabled || !pest.configured) {
      els.pestStatus.textContent = pest && pest.enabled
        ? 'Pest watch is on, but no detector model is loaded.'
        : 'Pest watch is turned off.';
      els.pestStatus.className = 'pest-status';
      els.pestStatus.style.display = 'block';
      els.pestDetails.classList.add('hidden');
      els.pestScanActions.classList.add('hidden');
      els.pestSimControls.style.display = 'none';
      renderPestTreatment(null);
      return;
    }

    els.pestStatus.style.display = 'block';
    els.pestDetails.classList.remove('hidden');
    els.pestScanActions.classList.remove('hidden');
    els.pestSimControls.style.display = 'flex';

    const status = pest.status || (pest.detected ? 'detected' : 'clear');
    const reason = pest.reason || 'ok';
    const busy = reason === 'scan_busy';
    const pestClass = pest.pest_class != null ? String(pest.pest_class) : null;
    const confidence = pest.confidence != null ? pest.confidence : null;
    const pct = confidence != null ? Math.round(confidence * 100) : null;
    const frames = Number.isInteger(pest.frames) ? pest.frames : null;
    const error = pest.error != null ? String(pest.error) : null;
    const timestamp = pest.timestamp != null ? String(pest.timestamp) : null;

    let outcome = '';
    const rows = [];

    if (status === 'detected') {
      els.pestStatus.className = 'pest-status pest-outcome--detected';
      outcome = `Pest detected — ${escapeHtml(pestClass || '—')} · ${pct != null ? pct + '%' : '—'} · ${fmtTime(timestamp)}`;
      rows.push({ label: 'Pest type', value: pestClass || '—' });
      if (pct != null) rows.push({ label: 'Surety', value: `${pct}%` });
      if (frames && frames > 1) rows.push({ label: 'Frames', value: `best of ${frames}` });
      rows.push({ label: 'Model', value: pest.model != null ? String(pest.model) : '—' });
      rows.push({ label: 'Last check', value: fmtTime(timestamp) });
    } else if (status === 'possible') {
      els.pestStatus.className = 'pest-status pest-outcome--possible';
      outcome = `Possible pest — ${escapeHtml(pestClass || '—')} · ${pct != null ? pct + '%' : '—'} — below ${thresholdPct}% confirmation, response not triggered`;
      rows.push({ label: 'Pest type', value: pestClass || '—' });
      if (pct != null) rows.push({ label: 'Surety', value: `${pct}%` });
      if (frames && frames > 1) rows.push({ label: 'Frames', value: `best of ${frames}` });
      rows.push({ label: 'Model', value: pest.model != null ? String(pest.model) : '—' });
      rows.push({ label: 'Last check', value: fmtTime(timestamp) });
    } else if (status === 'error') {
      els.pestStatus.className = 'pest-status pest-outcome--error';
      let msg;
      if (reason === 'capture_failed') msg = 'Camera returned no frame.';
      else if (reason === 'model_unavailable') msg = 'Pest model not loaded.';
      else if (reason === 'exception') msg = error || 'Scan failed.';
      else msg = 'Scan failed.';
      outcome = escapeHtml(msg);
      rows.push({ label: 'Model', value: pest.model != null ? String(pest.model) : '—' });
      if (timestamp) rows.push({ label: 'Last check', value: fmtTime(timestamp) });
    } else {
      // clear
      els.pestStatus.className = 'pest-status pest-outcome--clear';
      const across = frames && frames > 1 ? ` across ${frames} frame(s)` : '';
      outcome = `No pests found — best score ${pct != null ? pct + '%' : '—'}${across}`;
      rows.push({ label: 'Pest type', value: '—' });
      if (pct != null) rows.push({ label: 'Surety', value: `${pct}%` });
      rows.push({ label: 'Model', value: pest.model != null ? String(pest.model) : '—' });
      rows.push({ label: 'Last check', value: fmtTime(timestamp) });
    }

    let note = '';
    if (busy) {
      note = '<div class="pest-note pest-note--busy">A scan is already running — showing previous result.</div>';
    }

    els.pestStatus.innerHTML = outcome;
    els.pestDetails.innerHTML = note +
      rows.map(r => `<div class="pest-detail-row"><span class="label">${escapeHtml(r.label)}</span><span class="value">${escapeHtml(r.value)}</span></div>`).join('');

    renderPestTreatment(pest.suggestion || null);
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

  // Watering seconds → short farmer-readable duration ("12 min", "45s")
  function fmtMins(sec) {
    const s = Math.max(0, Math.round(sec || 0));
    if (s < 60) return `${s}s`;
    return `${Math.round(s / 60)} min`;
  }

  // Budget-row formatter — always whole minutes so used/max/remaining share
  // one unit ("0 min / 30 min"), never a mixed "0s / 30 min".
  function fmtMinsWhole(sec) {
    return `${Math.max(0, Math.round((sec || 0) / 60))} min`;
  }

  // Closed-loop watering: fault banner, daily budget, settle-window hint.
  // Older snapshots may not carry data.watering — render nothing then.
  // Only textContent/classList updates here: no DOM rebuilds, so the 5s
  // poll never re-triggers the banner animation unless the fault flips.
  function renderWatering(watering) {
    const faultEl = els.wateringFault;
    if (!faultEl) return;

    if (!watering) {
      faultEl.classList.add('hidden');
      els.wateringUsage.classList.add('hidden');
      els.wateringCooldown.classList.add('hidden');
      return;
    }

    // 1. Fault banner — the loudest signal on the card, but calm copy.
    if (watering.fault) {
      els.wateringFaultReason.textContent =
        watering.fault_reason ||
        'Watering has stopped and needs attention.';
      faultEl.classList.remove('hidden');
    } else {
      faultEl.classList.add('hidden');
    }

    // 2. Daily budget — sibling of the pest card's "Monthly pest responses" row.
    const budget = watering.budget;
    if (budget && budget.max_seconds != null) {
      els.wateringUsed.textContent = fmtMinsWhole(budget.used_seconds);
      els.wateringMax.textContent = fmtMinsWhole(budget.max_seconds);
      const rem = budget.remaining_seconds;
      els.wateringRemaining.textContent = `Remaining today: ${fmtMinsWhole(rem)}`;
      els.wateringRemaining.className = 'remaining' +
        (budget.blocked || rem <= 0 ? ' critical' : rem <= budget.max_seconds * 0.1 ? ' low' : '');
      els.wateringUsage.classList.remove('hidden');
    } else {
      els.wateringUsage.classList.add('hidden');
    }

    // 3. Settle window — muted one-liner, only while counting down.
    const cd = watering.cooldown_remaining_seconds;
    if (cd > 0) {
      els.wateringCooldown.textContent = `Waiting ${fmtMins(cd)} before the next watering`;
      els.wateringCooldown.classList.remove('hidden');
    } else {
      els.wateringCooldown.classList.add('hidden');
    }
  }

  function renderGarden(data) {
    const garden = data.garden;
    if (!garden) return;
    const area = garden.area_sq_m;
    const target = garden.water_target_mm_per_day;
    const useArea = data.use_area_based_budget;

    if (els.gardenArea) els.gardenArea.textContent = area.toFixed(1);
    if (els.gardenAreaState) {
      els.gardenAreaState.textContent = area > 0 ? 'Monitored' : 'Not set';
      els.gardenAreaState.className = 'state ' + (area > 0 ? 'state-good' : 'state-unknown');
    }
    if (els.gardenTarget) els.gardenTarget.textContent = target.toFixed(1);
    if (els.gardenTargetTh) els.gardenTargetTh.textContent = `Target depth per day`;
    if (els.gardenBudgetMode) {
      els.gardenBudgetMode.textContent = useArea ? 'Area-aware' : 'Fixed';
      els.gardenBudgetMode.className = 'value ' + (useArea ? 'state-good' : 'state-unknown');
    }
    if (els.gardenBudgetState) {
      els.gardenBudgetState.textContent = useArea
        ? 'Budget scales with garden area'
        : 'Using fixed daily budget';
      els.gardenBudgetState.className = 'state ' + (useArea ? 'state-good' : 'state-unknown');
    }
    if (els.gardenMeta) {
      els.gardenMeta.textContent = useArea
        ? `Area-based budget active (${area.toFixed(1)} m² × ${target.toFixed(1)} mm/day)`
        : 'Area-based budget is off — using fixed daily cap';
    }
  }

  function renderSystem(data) {
    els.sysUptime.textContent = uptimeStr(data.system.uptime_seconds);
    // DB
    els.sysDb.innerHTML = '';
    els.sysDb.appendChild(statusDot(data.database.ok));
    els.sysDb.append(' ');
    els.sysDb.appendChild(stateSpan(data.database.ok ? 'Connected' : `Error: ${data.database.error || 'unknown'}`, data.database.ok ? 'good' : 'bad'));

    // NPK sensor
    const npk = data.sensors.npk;
    const npkOk = !npk.error && npk.last_success;
    els.sysNpk.innerHTML = '';
    els.sysNpk.appendChild(statusDot(npkOk));
    els.sysNpk.append(' ');
    els.sysNpk.appendChild(stateSpan(npkOk ? 'OK' : `No reading: ${npk.error || 'unknown'}`, npkOk ? 'good' : 'bad'));

    // Moisture sensor
    const moist = data.sensors.moisture;
    const moistOk = !moist.error && moist.last_success;
    els.sysMoist.innerHTML = '';
    els.sysMoist.appendChild(statusDot(moistOk));
    els.sysMoist.append(' ');
    els.sysMoist.appendChild(stateSpan(moistOk ? 'OK' : `No reading: ${moist.error || 'unknown'}`, moistOk ? 'good' : 'bad'));

    // Camera
    els.sysCam.innerHTML = '';
    const cam = data.camera;
    const camLink = document.createElement('a');
    camLink.href = '/camera';
    camLink.className = 'nav-tab-link';
    camLink.textContent = 'Open camera view';
    if (cam.configured) {
      els.sysCam.appendChild(statusDot(!cam.error));
      els.sysCam.append(' ');
      els.sysCam.appendChild(stateSpan(cam.error ? `Error: ${cam.error}` : 'Connected', !cam.error ? 'good' : 'bad'));
      els.sysCam.append(' ');
      els.sysCam.appendChild(camLink);
    } else {
      els.sysCam.appendChild(statusDot(false));
      els.sysCam.append(' ');
      els.sysCam.appendChild(stateSpan(cam.error ? `Not connected: ${cam.error}` : 'Camera not installed', 'bad'));
      els.sysCam.append(' ');
      els.sysCam.appendChild(camLink);
    }

    // Pest model
    els.sysPest.innerHTML = '';
    const pest = data.pest;
    if (pest.enabled && pest.configured) {
      els.sysPest.appendChild(statusDot(!pest.error));
      els.sysPest.append(' ');
      els.sysPest.appendChild(stateSpan(pest.error ? `Error: ${pest.error}` : `Loaded (${pest.model})`, !pest.error ? 'good' : 'bad'));
    } else {
      els.sysPest.appendChild(statusDot(false));
      els.sysPest.append(' ');
      els.sysPest.appendChild(stateSpan('Not configured', 'unknown'));
    }

    // Automation
    els.sysAuto.innerHTML = '';
    const auto = data.automation;
    if (auto.running) {
      els.sysAuto.appendChild(statusDot(!auto.paused));
      els.sysAuto.append(' ');
      els.sysAuto.appendChild(stateSpan(auto.paused ? 'Paused' : 'Running', !auto.paused ? 'good' : 'warn'));
    } else {
      els.sysAuto.appendChild(statusDot(false));
      els.sysAuto.append(' ');
      els.sysAuto.appendChild(stateSpan('Stopped', 'bad'));
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

  function renderRecommendation(rec, relays) {
    if (!els.recStatus) return;

    const activeDiv = els.recStatus.querySelector('.rec-active');
    const emptyDiv = els.recStatus.querySelector('.rec-empty');

    if (!rec || !rec.recommended) {
      if (activeDiv) activeDiv.classList.add('hidden');
      if (emptyDiv) emptyDiv.classList.remove('hidden');
      return;
    }

    if (emptyDiv) emptyDiv.classList.add('hidden');
    if (activeDiv) activeDiv.classList.remove('hidden');

    // Priority badge
    if (els.recPriority) {
      els.recPriority.textContent = rec.priority ? rec.priority.toUpperCase() : '—';
      els.recPriority.className = 'rec-priority ' + (rec.priority || '');
    }
    // Model
    if (els.recModel) {
      els.recModel.textContent = rec.model ? `(${rec.model})` : '';
    }
    // Fertilizer name
    if (els.recFertilizerName) {
      els.recFertilizerName.textContent = rec.fertilizer_name || '—';
    }
    // Dosage
    if (els.recDosage) {
      els.recDosage.textContent = rec.dosage_g_per_10L != null ? rec.dosage_g_per_10L.toFixed(1) : '—';
    }
    // Reasoning
    if (els.recReasoning) {
      els.recReasoning.textContent = rec.reasoning || '—';
    }
    // Deficits
    if (els.recDeficits && rec.deficits) {
      const deficitItems = Object.entries(rec.deficits)
        .filter(([, v]) => v !== 0)
        .map(([k, v]) => {
          const label = k.charAt(0).toUpperCase() + k.slice(1);
          const sign = v > 0 ? '↓' : '↑';
          return `<span class="deficit-item ${v > 0 ? 'low' : 'high'}">${sign} ${label}: ${Math.abs(v).toFixed(1)}%</span>`;
        });
      els.recDeficits.innerHTML = deficitItems.length
        ? `<strong>Low nutrients:</strong> ${deficitItems.join(' ')}`
        : '<strong>Low nutrients:</strong> None';
    }
    // Duration
    if (els.recDur) {
      const r1 = relays?.find(r => r.id === 1);
      els.recDur.textContent = r1?.activation_duration_seconds || '—';
    }
    // Meta
    if (els.recMeta) {
      els.recMeta.textContent = `Suggested: ${fmtTime(rec.timestamp)}`;
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
  // Notifications — bell dropdown fed by the same data.alerts poll
  // ------------------------------------------------------------------

  function loadNotifKeys(storageKey) {
    try {
      const raw = JSON.parse(localStorage.getItem(storageKey) || '[]');
      return Array.isArray(raw) ? raw.map(String).slice(-NOTIF_CAP) : [];
    } catch (_) {
      return []; // localStorage unavailable (private mode, etc.)
    }
  }

  function saveNotifKeys(storageKey, set) {
    try {
      localStorage.setItem(storageKey, JSON.stringify([...set].slice(-NOTIF_CAP)));
    } catch (_) { /* storage unavailable — state still works this session */ }
  }

  // Oldest-first insertion order; drop from the front once past the cap.
  function trimNotifSet(set) {
    while (set.size > NOTIF_CAP) {
      set.delete(set.values().next().value);
    }
  }

  // Stable per-alert key: id plus its own identity, so a backend restart
  // (ids restart at 1) can never make a new alert inherit an old read state.
  function notifKey(a) {
    if (!a) return '';
    const stamp = `${a.timestamp || ''}|${a.source || ''}|${a.message || ''}`;
    return a.id != null ? `id:${a.id}|${stamp}` : stamp;
  }

  const NOTIF_SOURCES = {
    moisture: 'Soil moisture',
    watering: 'Watering',
    npk: 'Fertilizer',
    npk_recommendation: 'Fertilizer recommendation',
    pest_detection: 'Pest detection',
    pest_treatment: 'Pesticide recommendation',
  };

  function notifSourceLabel(src) {
    if (!src) return '';
    return NOTIF_SOURCES[src] || String(src).replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
  }

  function fmtRelTime(iso) {
    if (!iso) return '';
    const t = new Date(iso).getTime();
    if (Number.isNaN(t)) return fmtTime(iso);
    const diff = Date.now() - t;
    if (diff < 60000) return 'just now';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
    if (diff < 604800000) return `${Math.floor(diff / 86400000)}d ago`;
    return fmtTime(iso);
  }

  function setNotifOpen(open) {
    if (!els.notifPanel) return;
    els.notifPanel.classList.toggle('hidden', !open);
    els.notifBell?.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  function buildNotifItem(a) {
    const key = notifKey(a);
    const level = String(a.level || 'info').toLowerCase();
    const isRead = notifRead.has(key);

    const item = document.createElement('div');
    item.className = `notif-item level-${level} ${isRead ? 'read' : 'unread'}`;

    const main = document.createElement('button');
    main.type = 'button';
    main.className = 'notif-item-main';
    main.dataset.notifKey = key;

    const dot = document.createElement('span');
    dot.className = 'notif-dot';
    dot.setAttribute('aria-hidden', 'true');

    const body = document.createElement('span');
    body.className = 'notif-body';
    const msg = document.createElement('span');
    msg.className = 'notif-msg';
    msg.textContent = a.message || '';
    const meta = document.createElement('span');
    meta.className = 'notif-meta';
    meta.textContent = [notifSourceLabel(a.source), fmtRelTime(a.timestamp)].filter(Boolean).join(' • ');
    body.append(msg, meta);
    main.append(dot, body);

    const dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.className = 'notif-dismiss';
    dismiss.dataset.notifDismiss = key;
    dismiss.setAttribute('aria-label', 'Dismiss notification');
    dismiss.textContent = '×';

    item.append(main, dismiss);
    return item;
  }

  function buildNotifList(shown) {
    const frag = document.createDocumentFragment();
    if (!shown.length) {
      const empty = document.createElement('div');
      empty.className = 'notif-empty';
      empty.textContent = 'No new notifications';
      frag.appendChild(empty);
    } else {
      shown.forEach(a => frag.appendChild(buildNotifItem(a)));
    }
    els.notifList.replaceChildren(frag);
  }

  function updateNotifBadge(shown) {
    if (!els.notifBadge) return;
    const unread = shown.filter(a => !notifRead.has(notifKey(a)));
    const count = unread.length;
    if (count === 0) {
      els.notifBadge.classList.add('hidden');
      els.notifBell?.setAttribute('aria-label', 'Notifications');
      return;
    }
    // Tint the badge by the most serious unread level
    const rank = { info: 0, warning: 1, error: 2, critical: 2 };
    let top = 0;
    unread.forEach(a => {
      const r = rank[String(a.level || 'info').toLowerCase()];
      if (r !== undefined && r > top) top = r;
    });
    els.notifBadge.className = 'notif-badge ' + ['level-info', 'level-warning', 'level-error'][top];
    els.notifBadge.textContent = count > 99 ? '99+' : String(count);
    els.notifBell?.setAttribute('aria-label', `Notifications, ${count} unread`);
  }

  // Rebuild only when the visible set, read state, or minute changes —
  // keeps an open panel stable across the 5s poll instead of flickering.
  function renderNotifications(alerts) {
    const list = Array.isArray(alerts) ? alerts : [];
    lastNotifAlerts = list;
    lastNotifShown = list.filter(a => !notifDismissed.has(notifKey(a)));

    const sig = Math.floor(Date.now() / 60000) + JSON.stringify(lastNotifShown.map(a => [
      notifKey(a), a.level || '', a.source || '', a.message || '', a.timestamp || '',
      notifRead.has(notifKey(a)),
    ]));
    if (sig !== notifSig) {
      notifSig = sig;
      const active = document.activeElement;
      const refocus = (active && active.dataset && (active.dataset.notifKey || active.dataset.notifDismiss)) || null;
      buildNotifList(lastNotifShown);
      if (refocus) restoreNotifFocus(refocus);
    }

    updateNotifBadge(lastNotifShown);
    const allRead = lastNotifShown.length > 0 &&
      lastNotifShown.every(a => notifRead.has(notifKey(a)));
    if (els.notifMarkAll) els.notifMarkAll.disabled = lastNotifShown.length === 0 || allRead;
    if (els.notifClear) els.notifClear.disabled = lastNotifShown.length === 0;
  }

  // Keep keyboard focus inside the panel across a list rebuild.
  function restoreNotifFocus(key) {
    const candidates = els.notifList.querySelectorAll('[data-notif-key],[data-notif-dismiss]');
    for (const el of candidates) {
      if ((el.dataset.notifKey || el.dataset.notifDismiss) === key) {
        el.focus();
        return;
      }
    }
    const first = els.notifList.querySelector('[data-notif-key]');
    (first || els.notifBell)?.focus();
  }

  function onNotifListClick(e) {
    const dismissBtn = e.target.closest('[data-notif-dismiss]');
    if (dismissBtn) {
      notifDismissed.add(dismissBtn.dataset.notifDismiss);
      trimNotifSet(notifDismissed);
      saveNotifKeys(NOTIF_DISMISSED_KEY, notifDismissed);
      renderNotifications(lastNotifAlerts);
      return;
    }
    const main = e.target.closest('[data-notif-key]');
    if (main && !notifRead.has(main.dataset.notifKey)) {
      notifRead.add(main.dataset.notifKey);
      trimNotifSet(notifRead);
      saveNotifKeys(NOTIF_READ_KEY, notifRead);
      renderNotifications(lastNotifAlerts);
    }
  }

  function markAllNotifsRead() {
    lastNotifAlerts.forEach(a => notifRead.add(notifKey(a)));
    trimNotifSet(notifRead);
    saveNotifKeys(NOTIF_READ_KEY, notifRead);
    renderNotifications(lastNotifAlerts);
  }

  function clearNotifs() {
    lastNotifAlerts.forEach(a => notifDismissed.add(notifKey(a)));
    trimNotifSet(notifDismissed);
    saveNotifKeys(NOTIF_DISMISSED_KEY, notifDismissed);
    renderNotifications(lastNotifAlerts);
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

    // Refresh collapsible card heights after log content updates
    refreshCollapsibleCards();
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
      ['Time', 'Nitrogen (mg/kg)', 'Phosphorus (mg/kg)', 'Potassium (mg/kg)', 'Moisture (%)'],
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
      ['Time', 'Pump / sprayer', 'Trigger', 'Duration', 'Source'],
      html, 'No pump or sprayer use logged yet.',
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
      <td><span class="log-badge ${r.detected ? 'detected' : 'none'}">${r.detected ? 'Pest found' : 'None'}</span></td>
      <td>${r.pest_class ? escapeHtml(r.pest_class) : '—'}</td>
      <td class="num">${r.confidence != null ? (r.confidence * 100).toFixed(0) + '%' : '—'}</td>
      <td>${r.model ? escapeHtml(r.model) : '—'}</td>
    </tr>`);
    els.logPanels.detections.innerHTML = logTable(
      ['Time', 'Result', 'Pest type', 'Surety', 'Model'],
      html, 'No pest checks logged yet.',
    );
  }

  function renderLogError(panel, message) {
    panel.innerHTML = `<p class="log-error">${escapeHtml(message)}</p>`;
  }

  function switchView(view) {
    if (!view) return;
    activeView = view;
    els.navTabs.forEach(t => t.classList.toggle('active', t.dataset.view === view));
    document.getElementById('view-overview').classList.toggle('hidden', view !== 'overview');
    document.getElementById('view-logs').classList.toggle('hidden', view !== 'logs');
    document.getElementById('view-settings').classList.toggle('hidden', view !== 'settings');
    if (typeof updateBottomNav === 'function') updateBottomNav(view);
    // Keep the URL in sync so deep links (/logs, /settings) survive refresh
    // and back/forward navigation.
    const path = { overview: '/', logs: '/logs', settings: '/settings' }[view];
    if (path && window.location.pathname !== path) {
      history.replaceState(null, '', path);
    }
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
      header.className = 'card-header group-header';
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
      list.className = 'setting-list group-body';
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
    // Re-initialize collapsibles for newly created settings groups
    initCollapsibleCards();
    refreshCollapsibleCards();
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
    if (!confirm('Restart the server now? All pumps switch off for about 30 seconds.')) return;
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
    if (!confirm('Switch every pump off right now? This stops watering and spraying immediately.')) return;
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

  async function resetWateringFault() {
    const btn = els.btnWateringReset;
    if (btn) btn.disabled = true;
    try {
      const res = await postJson(`${API}/watering/reset`);
      addAlert(res.ok ? 'info' : 'error', 'Dashboard',
        res.message || (res.ok ? 'Watering fault cleared.' : 'Could not clear the watering fault.'));
      if (res.ok) poll(); // refresh the moisture card promptly
    } catch (e) {
      addAlert('error', 'Dashboard', e.message);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function forceNpkRead() {
    const btn = els.btnNpkReadNow;
    if (btn) btn.disabled = true;
    try {
      const res = await postJson(`${API}/npk/read`);
      addAlert(res.ok ? 'info' : 'error', 'Dashboard',
        res.message || (res.ok ? 'NPK reading started.' : 'Could not start NPK reading.'));
      if (res.ok) poll(); // refresh the NPK card promptly
    } catch (e) {
      addAlert('error', 'Dashboard', e.message);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function simulatePest() {
    try {
      const res = await postJson(`${API}/pest/simulate`, {
        detected: true,
        pest_class: els.simPestClass.value,
        confidence: parseFloat(els.simConfidence.value),
      });
      if (res.ok) addAlert('info', 'Dashboard', `Simulated ${res.pest_class} (${(res.confidence*100).toFixed(0)}% sure)`);
      else addAlert('error', 'Dashboard', res.message);
    } catch (e) { addAlert('error', 'Dashboard', e.message); }
  }

  async function forcePestMonitor(mode) {
    const btn = mode === 'stepper' ? els.btnScanStepper : els.btnScanCamera;
    if (btn) btn.disabled = true;
    try {
      const res = await postJson(`${API}/pest/scan`, { mode });
      addAlert(res.ok ? 'info' : 'error', 'Dashboard',
        res.message || (res.ok ? 'Pest check started.' : 'Could not start the pest check.'));
      if (res.ok) poll(); // refresh the pest card promptly
    } catch (e) {
      addAlert('error', 'Dashboard', e.message);
    } finally {
      // The request only schedules work; re-enable after a short delay.
      setTimeout(() => { if (btn) btn.disabled = false; }, 5000);
    }
  }

  async function runSystemTest() {
    const btn = els.btnSystemTest;
    const resultsDiv = document.getElementById('system-test-results');
    if (!btn || !resultsDiv) return;

    // Get selected test options
    const options = {
      relays: document.getElementById('test-relays')?.checked ?? true,
      sensors: document.getElementById('test-sensors')?.checked ?? true,
      camera: document.getElementById('test-camera')?.checked ?? true,
      stepper: document.getElementById('test-stepper')?.checked ?? true,
    };

    // UI state
    btn.disabled = true;
    btn.textContent = 'Running test...';
    resultsDiv.classList.remove('hidden');
    resultsDiv.innerHTML = '<p class="test-running">Running test... this may take up to 30 seconds.</p>';

    try {
      const res = await postJson(`${API}/system/test`, options);
      resultsDiv.innerHTML = renderTestResults(res);
    } catch (e) {
      resultsDiv.innerHTML = `<p class="test-error">Test failed: ${escapeHtml(e.message)}</p>`;
    } finally {
      btn.disabled = false;
      btn.textContent = 'Run test';
      refreshCollapsibleCards();
    }
  }

  function renderTestResults(data) {
    if (!data) return '<p class="test-error">No results returned.</p>';

    let html = `<div class="test-summary ${data.ok ? 'success' : 'failure'}">
      <strong>Overall: ${data.ok ? 'All passed' : 'Some checks failed'}</strong>
      <span class="test-time">${data.timestamp || ''}</span>
    </div>`;

    const tests = data.tests || {};
    for (const [category, results] of Object.entries(tests)) {
      if (!results) continue;
      const items = Array.isArray(results) ? results : [results];
      html += `<div class="test-category"><h4>${category.charAt(0).toUpperCase() + category.slice(1)}</h4><ul>`;
      for (const r of items) {
        if (!r) continue;
        const ok = r.ok === true;
        const relayId = r.relay_id ? ` Pump ${r.relay_id}` : '';
        const msg = escapeHtml(r.message || 'No message');
        html += `<li class="${ok ? 'test-pass' : 'test-fail'}">
          <span class="test-icon">${ok ? '✓' : '✗'}</span>
          <span class="test-name">${relayId ? relayId + ':' : ''}</span>
          <span class="test-msg">${msg}</span>
        </li>`;
      }
      html += '</ul></div>';
    }

    return html;
  }

  async function runStepperTest() {
    const btn = els.btnStepperTest;
    const resultDiv = els.stepperTestResult;
    if (!btn || !resultDiv) return;

    const steps = parseInt(els.stepperSteps?.value, 10) || 512;
    const delay = parseFloat(els.stepperDelay?.value) || 0.003;

    btn.disabled = true;
    btn.textContent = 'Testing...';
    resultDiv.classList.remove('hidden');
    resultDiv.innerHTML = '<p class="test-running">Testing camera arm...</p>';

    try {
      const res = await postJson(`${API}/stepper/test`, { steps, delay });
      if (res.ok) {
        resultDiv.innerHTML = `<p class="test-success">✓ ${escapeHtml(res.message)}</p>`;
      } else {
        resultDiv.innerHTML = `<p class="test-error">✗ ${escapeHtml(res.error || 'Test failed')}</p>`;
      }
    } catch (e) {
      resultDiv.innerHTML = `<p class="test-error">✗ ${escapeHtml(e.message)}</p>`;
    } finally {
      btn.disabled = false;
      btn.textContent = 'Move camera arm out and back';
      refreshCollapsibleCards();
    }
  }

  // Event listeners
  els.btnR1.addEventListener('click', () => activateRelay(1));
  els.btnApplyFertilizer?.addEventListener('click', () => activateRelay(1));
  els.btnEmergency.addEventListener('click', emergencyStop);
  els.btnPause.addEventListener('click', pauseAutomation);
  els.btnResume.addEventListener('click', resumeAutomation);
  els.btnSimPest.addEventListener('click', simulatePest);
  els.btnWateringReset?.addEventListener('click', resetWateringFault);
  els.btnNpkReadNow?.addEventListener('click', forceNpkRead);

  // Live refill preview: "That's ≈ N uses at X L/h × Y s cycle"
  function updateRefillPreview(litersInput, previewUsesEl, previewRateEl, tank) {
    const liters = parseFloat(litersInput.value || '0');
    const lph = tank.pump_lph || 0;
    const cycleSec = tank.dose_cycle_seconds != null ? tank.dose_cycle_seconds : null;
    let doseL, rateText;
    if (cycleSec != null && lph > 0) {
      doseL = (lph * cycleSec) / 3600;
      rateText = lph.toFixed(0) + ' L/h × ' + cycleSec.toFixed(0) + ' s cycle';
    } else {
      const perCycleMl = tank.fert_ml_per_cycle != null ? tank.fert_ml_per_cycle
        : (tank.ml_per_activation != null ? tank.ml_per_activation : 0);
      doseL = perCycleMl / 1000;
      rateText = perCycleMl > 0 ? perCycleMl.toFixed(0) + ' mL per use (server-set)' : '—';
    }
    if (liters > 0 && doseL > 0) {
      previewUsesEl.textContent = (liters / doseL).toFixed(1);
      previewRateEl.textContent = rateText;
    } else {
      previewUsesEl.textContent = '—';
      previewRateEl.textContent = '—';
    }
    return liters > 0;
  }

  // Tanks handlers
  const btnFertRef = document.getElementById('btn-fert-refill');
  const fertLiters = document.getElementById('fert-liters');
  if (btnFertRef && fertLiters) {
    fertLiters.addEventListener('input', () => {
      const valid = updateRefillPreview(fertLiters,
        document.getElementById('fert-preview-uses'),
        document.getElementById('fert-preview-rate'),
        lastTanks.fertilizer);
      btnFertRef.disabled = !valid;
    });
    btnFertRef.addEventListener('click', async () => {
      const liters = parseFloat(fertLiters.value || '0');
      if (liters <= 0) return;
      const body = {
        fertilizer_type: document.getElementById('fert-type').value,
        npk_n: parseFloat(document.getElementById('fert-n').value || '0'),
        npk_p: parseFloat(document.getElementById('fert-p').value || '0'),
        npk_k: parseFloat(document.getElementById('fert-k').value || '0'),
        total_liters: liters,
        soak_days: parseFloat(document.getElementById('fert-soak').value || '7'),
        mode: document.getElementById('fert-mode').value,
      };
      const res = await fetch(API + '/tanks/fertilizer/refill', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const j = await res.json().catch(() => ({ ok: false }));
      alert(j.message || (j.ok ? 'Tank marked refilled' : 'Failed'));
    });
  }
  const btnPestRef = document.getElementById('btn-pest-refill');
  const pestLiters = document.getElementById('pest-liters');
  if (btnPestRef && pestLiters) {
    pestLiters.addEventListener('input', () => {
      const valid = updateRefillPreview(pestLiters,
        document.getElementById('pest-preview-uses'),
        document.getElementById('pest-preview-rate'),
        lastTanks.pesticide);
      btnPestRef.disabled = !valid;
    });
    btnPestRef.addEventListener('click', async () => {
      const liters = parseFloat(pestLiters.value || '0');
      if (liters <= 0) return;
      const body = {
        pesticide_type: document.getElementById('pest-type').value,
        total_liters: liters,
        soak_days: parseFloat(document.getElementById('pest-soak').value || '0'),
      };
      const res = await fetch(API + '/tanks/pesticide/refill', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const j = await res.json().catch(() => ({ ok: false }));
      alert(j.message || (j.ok ? 'Tank marked refilled' : 'Failed'));
    });
  }
  document.getElementById('btn-fert-auto-on')?.addEventListener('click', () => tankAuto('fertilizer', true));
  document.getElementById('btn-fert-auto-off')?.addEventListener('click', () => tankAuto('fertilizer', false));
  document.getElementById('btn-pest-auto-on')?.addEventListener('click', () => tankAuto('pesticide', true));
  document.getElementById('btn-pest-auto-off')?.addEventListener('click', () => tankAuto('pesticide', false));

  async function tankAuto(tank, enabled) {
    const res = await fetch(API + '/tanks/' + tank + '/auto', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: enabled }),
    });
    const j = await res.json().catch(() => ({}));
    alert(j.message || 'Done');
  }

  els.btnScanStepper?.addEventListener('click', () => forcePestMonitor('stepper'));
  els.btnScanCamera?.addEventListener('click', () => forcePestMonitor('camera'));
  els.btnSaveSettings.addEventListener('click', saveSettings);
  els.btnRestartServer.addEventListener('click', restartServer);
  els.btnSystemTest?.addEventListener('click', runSystemTest);
  els.btnStepperTest?.addEventListener('click', runStepperTest);
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

  // Notification bell — open/close, item actions (mark read / dismiss)
  els.notifBell?.addEventListener('click', (e) => {
    e.stopPropagation();
    setNotifOpen(els.notifPanel.classList.contains('hidden'));
  });
  document.addEventListener('click', (e) => {
    if (!els.notifPanel || els.notifPanel.classList.contains('hidden')) return;
    if (!e.target.closest('.notif-wrap')) setNotifOpen(false);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && els.notifPanel && !els.notifPanel.classList.contains('hidden')) {
      setNotifOpen(false);
      els.notifBell?.focus();
    }
  });
  els.notifList?.addEventListener('click', onNotifListClick);
  els.notifMarkAll?.addEventListener('click', markAllNotifsRead);
  els.notifClear?.addEventListener('click', clearNotifs);
  els.navTabs.forEach(t => t.addEventListener('click', () => switchView(t.dataset.view)));
  els.subTabs.forEach(t => t.addEventListener('click', () => switchLogTab(t.dataset.log)));
  els.logsRefresh.addEventListener('click', loadLogs);
  els.logsLimit.addEventListener('change', loadLogs);

  // Mobile Navigation
  const hamburgerBtn = document.getElementById('hamburger-btn');
  const mobileNavDrawer = document.getElementById('mobile-nav-drawer');
  const mobileNavOverlay = document.getElementById('mobile-nav-overlay');
  const drawerClose = document.getElementById('drawer-close');

  function openMobileNav() {
    mobileNavDrawer.classList.add('open');
    mobileNavOverlay.classList.add('open');
    hamburgerBtn.setAttribute('aria-expanded', 'true');
    document.body.style.overflow = 'hidden';
  }

  function closeMobileNav() {
    mobileNavDrawer.classList.remove('open');
    mobileNavOverlay.classList.remove('open');
    hamburgerBtn.setAttribute('aria-expanded', 'false');
    document.body.style.overflow = '';
  }

  if (hamburgerBtn) {
    hamburgerBtn.addEventListener('click', openMobileNav);
    drawerClose.addEventListener('click', closeMobileNav);
    mobileNavOverlay.addEventListener('click', closeMobileNav);

    // Close drawer on escape key
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && mobileNavDrawer.classList.contains('open')) {
        closeMobileNav();
      }
    });

    // Handle mobile nav item clicks
    document.querySelectorAll('.mobile-nav-item').forEach(item => {
      item.addEventListener('click', (e) => {
        e.preventDefault();
        const view = item.dataset.view;
        if (view) {
          switchView(view);
        } else {
          window.location.href = item.getAttribute('href');
        }
        closeMobileNav();
      });
    });
  }

  // Bottom Navigation
  function updateBottomNav(view) {
    document.querySelectorAll('.bottom-nav-item').forEach(item => {
      item.classList.toggle('active', item.dataset.view === view);
    });
    document.querySelectorAll('.mobile-nav-item').forEach(item => {
      item.classList.toggle('active', item.dataset.view === view);
    });
  }

  document.querySelectorAll('.bottom-nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      const view = item.dataset.view;
      if (view) {
        switchView(view);
      } else {
        window.location.href = item.getAttribute('href');
      }
    });
  });

  // Collapsible Cards & Settings Groups
  function refreshCollapsibleCards() {
    document.querySelectorAll('.card.collapsible:not(.collapsed) .card-body').forEach(body => {
      body.style.maxHeight = body.scrollHeight + 'px';
    });
    document.querySelectorAll('.settings-group:not(.collapsed) .group-body').forEach(body => {
      body.style.maxHeight = body.scrollHeight + 'px';
    });
  }

  function initCollapsibleCards() {
    // Card collapsibles
    document.querySelectorAll('.card.collapsible').forEach(card => {
      const header = card.querySelector('.card-header');
      const body = card.querySelector('.card-body');
      if (!header || !body) return;

      body.style.maxHeight = body.scrollHeight + 'px';

      header.addEventListener('click', (e) => {
        if (e.target.closest('button')) return;
        card.classList.toggle('collapsed');
        body.style.maxHeight = card.classList.contains('collapsed') ? '0' : body.scrollHeight + 'px';
      });
    });

    // Settings group collapsibles
    document.querySelectorAll('.settings-group').forEach(group => {
      const header = group.querySelector('.group-header');
      const body = group.querySelector('.group-body');
      if (!header || !body) return;

      // Ensure max-height is set for transition
      body.style.maxHeight = body.scrollHeight + 'px';

      header.addEventListener('click', (e) => {
        // Don't toggle if clicking interactive elements inside header
        if (e.target.closest('button, input, select, label, a')) return;
        group.classList.toggle('collapsed');
        body.style.maxHeight = group.classList.contains('collapsed') ? '0' : body.scrollHeight + 'px';
      });
    });
  }

  initCollapsibleCards();

  // Update collapsible card max-heights on resize
  let resizeTimeout;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(() => {
      refreshCollapsibleCards();
    }, 150);
  });

  // Start — open the view matching the URL (deep links from other pages,
  // e.g. /logs and /settings, must land on the right tab, not Overview).
  const initialPath = window.location.pathname.replace(/\/+$/, '') || '/';
  const initialView = { '/logs': 'logs', '/settings': 'settings' }[initialPath];
  if (initialView) switchView(initialView);

  poll();
  setInterval(poll, POLL_MS);
})();
