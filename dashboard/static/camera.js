/* VeggieCare Camera view — vanilla JS, no dependencies. */

(() => {
  'use strict';

  const STREAM_URL = '/api/camera/stream';
  const STATUS_URL = '/api/camera/status';
  const SNAPSHOT_URL = '/api/camera/snapshot';
  const PROBE_URL = '/api/camera/probe';
  const POLL_MS = 10000;
  const MAX_RETRIES = 4;

  const $ = (id) => document.getElementById(id);

  const els = {
    stream: $('cam-stream'),
    error: $('cam-error'),
    errorTitle: $('cam-error-title'),
    errorReason: $('cam-error-reason'),
    badge: $('cam-conn-badge'),
    toggle: $('cam-toggle'),
    snapshot: $('cam-snapshot'),
    retry: $('cam-retry'),
    meta: $('cam-meta'),
    devStatus: $('cam-dev-status'),
    devBackend: $('cam-dev-backend'),
    devPath: $('cam-dev-path'),
    devRes: $('cam-dev-res'),
    devFps: $('cam-dev-fps'),
    devCv2: $('cam-dev-cv2'),
    devMessage: $('cam-dev-message'),
    devices: $('cam-devices'),
    probe: $('cam-probe'),
    probeNote: $('cam-probe-note'),
  };

  let streaming = false;
  let connected = false;
  let retries = 0;
  let retryTimer = null;
  let pollTimer = null;

  function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function showError(title, reason) {
    if (els.errorTitle) els.errorTitle.textContent = title;
    if (els.errorReason) els.errorReason.textContent = reason || '';
    if (els.error) els.error.classList.remove('hidden');
    if (els.stream) els.stream.classList.add('hidden');
  }

  function hideError() {
    if (els.error) els.error.classList.add('hidden');
  }

  function setBadge(text, ok) {
    if (!els.badge) return;
    els.badge.textContent = text;
    els.badge.className = 'badge ' + (ok ? 'badge-ok' : 'badge-bad');
  }

  // ------------------------------------------------------------------
  // Streaming
  // ------------------------------------------------------------------

  function stopStream() {
    streaming = false;
    if (retryTimer) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
    // Clearing src aborts the multipart request server-side, which stops
    // the Pi from encoding frames for a viewer that has left.
    if (els.stream) {
      els.stream.removeAttribute('src');
      els.stream.classList.add('hidden');
    }
    if (els.toggle) els.toggle.textContent = 'Start';
    setBadge(connected ? 'Connected' : 'Stopped', connected);
  }

  function scheduleReconnect() {
    if (!streaming) return;
    const delay = Math.min(2000 * Math.pow(2, retries), 15000);
    retries += 1;
    if (retries > MAX_RETRIES) {
      streaming = false;
      if (els.toggle) els.toggle.textContent = 'Start';
      if (els.retry) els.retry.classList.remove('hidden');
      setBadge('Disconnected', false);
      return;
    }
    retryTimer = setTimeout(startStream, delay);
  }

  function startStream() {
    if (els.retry) els.retry.classList.add('hidden');
    hideError();
    streaming = true;
    if (els.toggle) els.toggle.textContent = 'Stop';
    if (!els.stream) return;

    els.stream.classList.add('hidden');
    els.stream.onload = () => {
      connected = true;
      retries = 0;
      hideError();
      if (els.stream) els.stream.classList.remove('hidden');
      setBadge('Live', true);
    };
    els.stream.onerror = () => {
      connected = false;
      setBadge('Disconnected', false);
      if (streaming) scheduleReconnect();
    };
    els.stream.src = `${STREAM_URL}?t=${Date.now()}`;
  }

  // ------------------------------------------------------------------
  // Status rendering
  // ------------------------------------------------------------------

  function renderDevices(devices) {
    if (!els.devices) return;
    if (!devices || !devices.length) {
      els.devices.innerHTML =
        '<p class="cam-empty">No /dev/video* nodes found. The kernel does not see a ' +
        'camera — check the USB cable and run <code>lsusb | grep -i camera</code>.</p>';
      return;
    }
    els.devices.innerHTML = devices.map((d) => {
      const capture = d.capture === true
        ? 'capture'
        : (d.capture === false ? 'metadata only' : 'capture status unknown');
      const label = d.name || d.card || 'Unnamed device';
      return `<div class="cam-device">
        <span class="cam-device-path">${escapeHtml(d.path)}</span>
        <span class="cam-device-name">${escapeHtml(label)}</span>
        <span class="cam-device-meta">index ${escapeHtml(d.index)} &middot; ${escapeHtml(capture)}${
          d.resolution ? ' &middot; ' + escapeHtml(d.resolution) : ''}</span>
      </div>`;
    }).join('');
  }

  function renderStatus(payload) {
    const cam = (payload && payload.camera) || {};
    const ok = !!cam.configured;

    if (els.devStatus) {
      els.devStatus.innerHTML = ok
        ? '<span class="status ok"></span> Connected'
        : '<span class="status error"></span> Unavailable';
    }
    if (els.devBackend) els.devBackend.textContent = cam.name || '—';
    if (els.devPath) els.devPath.textContent = cam.device_path || '—';
    if (els.devRes) {
      els.devRes.textContent = (cam.width && cam.height) ? `${cam.width}x${cam.height}` : '—';
    }
    if (els.devFps) els.devFps.textContent = cam.fps ? `${cam.fps} fps` : '—';
    if (els.devCv2) {
      els.devCv2.innerHTML = payload.cv2_available
        ? '<span class="status ok"></span> Installed'
        : `<span class="status error"></span> ${escapeHtml(payload.cv2_error || 'Missing')}`;
    }
    if (els.devMessage) els.devMessage.textContent = cam.message || '';

    renderDevices(payload.devices);

    if (!ok) {
      showError(
        cam.error ? 'Camera unavailable' : (cam.message || 'Camera unavailable'),
        cam.error || cam.message || 'The camera reported no usable device.'
      );
      setBadge('Unavailable', false);
    } else if (!streaming) {
      hideError();
      setBadge('Ready', true);
    }
  }

  async function loadStatus() {
    try {
      const res = await fetch(STATUS_URL, { headers: { Accept: 'application/json' } });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const body = await res.json();
      renderStatus(body);
      return !!body.camera && !!body.camera.configured;
    } catch (err) {
      showError('Could not reach VeggieCare', String(err));
      setBadge('Offline', false);
      return false;
    }
  }

  // ------------------------------------------------------------------
  // Snapshot
  // ------------------------------------------------------------------

  async function takeSnapshot() {
    if (!els.snapshot) return;
    els.snapshot.disabled = true;
    try {
      const res = await fetch(`${SNAPSHOT_URL}?t=${Date.now()}`);
      if (!res.ok) {
        let message = `${res.status} ${res.statusText}`;
        try {
          const body = await res.json();
          if (body && body.error) message = body.error;
        } catch (_) { /* non-JSON error body */ }
        showError('Snapshot failed', message);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank', 'noopener');
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (err) {
      showError('Snapshot failed', String(err));
    } finally {
      els.snapshot.disabled = false;
    }
  }

  // ------------------------------------------------------------------
  // Device probing
  // ------------------------------------------------------------------

  async function runProbe() {
    if (!els.probe) return;
    els.probe.disabled = true;
    if (els.probeNote) els.probeNote.textContent = 'Testing nodes…';

    try {
      const res = await fetch(PROBE_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const body = await res.json();

      if (!res.ok) {
        if (els.probeNote) els.probeNote.textContent = body.error || `Failed (${res.status})`;
        showError('Camera probe failed', body.error || `${res.status} ${res.statusText}`);
        return;
      }

      renderProbe(body);
    } catch (err) {
      if (els.probeNote) els.probeNote.textContent = String(err);
      showError('Camera probe failed', String(err));
    } finally {
      els.probe.disabled = false;
    }
  }

  function renderProbe(body) {
    const results = body.results || [];
    const working = body.working || [];

    if (!results.length) {
      if (els.probeNote) els.probeNote.textContent = body.error || 'Nothing to test.';
      return;
    }

    if (working.length) {
      const best = body.recommended || working[0];
      if (els.probeNote) {
        els.probeNote.innerHTML =
          `Found ${working.length} working node${working.length > 1 ? 's' : ''}. ` +
          `Set <code>camera.device_path: ${escapeHtml(best)}</code> in config/config.yaml.`;
      }
    } else if (els.probeNote) {
      els.probeNote.innerHTML =
        'None of these nodes deliver frames. On a Pi with libcamera the video nodes ' +
        'are mostly metadata &mdash; attach the webcam to a USB port and re-run.';
    }

    if (!els.devices) return;
    els.devices.innerHTML = results.map((r) => {
      const verdict = r.delivers_frames
        ? '<span class="status ok"></span> delivers frames'
        : `<span class="status error"></span> ${escapeHtml(r.error || 'no frames')}`;
      const dims = (r.width && r.height) ? `${r.width}&times;${r.height}` : '';
      return `<div class="cam-device">
        <span class="cam-device-path">${escapeHtml(r.target)}</span>
        <span class="cam-device-meta">${verdict}${
          dims ? ' &middot; ' + dims : ''}</span>
      </div>`;
    }).join('');
  }

  // ------------------------------------------------------------------
  // Wiring
  // ------------------------------------------------------------------

  function init() {
    if (els.toggle) {
      els.toggle.addEventListener('click', () => {
        if (streaming) stopStream();
        else startStream();
      });
    }
    if (els.retry) {
      els.retry.addEventListener('click', async () => {
        retries = 0;
        if (await loadStatus()) startStream();
      });
    }
    if (els.snapshot) els.snapshot.addEventListener('click', takeSnapshot);
    if (els.probe) els.probe.addEventListener('click', runProbe);

    // Leaving the page must release the stream.
    window.addEventListener('pagehide', stopStream);
    window.addEventListener('beforeunload', stopStream);

    loadStatus().then((ok) => {
      // A degraded camera would only 503 in a retry loop, and starting the
      // stream would hide the diagnostic that renderStatus just showed.
      if (ok) startStream();
    });

    pollTimer = setInterval(() => {
      if (!streaming) loadStatus();
    }, POLL_MS);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.addEventListener('pagehide', () => {
    if (pollTimer) clearInterval(pollTimer);
  });
})();
