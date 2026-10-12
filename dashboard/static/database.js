/* VeggieCare Database Page — vanilla JS, no dependencies */

(() => {
  const API = '/api';
  const els = {
    btnExportJson: document.getElementById('btn-export-json'),
    btnRefreshDb: document.getElementById('btn-refresh-db'),
    exportStatus: document.getElementById('export-status'),
    tableList: document.getElementById('table-list'),
    tableViewer: document.getElementById('table-viewer'),
    tableViewerTitle: document.getElementById('table-viewer-title'),
    tableName: document.getElementById('table-name'),
    tablePageSize: document.getElementById('table-page-size'),
    tableViewerContent: document.getElementById('table-viewer-content'),
  };

  // State for table viewer
  let currentTable = null;
  let currentPage = 1;

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
    if (!iso) return '\u2014';
    const d = new Date(iso);
    return d.toLocaleString([], { dateStyle: 'short', timeStyle: 'medium' });
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => ({ '&': '&', '<': '<', '>': '>', '"': '"', "'": '\u2019' }[c]));
  }

  function formatValue(val) {
    if (val === null || val === undefined) return '<span class="null-value">NULL</span>';
    if (typeof val === 'object') return escapeHtml(JSON.stringify(val));
    if (typeof val === 'number' && !Number.isInteger(val)) return escapeHtml(val.toFixed(4));
    return escapeHtml(String(val));
  }

  async function loadTables() {
    els.tableList.innerHTML = '<p class="log-empty">Loading\u2026</p>';
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
      <div class="table-item" data-table="${escapeHtml(name)}">
        <span class="table-name">${escapeHtml(name)}</span>
        <span class="table-badge">${name}</span>
        <button class="btn btn-ghost btn-sm table-view-btn" type="button" data-table="${escapeHtml(name)}">View</button>
      </div>
    `).join('');

    els.tableList.innerHTML = `
      <div class="table-list-wrap">${html}</div>
    `;

    // Add click handlers for view buttons
    els.tableList.querySelectorAll('.table-view-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const tableName = btn.dataset.table;
        openTableViewer(tableName);
      });
    });

    // Also allow clicking the row to view
    els.tableList.querySelectorAll('.table-item').forEach(item => {
      item.addEventListener('click', () => {
        const tableName = item.dataset.table;
        openTableViewer(tableName);
      });
    });
  }

  async function openTableViewer(tableName) {
    currentTable = tableName;
    currentPage = 1;
    els.tableName.textContent = tableName;
    els.tableViewer.classList.remove('hidden');
    await loadTableData(tableName, currentPage);
    // Scroll to viewer
    els.tableViewer.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function closeTableViewer() {
    currentTable = null;
    currentPage = 1;
    els.tableViewer.classList.add('hidden');
    els.tableViewerContent.innerHTML = '';
  }

  async function loadTableData(tableName, page) {
    const perPage = parseInt(els.tablePageSize.value, 10);
    els.tableViewerContent.innerHTML = '<p class="log-empty">Loading\u2026</p>';

    try {
      const data = await fetchJson(`${API}/database/table/${encodeURIComponent(tableName)}?page=${page}&per_page=${perPage}`);
      if (!data.ok) throw new Error(data.error || 'Failed to load table data');
      renderTableData(data);
    } catch (e) {
      els.tableViewerContent.innerHTML = `<p class="log-error">Failed to load table: ${escapeHtml(e.message)}</p>`;
    }
  }

  function renderTableData(data) {
    const { columns, table: tableName, pagination, tableData } = data;
    const rows = data.data;

    if (!rows.length) {
      els.tableViewerContent.innerHTML = '<p class="log-empty">No rows in this table.</p>';
      renderPagination(pagination);
      return;
    }

    // Build table HTML
    const thead = `
      <thead>
        <tr>
          ${columns.map(col => `<th>${escapeHtml(col)}</th>`).join('')}
        </tr>
      </thead>
    `;

    const tbody = `
      <tbody>
        ${rows.map(row => `
          <tr>
            ${columns.map(col => `<td>${formatValue(row[col])}</td>`).join('')}
          </tr>
        `).join('')}
      </tbody>
    `;

    const paginationHtml = renderPagination(pagination);

    els.tableViewerContent.innerHTML = `
      <div class="table-viewer-wrap">
        <div class="log-table-wrap">
          <table class="log-table">
            ${thead}
            ${tbody}
          </table>
        </div>
        ${paginationHtml}
      </div>
    `;
  }

  function renderPagination(pagination) {
    const { page, per_page, total, total_pages } = pagination;
    if (total_pages <= 1) return '';

    let html = '<div class="table-pagination">';
    html += `<span class="pagination-info">Page ${page} of ${total_pages} (${total} total rows)</span>`;
    html += '<div class="pagination-controls">';

    if (page > 1) {
      html += `<button class="btn btn-secondary btn-sm" data-page="${page - 1}" type="button">&laquo; Prev</button>`;
    }

    // Page numbers
    const maxVisible = 5;
    let start = Math.max(1, page - Math.floor(maxVisible / 2));
    let end = Math.min(total_pages, start + maxVisible - 1);
    if (end - start + 1 < maxVisible) {
      start = Math.max(1, end - maxVisible + 1);
    }

    for (let p = start; p <= end; p++) {
      if (p === page) {
        html += `<span class="pagination-current">${p}</span>`;
      } else {
        html += `<button class="btn btn-ghost btn-sm" data-page="${p}" type="button">${p}</button>`;
      }
    }

    if (page < total_pages) {
      html += `<button class="btn btn-secondary btn-sm" data-page="${page + 1}" type="button">Next &raquo;</button>`;
    }

    html += '</div></div>';
    return html;
  }

  function handlePaginationClick(e) {
    const btn = e.target.closest('[data-page]');
    if (!btn) return;
    const page = parseInt(btn.dataset.page, 10);
    if (page && currentTable) {
      currentPage = page;
      loadTableData(currentTable, page);
    }
  }

  function handlePageSizeChange() {
    if (currentTable) {
      currentPage = 1;
      loadTableData(currentTable, 1);
    }
  }

  async function exportDatabase() {
    hideStatus();
    els.btnExportJson.disabled = true;
    els.btnExportJson.textContent = 'Exporting\u2026';

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
  els.tablePageSize.addEventListener('change', handlePageSizeChange);
  els.tableViewerContent.addEventListener('click', handlePaginationClick);

  // Initial load
  loadTables();
})();