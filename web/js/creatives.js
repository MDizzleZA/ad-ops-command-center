/* creatives.js — creative performance grid with badges, lazy sparklines,
   and hand-off buttons to the Brief Console and Cloner.
   Security note: every dynamic value is HTML-escaped via esc() before rendering. */

import {
  getJSON, postJSON, rangeParams,
  fmtR, fmtPct, esc, toast,
  skeleton, emptyState, viewHead,
  platformChip, BADGE_LABELS,
  mediaUrl, gradFor, initials, sparkline, onCleanup, navTo,
} from './api.js';

export default { render };

// Filters persist while the app is open (per-session, not in localStorage).
const filters = { platform: '', format: '', sort: 'spend' };

async function render(el, ctx) {
  el.innerHTML = viewHead('Creatives', `${ctx.dateFrom} to ${ctx.dateTo} · creative-level performance`) + `
    <div class="filter-bar">
      <select id="f-platform" class="select" aria-label="Platform">
        <option value="">All platforms</option>
        <option value="meta">Meta</option>
        <option value="google">Google</option>
        <option value="linkedin">LinkedIn</option>
        <option value="bing">Microsoft</option>
      </select>
      <select id="f-format" class="select" aria-label="Format">
        <option value="">All formats</option>
        <option value="image">Image</option>
        <option value="video">Video</option>
        <option value="carousel">Carousel</option>
      </select>
      <select id="f-sort" class="select" aria-label="Sort">
        <option value="spend">Sort: Spend</option>
        <option value="cpl">Sort: CPL</option>
        <option value="ctr">Sort: CTR</option>
      </select>
      <div class="grow"></div>
      <span class="muted small">Badges: <span class="badge winning">Winning</span> <span class="badge watch">Watch</span> <span class="badge fatiguing">Fatiguing</span></span>
    </div>
    <div id="creative-grid">${skeleton('cards', 6)}</div>`;

  const selPlatform = el.querySelector('#f-platform');
  const selFormat = el.querySelector('#f-format');
  const selSort = el.querySelector('#f-sort');
  selPlatform.value = filters.platform;
  selFormat.value = filters.format;
  selSort.value = filters.sort;

  const grid = el.querySelector('#creative-grid');
  const reload = () => {
    filters.platform = selPlatform.value;
    filters.format = selFormat.value;
    filters.sort = selSort.value;
    load(grid, ctx);
  };
  selPlatform.addEventListener('change', reload);
  selFormat.addEventListener('change', reload);
  selSort.addEventListener('change', reload);

  await load(grid, ctx);
}

async function load(grid, ctx) {
  grid.innerHTML = skeleton('cards', 6);
  const data = await getJSON('/api/creatives', rangeParams(ctx, {
    platform: filters.platform || undefined,
    format: filters.format || undefined,
    sort: filters.sort,
  }));
  const rows = data.creatives || [];

  if (!rows.length) {
    grid.innerHTML = emptyState(
      'image',
      'No creatives found',
      'Press Sync now to pull creatives from your ad platforms, or import a CSV under Settings. If you have filters set, try clearing them.',
    );
    return;
  }

  grid.innerHTML = `<div class="grid grid-cards">${rows.map(creativeCard).join('')}</div>`;

  // Action buttons (→ Brief / → Clone)
  grid.querySelectorAll('button[data-act]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        await sendToReference(btn.dataset.id, btn.dataset.act);
      } finally {
        btn.disabled = false;
      }
    });
  });

  // Lazy sparklines via IntersectionObserver
  const io = new IntersectionObserver((entries) => {
    for (const en of entries) {
      if (!en.isIntersecting) continue;
      io.unobserve(en.target);
      loadSpark(en.target, ctx);
    }
  }, { rootMargin: '120px' });
  grid.querySelectorAll('canvas[data-spark]').forEach((c) => io.observe(c));
  onCleanup(() => io.disconnect());
}

function creativeCard(c) {
  const thumbUrl = mediaUrl(c.thumbnail);
  const thumb = thumbUrl
    ? `<img src="${esc(thumbUrl)}" alt="" loading="lazy" onerror="this.remove()">`
    : `<div class="thumb-fallback" style="background:${gradFor(c.name)}">${esc(initials(c.name))}</div>`;
  const badge = c.badge
    ? `<span class="badge ${esc(c.badge)}" title="${esc(c.badge_reason || '')}">${esc(BADGE_LABELS[c.badge] || c.badge)}</span>`
    : '';
  return `
    <div class="card creative-card hoverable">
      <div class="creative-thumb">
        ${thumb}
        ${badge ? `<span class="thumb-badge">${badge}</span>` : ''}
      </div>
      <div class="creative-body">
        <div class="creative-name" title="${esc(c.name)}">${esc(c.name)}</div>
        <div class="flex flex-wrap">
          ${platformChip(c.platform)}
          ${c.format ? `<span class="muted small">${esc(c.format)}</span>` : ''}
          ${c.client_name ? `<span class="muted small">· ${esc(c.client_name)}</span>` : ''}
        </div>
        <div class="creative-metrics">
          <div><span class="m-label">Spend</span><span class="m-val">${fmtR(c.spend)}</span></div>
          <div><span class="m-label">CPL</span><span class="m-val">${fmtR(c.cpl)}</span></div>
          <div><span class="m-label">CTR</span><span class="m-val">${fmtPct(c.ctr)}</span></div>
        </div>
        <div class="spark-wrap"><canvas data-spark data-id="${esc(c.id)}"></canvas></div>
        <div class="card-actions">
          <button class="btn btn-sm" data-act="brief" data-id="${esc(c.id)}">&rarr; Brief</button>
          <button class="btn btn-sm" data-act="cloner" data-id="${esc(c.id)}">&rarr; Clone</button>
        </div>
      </div>
    </div>`;
}

async function loadSpark(canvas, ctx) {
  const id = canvas.dataset.id;
  let data = null;
  try {
    data = await getJSON(`/api/creatives/${id}/timeseries`, {
      date_from: ctx.dateFrom, date_to: ctx.dateTo,
    }, { silent: true });
  } catch { /* sparkline is decoration — fail quietly */ }
  const series = (data && data.series) || [];
  if (!series.length) {
    const wrap = canvas.parentElement;
    if (wrap) {
      wrap.textContent = 'No daily data';
      wrap.className = 'spark-wrap muted small';
      wrap.style.display = 'flex';
      wrap.style.alignItems = 'center';
    }
    return;
  }
  const sorted = [...series].sort((a, b) => String(a.date).localeCompare(String(b.date)));
  sparkline(canvas, sorted.map((r) => r.spend || 0));
}

async function sendToReference(id, target) {
  const res = await postJSON(`/api/creatives/${id}/to-reference`, { target: target === 'cloner' ? 'cloner' : 'brief' });
  if (target === 'cloner') {
    if (res.clone_job_id) {
      navTo(`#cloner?job=${res.clone_job_id}`);
    } else if (res.reference_ad_id) {
      navTo(`#cloner?ref=${res.reference_ad_id}`);
    } else {
      toast('Saved as reference, but no clone job was created.', 'warn');
    }
  } else if (res.reference_ad_id) {
    navTo(`#briefs?ref=${res.reference_ad_id}`);
  } else {
    toast('Saved as reference ad.', 'success');
  }
}
