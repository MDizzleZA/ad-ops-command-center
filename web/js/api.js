/* api.js — shared API client, global state, and UI utilities.
   Ad Ops Command Center · vanilla ES modules, no build step.
   Note: all dynamic values rendered into HTML strings are escaped via esc(). */

/* ================= Global state ================= */

const LS_KEY = 'adops.state.v1';

export const state = {
  clientId: '',
  preset: '30',
  dateFrom: '',
  dateTo: '',
};

export function isoDate(d) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export function applyPreset(days) {
  state.preset = String(days);
  const to = new Date();
  const from = new Date();
  from.setDate(to.getDate() - (Number(days) - 1));
  state.dateFrom = isoDate(from);
  state.dateTo = isoDate(to);
}

export function loadState() {
  try {
    Object.assign(state, JSON.parse(localStorage.getItem(LS_KEY) || '{}'));
  } catch { /* corrupt state — start fresh */ }
  if (state.preset !== 'custom' || !state.dateFrom || !state.dateTo) {
    applyPreset(state.preset === 'custom' ? '30' : (state.preset || '30'));
  }
}

export function saveState() {
  localStorage.setItem(LS_KEY, JSON.stringify(state));
}

/* ================= Fetch layer ================= */

export function qs(params) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== '') u.set(k, v);
  }
  const s = u.toString();
  return s ? `?${s}` : '';
}

export function rangeParams(ctx, extra) {
  return {
    client_id: ctx.clientId || undefined,
    date_from: ctx.dateFrom,
    date_to: ctx.dateTo,
    ...(extra || {}),
  };
}

export async function fetchJSON(url, opts = {}) {
  const { silent, ...init } = opts;
  let res;
  try {
    res = await fetch(url, init);
  } catch (err) {
    if (!silent) toast('Network error — is the backend running on port 7480?', 'error');
    throw err;
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      const d = j.detail ?? j.error ?? j.message;
      if (d) detail = typeof d === 'string' ? d : JSON.stringify(d);
    } catch { /* non-JSON error body */ }
    if (!silent) toast(detail, 'error');
    const e = new Error(detail);
    e.status = res.status;
    throw e;
  }
  if (res.status === 204) return null;
  return res.json();
}

export function getJSON(path, params, opts) {
  return fetchJSON(path + qs(params), opts);
}

export function postJSON(path, body, opts) {
  return fetchJSON(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
    ...(opts || {}),
  });
}

export function putJSON(path, body, opts) {
  return fetchJSON(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
    ...(opts || {}),
  });
}

export function postForm(path, formData, opts) {
  return fetchJSON(path, { method: 'POST', body: formData, ...(opts || {}) });
}

/* ================= Formatting ================= */

function group(n) {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

export function fmtR(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '–';
  const v = Math.round(Number(n));
  return v < 0 ? `-R ${group(Math.abs(v))}` : `R ${group(v)}`;
}

export function fmtN(n, dec) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '–';
  const v = Number(n);
  const d = dec !== undefined ? dec : (Math.abs(v) < 10 && v % 1 !== 0 ? 1 : 0);
  const fixed = Math.abs(v).toFixed(d);
  const [int, frac] = fixed.split('.');
  return (v < 0 ? '-' : '') + group(int) + (frac ? `.${frac}` : '');
}

export function fmtPct(n, dec = 2) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '–';
  return `${Number(n).toFixed(dec)}%`;
}

export function fmtDate(s) {
  if (!s) return '–';
  return String(s).slice(0, 10);
}

export function fmtDateTime(s) {
  if (!s) return '–';
  return String(s).replace('T', ' ').slice(0, 16);
}

/** HTML-escape a value before interpolating it into a template string. */
export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

/* ================= Delta chips ================= */

/** Green ▲ / red ▼ vs previous period. For cost metrics pass {invert:true} — down is good. */
export function deltaChip(cur, prev, opts = {}) {
  const invert = !!opts.invert;
  if (cur === null || cur === undefined || prev === null || prev === undefined ||
      !isFinite(Number(prev)) || Number(prev) === 0 || !isFinite(Number(cur))) {
    return '<span class="delta flat">—</span>';
  }
  const pct = ((Number(cur) - Number(prev)) / Math.abs(Number(prev))) * 100;
  if (!isFinite(pct)) return '<span class="delta flat">—</span>';
  if (Math.abs(pct) < 0.05) return '<span class="delta flat">0.0%</span>';
  const up = pct > 0;
  const good = invert ? !up : up;
  const arrow = up ? '▲' : '▼';
  return `<span class="delta ${good ? 'up' : 'down'}" title="vs previous period">${arrow} ${Math.abs(pct).toFixed(1)}%</span>`;
}

/* ================= Platform chips & badges ================= */

export const PLATFORM_COLORS = {
  meta: '#6C8CFF', google: '#4ADE80', linkedin: '#A78BFA',
  bing: '#22D3EE', ga4: '#FBBF24', gsc: '#F472B6', gbp: '#34D399', csv: '#8A93A6',
};

export const PLATFORM_LABELS = {
  meta: 'Meta', google: 'Google', linkedin: 'LinkedIn',
  bing: 'Microsoft', ga4: 'GA4', gsc: 'Search Console', gbp: 'Business Profile', csv: 'CSV',
};

export function platformChip(p) {
  const k = String(p || '').toLowerCase();
  return `<span class="chip ${esc(k)}">${esc(PLATFORM_LABELS[k] || p || '—')}</span>`;
}

export const BADGE_LABELS = { winning: 'Winning', fatiguing: 'Fatiguing', watch: 'Watch' };

export function statusBadge(status) {
  const s = String(status || '').toLowerCase();
  const cls = { running: 'running', done: 'done', error: 'error', pass: 'pass', warn: 'warn', block: 'block' }[s] || 'watch';
  return `<span class="badge ${cls}">${esc(status || '—')}</span>`;
}

export function hexA(hex, a) {
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}

/* ================= Icons (static inline SVG, no user input) ================= */

const PATHS = {
  dashboard: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  radar: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="5"/><line x1="12" y1="2" x2="12" y2="7"/>',
  search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
  sliders: '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
  chart: '<line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
  box: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
  alert: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  zap: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
};

export function icon(name, size = 40) {
  const inner = PATHS[name] || PATHS.box;
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
}

/* ================= UI helpers ================= */

export function toast(msg, type = 'info', ms = 4500) {
  const host = document.getElementById('toasts');
  if (!host) return;
  const div = document.createElement('div');
  div.className = `toast ${type === 'info' ? '' : type}`.trim();
  const span = document.createElement('span');
  span.textContent = String(msg);
  const btn = document.createElement('button');
  btn.className = 'toast-close';
  btn.setAttribute('aria-label', 'Dismiss');
  btn.textContent = '×';
  btn.addEventListener('click', () => div.remove());
  div.appendChild(span);
  div.appendChild(btn);
  host.appendChild(div);
  setTimeout(() => div.remove(), ms);
}

export function modal(title, content, opts = {}) {
  const root = document.getElementById('modal-root');
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal ${opts.wide ? 'wide' : ''}">
      <div class="modal-head"><h3>${esc(title)}</h3><button class="modal-close" aria-label="Close">&times;</button></div>
      <div class="modal-body"></div>
    </div>`;
  const body = overlay.querySelector('.modal-body');
  if (typeof content === 'string') body.innerHTML = content;
  else if (content) body.appendChild(content);
  const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  overlay.querySelector('.modal-close').addEventListener('click', close);
  overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) close(); });
  document.addEventListener('keydown', onKey);
  root.appendChild(overlay);
  return { overlay, body, close };
}

export function skeleton(kind = 'blocks', n = 3) {
  if (kind === 'rows') return '<div class="skel skel-row"></div>'.repeat(n);
  if (kind === 'cards') return `<div class="skel-grid">${'<div class="skel skel-card"></div>'.repeat(n)}</div>`;
  return '<div class="skel skel-block"></div>'.repeat(n);
}

export function emptyState(iconName, title, hint, actionHTML = '') {
  return `
    <div class="empty">
      <div class="empty-icon">${icon(iconName, 40)}</div>
      <div class="empty-title">${esc(title)}</div>
      <p class="empty-hint">${esc(hint)}</p>
      ${actionHTML ? `<div class="mt-16">${actionHTML}</div>` : ''}
    </div>`;
}

export function errorPanel(err) {
  return `
    <div class="empty">
      <div class="empty-icon">${icon('alert', 40)}</div>
      <div class="empty-title">Something went wrong</div>
      <p class="empty-hint">${esc(err && err.message ? err.message : 'Could not load this view. Check that the backend is running.')}</p>
    </div>`;
}

export function viewHead(title, sub = '', rightHTML = '') {
  return `
    <div class="view-head">
      <div><h1>${esc(title)}</h1>${sub ? `<div class="sub">${esc(sub)}</div>` : ''}</div>
      ${rightHTML ? `<div class="flex flex-wrap">${rightHTML}</div>` : ''}
    </div>`;
}

/* ================= Media / thumbnails ================= */

export function mediaUrl(f) {
  if (!f) return null;
  const s = String(f);
  if (s.startsWith('http') || s.startsWith('/') || s.startsWith('data:')) return s;
  return `/media/${s}`;
}

const GRADS = [
  'linear-gradient(135deg, #6C8CFF, #A78BFA)',
  'linear-gradient(135deg, #4ADE80, #22D3EE)',
  'linear-gradient(135deg, #F59E0B, #F87171)',
  'linear-gradient(135deg, #22D3EE, #6C8CFF)',
  'linear-gradient(135deg, #A78BFA, #F472B6)',
  'linear-gradient(135deg, #34D399, #6C8CFF)',
];

export function gradFor(s) {
  let h = 0;
  for (const c of String(s || 'x')) h = ((h * 31) + c.charCodeAt(0)) >>> 0;
  return GRADS[h % GRADS.length];
}

export function initials(name) {
  const words = String(name || '?').trim().split(/\s+/).slice(0, 2);
  return words.map((w) => w[0] || '').join('').toUpperCase() || '?';
}

/** Shared card renderer for reference ads (Spy + Scans). All fields escaped. */
export function refAdCard(ad, opts = {}) {
  const media = mediaUrl(ad.media);
  const thumb = media
    ? `<img src="${esc(media)}" alt="" loading="lazy" onerror="this.remove()">`
    : `<div class="thumb-fallback" style="background:${gradFor(ad.page_name)}">${esc(initials(ad.page_name))}</div>`;
  return `
    <div class="card ad-card hoverable" data-ad-id="${esc(ad.id)}">
      ${opts.ribbon ? '<div class="ribbon">NEW</div>' : ''}
      <div class="ad-media">${thumb}</div>
      <div class="ad-body">
        <div class="ad-meta-row">
          <strong>${esc(ad.page_name || 'Unknown page')}</strong>
          ${ad.platform ? platformChip(ad.platform) : ''}
          ${ad.is_active ? '<span class="badge done">Active</span>' : ''}
        </div>
        ${ad.headline ? `<div class="ad-headline">${esc(ad.headline)}</div>` : ''}
        ${ad.body ? `<div class="ad-text">${esc(ad.body)}</div>` : ''}
        <div class="ad-meta-row">
          ${ad.cta ? `<span class="chip">${esc(ad.cta)}</span>` : ''}
          ${ad.started_running ? `<span>Since ${esc(fmtDate(ad.started_running))}</span>` : ''}
        </div>
        ${opts.actions ? `<div class="card-actions">${opts.actions}</div>` : ''}
      </div>
    </div>`;
}

/* ================= Charts ================= */

const liveCharts = [];

export function makeChart(canvas, config) {
  if (!window.Chart || !canvas) return null;
  const c = new window.Chart(canvas, config);
  liveCharts.push(c);
  return c;
}

export function destroyCharts() {
  while (liveCharts.length) {
    try { liveCharts.pop().destroy(); } catch { /* already gone */ }
  }
}

export function sparkline(canvas, values, color = '#6C8CFF') {
  return makeChart(canvas, {
    type: 'line',
    data: {
      labels: values.map((_, i) => i),
      datasets: [{
        data: values,
        borderColor: color,
        backgroundColor: hexA(color, 0.12),
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.35,
        fill: 'origin',
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false } },
    },
  });
}

/* ================= Lifecycle (cleanups between routes) ================= */

const cleanups = [];

export function onCleanup(fn) {
  cleanups.push(fn);
}

export function runCleanups() {
  while (cleanups.length) {
    try { cleanups.pop()(); } catch { /* cleanup failed — ignore */ }
  }
}

/** Poll fn every `ms` until it resolves truthy; auto-cancelled on route change. */
export function pollUntil(fn, ms) {
  let stopped = false;
  let t = null;
  onCleanup(() => { stopped = true; if (t) clearTimeout(t); });
  const tick = async () => {
    if (stopped) return;
    let done = false;
    try { done = await fn(); } catch { done = false; }
    if (done || stopped) return;
    t = setTimeout(tick, ms);
  };
  tick();
  return () => { stopped = true; if (t) clearTimeout(t); };
}

/* ================= Misc ================= */

export function requireClient(ctx) {
  if (ctx.clientId) return Number(ctx.clientId);
  toast('Select a client in the top-left switcher first — this action is client-specific.', 'warn');
  return null;
}

export function clientName(ctx, id) {
  const c = (ctx.clients || []).find((x) => String(x.id) === String(id));
  return c ? c.name : (id ? `Client ${id}` : 'All clients');
}

export function navTo(hash) {
  window.location.hash = hash;
}
