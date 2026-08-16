/* app.js — boot, hash router, topbar (client switcher, date range, sync).
   Ad Ops Command Center.
   All dynamic values interpolated into markup are escaped via esc() from api.js. */

import {
  state, loadState, saveState, applyPreset,
  getJSON, postJSON, qs,
  toast, esc, icon, errorPanel,
  runCleanups, destroyCharts,
} from './api.js';

import dashboard from './dashboard.js';
import organic from './organic.js';
import creatives from './creatives.js';
import briefs from './briefs.js';
import cloner from './cloner.js';
import scans from './scans.js';
import spy from './spy.js';
import grader from './grader.js';
import pipeline from './pipeline.js';
import settings from './settings.js';

const MODULES = { dashboard, organic, creatives, briefs, cloner, scans, spy, grader, pipeline, settings };

const NAV = [
  { view: 'dashboard', label: 'Dashboard', icon: 'dashboard' },
  { view: 'organic', label: 'Organic', icon: 'chart' },
  { view: 'creatives', label: 'Creatives', icon: 'image' },
  { view: 'grader', label: 'Grader', icon: 'zap' },
  { view: 'pipeline', label: 'Pipeline', icon: 'file' },
  { view: 'briefs', label: 'Briefs', icon: 'file' },
  { view: 'cloner', label: 'Cloner', icon: 'copy' },
  { view: 'scans', label: 'Scans', icon: 'radar' },
  { view: 'spy', label: 'Ad Spy', icon: 'search' },
  { sep: true },
  { view: 'settings', label: 'Settings', icon: 'sliders' },
];

let clients = [];

/* ---------------- Chart.js defaults ---------------- */

function setupChartDefaults() {
  if (!window.Chart) return;
  const C = window.Chart;
  C.defaults.color = '#8A93A6';
  C.defaults.borderColor = 'rgba(38, 43, 56, 0.6)';
  C.defaults.font.family = "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif";
  C.defaults.font.size = 11;
  C.defaults.plugins.tooltip.backgroundColor = '#1E2330';
  C.defaults.plugins.tooltip.borderColor = '#262B38';
  C.defaults.plugins.tooltip.borderWidth = 1;
  C.defaults.plugins.tooltip.titleColor = '#E6E9F0';
  C.defaults.plugins.tooltip.bodyColor = '#E6E9F0';
}

/* ---------------- Navigation ---------------- */

function buildNav() {
  const nav = document.getElementById('nav');
  nav.innerHTML = NAV.map((item) => {
    if (item.sep) return '<div class="nav-sep"></div>';
    return `<a href="#${item.view}" data-view="${item.view}">${icon(item.icon, 16)}<span>${esc(item.label)}</span></a>`;
  }).join('');
}

function setActiveNav(view) {
  document.querySelectorAll('#nav a').forEach((a) => {
    a.classList.toggle('active', a.dataset.view === view);
  });
}

/* ---------------- Router ---------------- */

export function parseHash() {
  const raw = (window.location.hash || '#dashboard').slice(1);
  const [path, q] = raw.split('?');
  const view = MODULES[path] ? path : 'dashboard';
  const query = Object.fromEntries(new URLSearchParams(q || ''));
  return { view, query };
}

function selectClient(id) {
  state.clientId = id === null || id === undefined ? '' : String(id);
  saveState();
  const sw = document.getElementById('client-switcher');
  if (sw) sw.value = state.clientId;
  if (parseHash().view === 'dashboard') route();
  else window.location.hash = '#dashboard';
}

function route() {
  runCleanups();
  destroyCharts();
  const { view, query } = parseHash();
  setActiveNav(view);
  const el = document.getElementById('view');
  el.innerHTML = '';
  el.scrollTop = 0;
  const ctx = {
    clientId: state.clientId,
    dateFrom: state.dateFrom,
    dateTo: state.dateTo,
    clients,
    query,
    refresh: route,
    selectClient,
  };
  const mod = MODULES[view];
  Promise.resolve(mod.render(el, ctx)).catch((err) => {
    console.error(`[${view}]`, err);
    el.innerHTML = errorPanel(err);
  });
}

/* ---------------- Topbar: client switcher ---------------- */

function populateClientSwitcher() {
  const sw = document.getElementById('client-switcher');
  sw.innerHTML = '<option value="">All clients</option>' + clients.map(
    (c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`,
  ).join('');
  if (state.clientId && !clients.some((c) => String(c.id) === String(state.clientId))) {
    state.clientId = '';
    saveState();
  }
  sw.value = state.clientId;
  sw.addEventListener('change', () => {
    state.clientId = sw.value;
    saveState();
    route();
  });
}

/* ---------------- Topbar: date range ---------------- */

function wireDateRange() {
  const preset = document.getElementById('date-preset');
  const custom = document.getElementById('custom-range');
  const fromEl = document.getElementById('date-from');
  const toEl = document.getElementById('date-to');

  const syncInputs = () => {
    preset.value = state.preset === 'custom' ? 'custom' : state.preset;
    if (![...preset.options].some((o) => o.value === preset.value)) preset.value = '30';
    custom.classList.toggle('hidden', state.preset !== 'custom');
    fromEl.value = state.dateFrom;
    toEl.value = state.dateTo;
  };
  syncInputs();

  preset.addEventListener('change', () => {
    if (preset.value === 'custom') {
      state.preset = 'custom';
      custom.classList.remove('hidden');
      saveState();
      return; // wait for the user to pick both dates
    }
    applyPreset(preset.value);
    syncInputs();
    saveState();
    route();
  });

  const onCustom = () => {
    if (!fromEl.value || !toEl.value) return;
    if (fromEl.value > toEl.value) {
      toast('The from date must be before the to date.', 'warn');
      return;
    }
    state.preset = 'custom';
    state.dateFrom = fromEl.value;
    state.dateTo = toEl.value;
    saveState();
    route();
  };
  fromEl.addEventListener('change', onCustom);
  toEl.addEventListener('change', onCustom);
}

/* ---------------- Topbar: sync ---------------- */

let syncTimer = null;
let syncing = false;

function setSyncing(on) {
  syncing = on;
  const btn = document.getElementById('sync-btn');
  const iconEl = document.getElementById('sync-icon');
  const label = document.getElementById('sync-label');
  btn.disabled = on;
  iconEl.classList.toggle('spin', on);
  label.textContent = on ? 'Syncing…' : 'Sync now';
}

async function pollSyncRuns(notifyWhenDone) {
  if (syncTimer) clearTimeout(syncTimer);
  let runs = [];
  try {
    runs = await getJSON('/api/sync/runs', { limit: 30 }, { silent: true });
  } catch {
    setSyncing(false);
    return;
  }
  const running = Array.isArray(runs) && runs.some((r) => r.status === 'running');
  if (running) {
    setSyncing(true);
    syncTimer = setTimeout(() => pollSyncRuns(true), 5000);
  } else {
    const wasSyncing = syncing;
    setSyncing(false);
    if (wasSyncing && notifyWhenDone) {
      const errs = (runs || []).filter((r) => r.status === 'error').length;
      if (errs) toast(`Sync finished with ${errs} error${errs === 1 ? '' : 's'} — see Settings for details.`, 'warn');
      else toast('Sync complete.', 'success');
      route();
    }
  }
}

function wireSync() {
  document.getElementById('sync-btn').addEventListener('click', async () => {
    if (syncing) return;
    setSyncing(true);
    try {
      await postJSON(`/api/sync/all${state.clientId ? qs({ client_id: state.clientId }) : ''}`);
      toast('Sync started — pulling data from all platforms.');
      pollSyncRuns(true);
    } catch {
      setSyncing(false);
    }
  });
}

/* ---------------- Boot ---------------- */

async function boot() {
  loadState();
  setupChartDefaults();
  buildNav();
  wireDateRange();
  wireSync();
  try {
    clients = await getJSON('/api/clients', null, { silent: true });
    if (!Array.isArray(clients)) clients = [];
  } catch {
    clients = [];
    toast('Could not reach the backend — start the FastAPI server and refresh.', 'error', 8000);
  }
  populateClientSwitcher();
  pollSyncRuns(false); // pick up any sync already in flight
  window.addEventListener('hashchange', route);
  route();
}

boot();
