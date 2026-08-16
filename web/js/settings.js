/* settings.js — grouped key/value settings editor, connected accounts table,
   CSV import panel, and sync-run history with expandable errors.
   Security note: every dynamic value is HTML-escaped via esc() before rendering. */

import {
  getJSON, putJSON, postForm,
  esc, toast,
  skeleton, emptyState, viewHead, statusBadge,
  fmtN, fmtDateTime, platformChip,
} from './api.js';

export default { render };

async function render(el, ctx) {
  el.innerHTML = viewHead('Settings', 'Integration keys, accounts, CSV import and sync history') + skeleton('blocks', 4);

  const [settings, presets, runs, accounts] = await Promise.all([
    getJSON('/api/settings').catch(() => null),
    getJSON('/api/sync/csv/presets', null, { silent: true }).catch(() => ({})),
    getJSON('/api/sync/runs', { limit: 30 }, { silent: true }).catch(() => []),
    loadAccounts(ctx),
  ]);

  el.innerHTML = viewHead('Settings', 'Integration keys, accounts, CSV import and sync history') + `
    <div class="section card">
      <div class="section-head">
        <h2>Application settings</h2>
        <button id="btn-save-settings" class="btn btn-primary btn-sm">Save settings</button>
      </div>
      <div id="settings-groups">${settingsGroups(settings)}</div>
    </div>

    <div class="section">
      <div class="section-head"><h2>Connected accounts</h2></div>
      ${accountsTable(accounts, ctx)}
    </div>

    <div class="section card">
      <div class="section-head"><h2>CSV import</h2></div>
      <p class="muted small mb-16">Import a platform export (e.g. LinkedIn campaign performance) into an account using a column-mapping preset.</p>
      <div class="flex flex-wrap">
        <select id="csv-account" class="select" aria-label="Account">
          ${accounts.length
            ? accounts.map((a) => `<option value="${esc(a.id)}">${esc(a.clientName)} · ${esc(a.platform)} ${esc(a.alias || a.external_id || '')}</option>`).join('')
            : '<option value="">No accounts available</option>'}
        </select>
        <select id="csv-preset" class="select" aria-label="Preset">
          ${Object.keys(presets || {}).length
            ? Object.keys(presets).map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join('')
            : '<option value="">No presets</option>'}
        </select>
        <input id="csv-file" class="input" type="file" accept=".csv,text/csv">
        <button id="btn-csv" class="btn btn-primary">Import</button>
      </div>
      <div id="csv-result" class="mt-8 muted small"></div>
    </div>

    <div class="section">
      <div class="section-head"><h2>Sync runs</h2></div>
      ${syncRunsTable(runs)}
    </div>`;

  wireSettingsSave(el, settings);
  wireCsvImport(el);
  wireErrorRows(el);
}

/* ================= Settings editor ================= */

function groupFor(key) {
  const k = String(key).toLowerCase();
  if (k.startsWith('apify') || k.includes('actor')) return 'Apify';
  if (k.startsWith('gemini') || k.includes('model')) return 'AI models';
  if (k.includes('schedule') || k.includes('cron') || k.includes('time')) return 'Schedule';
  return 'Other';
}

function settingsGroups(settings) {
  if (!settings || !Object.keys(settings).length) {
    return '<p class="muted small">No settings returned by the backend yet — defaults are in use. Once the backend seeds its settings they will be editable here.</p>';
  }
  const groups = new Map();
  for (const [k, v] of Object.entries(settings)) {
    const g = groupFor(k);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push([k, v]);
  }
  const order = ['Apify', 'AI models', 'Schedule', 'Other'];
  return [...groups.entries()]
    .sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]))
    .map(([g, entries]) => `
      <div class="settings-group">
        <h3 class="mb-8">${esc(g)}</h3>
        ${entries.map(([k, v]) => `
          <div class="settings-row">
            <div class="settings-key">${esc(k)}</div>
            <input class="input" type="text" data-setting="${esc(k)}" value="${esc(v === null || v === undefined ? '' : v)}">
          </div>`).join('')}
      </div>`).join('');
}

function wireSettingsSave(el, settings) {
  const btn = el.querySelector('#btn-save-settings');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    if (!settings) { toast('Nothing to save — settings did not load.', 'warn'); return; }
    const payload = {};
    el.querySelectorAll('input[data-setting]').forEach((input) => {
      payload[input.dataset.setting] = input.value;
    });
    btn.disabled = true;
    try {
      await putJSON('/api/settings', payload);
      toast('Settings saved.', 'success');
    } finally {
      btn.disabled = false;
    }
  });
}

/* ================= Accounts ================= */

async function loadAccounts(ctx) {
  const ids = ctx.clientId
    ? [ctx.clientId]
    : (ctx.clients || []).map((c) => c.id);
  const results = await Promise.all(ids.map(async (id) => {
    try {
      const c = await getJSON(`/api/clients/${id}`, null, { silent: true });
      return (c.accounts || []).map((a) => ({ ...a, clientName: c.name }));
    } catch {
      return [];
    }
  }));
  return results.flat();
}

function accountsTable(accounts, ctx) {
  if (!accounts.length) {
    return emptyState(
      'box',
      'No ad accounts connected',
      ctx.clientId
        ? 'This client has no accounts on record. Add accounts to the database seed, then sync.'
        : 'No clients have accounts on record yet. Add them to the database seed, then press Sync now.',
    );
  }
  return `
    <div class="table-wrap">
      <table class="table">
        <thead><tr>
          <th>Client</th><th>Platform</th><th>Alias</th><th>External ID</th><th>Currency</th><th>Sync</th>
        </tr></thead>
        <tbody>
          ${accounts.map((a) => `
            <tr>
              <td>${esc(a.clientName)}</td>
              <td>${platformChip(a.platform)}</td>
              <td>${esc(a.alias || '—')}</td>
              <td class="muted small num">${esc(a.external_id || '—')}</td>
              <td>${esc(a.currency || '—')}</td>
              <td>${a.sync_enabled ? '<span class="badge done">enabled</span>' : '<span class="muted small">off</span>'}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

/* ================= CSV import ================= */

function wireCsvImport(el) {
  const btn = el.querySelector('#btn-csv');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const accountId = el.querySelector('#csv-account').value;
    const preset = el.querySelector('#csv-preset').value;
    const file = el.querySelector('#csv-file').files[0];
    const result = el.querySelector('#csv-result');
    if (!accountId) { toast('Pick an account to import into.', 'warn'); return; }
    if (!preset) { toast('Pick a mapping preset.', 'warn'); return; }
    if (!file) { toast('Choose a CSV file first.', 'warn'); return; }

    const fd = new FormData();
    fd.append('file', file);
    fd.append('account_id', accountId);
    fd.append('preset', preset);

    btn.disabled = true;
    result.textContent = 'Importing…';
    try {
      const res = await postForm('/api/sync/csv', fd);
      result.textContent = `Imported ${fmtN(res.rows_written)} rows, skipped ${fmtN(res.skipped)} (${res.mode || 'upsert'}).`;
      toast('CSV imported.', 'success');
    } catch (err) {
      result.textContent = `Import failed: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  });
}

/* ================= Sync runs ================= */

function syncRunsTable(runs) {
  if (!runs || !runs.length) {
    return emptyState(
      'zap',
      'No sync runs yet',
      'Press Sync now (top right) to pull data from every connected platform. Each run is logged here with row counts and errors.',
    );
  }
  return `
    <div class="table-wrap">
      <table class="table">
        <thead><tr>
          <th>Platform</th><th>Account</th><th>Status</th>
          <th class="num">Rows</th><th>Started</th><th>Finished</th>
        </tr></thead>
        <tbody>
          ${runs.map((r, i) => `
            <tr class="${r.error ? 'row-click' : ''}" ${r.error ? `data-err-toggle="${i}"` : ''}>
              <td>${platformChip(r.platform)}</td>
              <td class="muted small">${esc(r.alias || '—')}</td>
              <td>${statusBadge(r.status)}${r.error ? ' <span class="muted small">(click for detail)</span>' : ''}</td>
              <td class="num">${fmtN(r.rows_written)}</td>
              <td class="muted small">${esc(fmtDateTime(r.started_at))}</td>
              <td class="muted small">${esc(fmtDateTime(r.finished_at))}</td>
            </tr>
            ${r.error ? `
              <tr class="hidden" data-err-row="${i}">
                <td colspan="6"><div class="error-detail">${esc(r.error)}</div></td>
              </tr>` : ''}`).join('')}
        </tbody>
      </table>
    </div>`;
}

function wireErrorRows(el) {
  el.querySelectorAll('tr[data-err-toggle]').forEach((tr) => {
    tr.addEventListener('click', () => {
      const row = el.querySelector(`tr[data-err-row="${tr.dataset.errToggle}"]`);
      if (row) row.classList.toggle('hidden');
    });
  });
}
