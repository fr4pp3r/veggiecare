/* VeggieCare Database Page — vanilla JS, no dependencies */

(() => {
  const API = '/api';
  const els = {
    btnExportJson: document.getElementById('btn-export-json'),
    btnRefreshDb: document.getElementById('btn-refresh-db'),
    exportStatus: document.getElementById('export-status'),
    tableList: document.getElementById('table-list'),
  };

  async function fetchJson(url, options = {}) {
    const res = await fetch(url, { headers: { 'Accept': 'application/json' }, ...options });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  }

  function showStatus(message, isError = false) {
    els.exportStatus.textContent = message;
    els.exportStatus.className = isError ? 'status-error' : 'status-success';
    els.exportStatus.classList.remove('hidden');
  }

  function hideStatus() {
    els.exportStatus.classList.add('hidden');
    els.exportStatus.textContent = '';
  }

  function fmtTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleString([], { dateStyle: 'short', timeStyle: 'medium' });
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => ({ '&': '&', '<': '<', '>': '>', '"': '"', "'": ''' }[c]));
  }

  async function loadTables() {
    els.tableList.innerHTML = '<p class="log-empty">Loading…</p>';
    try {
      const data = await fetchJson(`${API}/database/tables`);
      if (!data.ok) throw new Error(data.error || 'Failed to load tables');
      renderTables(data.tables);
    } catch (e) {
      els.tableList.innerHTML = `<p class="log-error">Failed to load tables: ${escapeHtml(e.message)}</p>`;
    }
  }

  function renderTables(tables) {
    if (!tables.length) {
      els.tableList.innerHTML = '<p class="log-empty">No tables found.</p>';
      return;
    }

    const html = tables.map(name => `
      <div class="table-item">
        <span class="table-name">${escapeHtml(name)}</span>
        <span class="table-badge">${name}</span>
      </div>
    `).join('');

    els.tableList.innerHTML = `
      <div class="table-list-wrap">${html}</div>
    `;
  }

  async function exportDatabase() {
    hideStatus();
    els.btnExportJson.disabled = true;
    els.btnExportJson.textContent = 'Exporting…';

    try {
      const data = await fetchJson(`${API}/database/export`);
      if (!data.ok) throw new Error(data.error || 'Export failed');

      // Create blob and trigger download
      const jsonStr = JSON.stringify(data.data, null, 2);
      const blob = new Blob([jsonStr], { type: 'application/json' });
      const url = URL.createObjectURL(blob);

      const now = new Date();
      const timestamp = now.toISOString().slice(0, 19).replace('T', '-').replace(/:/g, '-');
      const filename = `veggiecare-db-${timestamp}.json`;

      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      showStatus(`Downloaded ${filename}`);
    } catch (e) {
      showStatus(`Export failed: ${escapeHtml(e.message)}`, true);
    } finally {
      els.btnExportJson.disabled = false;
      els.btnExportJson.textContent = 'Download JSON Export';
    }
  }

  // Event listeners
  els.btnExportJson.addEventListener('click', exportDatabase);
  els.btnRefreshDb.addEventListener('click', loadTables);

  // Initial load
  loadTables();
})();