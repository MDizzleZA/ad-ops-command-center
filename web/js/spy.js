/* spy.js — Ad Library keyword search across Meta, Google and LinkedIn, results
   grid with save/brief/clone, manual reference-ad entry, and recent search runs.
   Security note: every dynamic value is HTML-escaped via esc() before rendering. */

import {
  getJSON, postJSON, postForm,
  esc, toast, modal,
  skeleton, emptyState, viewHead, statusBadge,
  fmtN, fmtDateTime,
  refAdCard, requireClient, pollUntil, navTo,
} from './api.js';

export default { render };

async function render(el, ctx) {
  el.innerHTML = viewHead(
    'Ad Spy',
    'Search the Meta, Google and LinkedIn ad libraries for any keyword and bank the best ads as references',
    '<button id="btn-manual" class="btn">Add manually</button>',
  ) + `
    <div class="filter-bar card" style="padding: 12px 14px;">
      <select id="spy-platform" class="input" style="width: 130px;" aria-label="Ad platform">
        <option value="meta">Meta</option>
        <option value="google">Google</option>
        <option value="linkedin">LinkedIn</option>
      </select>
      <input id="spy-q" class="input" type="search" placeholder="Keyword or brand — e.g. fixed income investment" style="flex: 1; min-width: 220px;">
      <input id="spy-country" class="input" type="text" value="ZA" maxlength="2" style="width: 64px; text-transform: uppercase;" aria-label="Country code">
      <label class="flex muted small" style="gap: 6px;">
        <input id="spy-active" type="checkbox" checked> Active only
      </label>
      <button id="btn-search" class="btn btn-primary">Search</button>
    </div>
    <p id="spy-hint" class="muted small" style="margin: 6px 2px 0;"></p>

    <div id="spy-results" class="section"></div>

    <div class="section">
      <div class="section-head"><h2>Recent searches</h2></div>
      <div id="spy-runs">${skeleton('rows', 3)}</div>
    </div>`;

  const resultsEl = el.querySelector('#spy-results');
  resultsEl.innerHTML = emptyState(
    'search',
    'Search the Ad Library',
    'Enter a keyword above (country defaults to ZA) and press Search. Results can be saved as reference ads or pushed straight to a brief or clone job.',
  );

  const doSearch = () => search(el, ctx);
  el.querySelector('#btn-search').addEventListener('click', doSearch);
  el.querySelector('#spy-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });
  el.querySelector('#btn-manual').addEventListener('click', () => addManually(el, ctx));

  const platformSel = el.querySelector('#spy-platform');
  platformSel.addEventListener('change', () => applyPlatform(el));
  applyPlatform(el);

  await loadRuns(el, ctx);

  if (ctx.query.run) {
    showRun(el, ctx, ctx.query.run);
  }
}

/* ================= Platform-specific copy ================= */

const PLATFORM_META = {
  meta: {
    label: 'Meta',
    placeholder: 'Keyword or brand — e.g. fixed income investment',
    hint: 'Searches the Meta Ad Library by keyword. "Active only" hides ended ads.',
  },
  google: {
    label: 'Google',
    placeholder: 'Advertiser, brand or domain — e.g. Acme Corp or psg.co.za',
    hint: 'Searches the Google Ads Transparency Center by advertiser/domain (Search, YouTube, Display, Shopping). "Active only" limits to ads shown in the last 90 days.',
  },
  linkedin: {
    label: 'LinkedIn',
    placeholder: 'Company or keyword — e.g. Old Mutual',
    hint: 'Searches the LinkedIn Ad Library by company or keyword. "Active only" limits to the last 30 days.',
  },
};

function currentPlatform(el) {
  return el.querySelector('#spy-platform').value || 'meta';
}

function applyPlatform(el) {
  const meta = PLATFORM_META[currentPlatform(el)] || PLATFORM_META.meta;
  el.querySelector('#spy-q').placeholder = meta.placeholder;
  el.querySelector('#spy-hint').textContent = meta.hint;
}

/* ================= Search + polling ================= */

async function search(el, ctx) {
  const q = el.querySelector('#spy-q').value.trim();
  if (!q) { toast('Type a keyword to search for.', 'warn'); return; }
  const platform = currentPlatform(el);
  const platformLabel = (PLATFORM_META[platform] || PLATFORM_META.meta).label;
  const country = (el.querySelector('#spy-country').value.trim() || 'ZA').toUpperCase();
  const activeOnly = el.querySelector('#spy-active').checked;

  const btn = el.querySelector('#btn-search');
  const resultsEl = el.querySelector('#spy-results');
  btn.disabled = true;
  resultsEl.innerHTML = `
    <div class="progress-panel card">
      <div class="spinner spinner-lg"></div>
      <div>Searching the ${esc(platformLabel)} Ad Library for <strong>${esc(q)}</strong> (${esc(country)})…</div>
      <div class="small">Scraper runs usually finish within a minute or two.</div>
    </div>`;

  let started;
  try {
    started = await postJSON('/api/spy/search', { query: q, country, active_only: activeOnly, platform });
  } catch {
    btn.disabled = false;
    resultsEl.innerHTML = emptyState('search', 'Search failed to start', 'Check the Apify settings under Settings, then try again.');
    return;
  }

  pollUntil(async () => {
    const data = await getJSON(`/api/spy/runs/${started.run_id}`, null, { silent: true });
    const run = (data && data.run) || {};
    if (run.status === 'running') return false;
    btn.disabled = false;
    if (run.status === 'error') {
      resultsEl.innerHTML = emptyState('alert', 'Search failed', run.error || 'The scraper run errored — check Apify credits and settings.');
    } else {
      const ads = (data && data.ads) || [];
      renderResults(resultsEl, ads, ctx);
      toast(`Search done — ${ads.length} ads found.`, 'success');
      loadRuns(el, ctx);
    }
    return true;
  }, 4000);
}

function renderResults(host, ads, ctx) {
  if (!ads.length) {
    host.innerHTML = emptyState(
      'search',
      'No ads found',
      'Try a broader keyword, a different country code, or untick Active only to include ended ads.',
    );
    return;
  }
  host.innerHTML = `<div class="grid grid-cards-sm">
    ${ads.map((ad) => refAdCard(ad, {
      actions: `
        <button class="btn btn-sm" data-act="save" data-ad="${esc(ad.id)}">Save</button>
        <button class="btn btn-sm" data-act="brief" data-ad="${esc(ad.id)}">&rarr; Brief</button>
        <button class="btn btn-sm" data-act="cloner" data-ad="${esc(ad.id)}">&rarr; Clone</button>`,
    })).join('')}
  </div>`;

  const byId = new Map(ads.map((a) => [String(a.id), a]));
  host.querySelectorAll('button[data-act]').forEach((btn) => {
    btn.addEventListener('click', () => handleAction(btn, byId.get(btn.dataset.ad), ctx));
  });
}

async function handleAction(btn, ad, ctx) {
  if (!ad) return;
  const act = btn.dataset.act;
  btn.disabled = true;
  try {
    if (act === 'save') {
      await postJSON('/api/reference-ads', {
        client_id: ctx.clientId ? Number(ctx.clientId) : undefined,
        page_name: ad.page_name,
        headline: ad.headline,
        body: ad.body,
        cta: ad.cta,
        media_url: ad.media || undefined,
      });
      toast('Saved to the reference library.', 'success');
      btn.textContent = 'Saved ✓';
      return;
    }
    const clientId = requireClient(ctx);
    if (!clientId) return;
    const res = await postJSON('/api/spy/save', {
      reference_ad_id: Number(ad.id),
      target: act === 'cloner' ? 'cloner' : 'brief',
      client_id: clientId,
    });
    if (act === 'cloner' && res.clone_job_id) navTo(`#cloner?job=${res.clone_job_id}`);
    else if (act === 'brief') navTo(`#briefs?ref=${ad.id}`);
  } finally {
    btn.disabled = false;
  }
}

/* ================= Recent runs ================= */

async function loadRuns(el, ctx) {
  const host = el.querySelector('#spy-runs');
  if (!host) return;
  let runs = [];
  try {
    runs = await getJSON('/api/spy/runs', null, { silent: true });
  } catch {
    host.innerHTML = '<p class="muted small">Could not load recent runs.</p>';
    return;
  }
  if (!runs || !runs.length) {
    host.innerHTML = '<p class="muted small">No searches yet — your run history will appear here.</p>';
    return;
  }
  host.innerHTML = `
    <div class="table-wrap">
      <table class="table">
        <thead><tr><th>Purpose</th><th>Status</th><th class="num">Items</th><th>Started</th><th></th></tr></thead>
        <tbody>
          ${runs.map((r) => `
            <tr>
              <td>${esc(r.purpose || '—')}</td>
              <td>${statusBadge(r.status)}${r.error ? ` <span class="muted small" title="${esc(r.error)}">error</span>` : ''}</td>
              <td class="num">${fmtN(r.items)}</td>
              <td class="muted small">${esc(fmtDateTime(r.started_at))}</td>
              <td class="right">${r.status === 'done' ? `<button class="btn btn-sm" data-run="${esc(r.id)}">View results</button>` : ''}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
  host.querySelectorAll('button[data-run]').forEach((b) => {
    b.addEventListener('click', () => showRun(el, ctx, b.dataset.run));
  });
}

async function showRun(el, ctx, runId) {
  const resultsEl = el.querySelector('#spy-results');
  resultsEl.innerHTML = skeleton('cards', 3);
  try {
    const data = await getJSON(`/api/spy/runs/${runId}`);
    renderResults(resultsEl, data.ads || [], ctx);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch {
    resultsEl.innerHTML = emptyState('alert', 'Could not load run', 'The run may have been cleaned up.');
  }
}

/* ================= Manual add ================= */

function addManually(el, ctx) {
  const m = modal('Add a reference ad manually', `
    <div class="mb-16">
      <label class="field-label" for="ma-page">Page / brand name</label>
      <input id="ma-page" class="input w-100" type="text" placeholder="e.g. Acme Corp">
    </div>
    <div class="mb-16">
      <label class="field-label" for="ma-headline">Headline</label>
      <input id="ma-headline" class="input w-100" type="text">
    </div>
    <div class="mb-16">
      <label class="field-label" for="ma-body">Body copy</label>
      <textarea id="ma-body" class="input" rows="4"></textarea>
    </div>
    <div class="mb-16">
      <label class="field-label" for="ma-cta">CTA</label>
      <input id="ma-cta" class="input w-100" type="text" placeholder="e.g. Learn more">
    </div>
    <div class="mb-16">
      <label class="field-label" for="ma-file">Image (optional)</label>
      <input id="ma-file" class="input w-100" type="file" accept="image/*">
    </div>
    <div class="flex">
      <button id="ma-save" class="btn btn-primary">Save reference ad</button>
      <span class="muted small">${ctx.clientId ? 'Will be linked to the selected client.' : 'No client selected — saved unassigned.'}</span>
    </div>
  `);

  m.body.querySelector('#ma-save').addEventListener('click', async () => {
    const page = m.body.querySelector('#ma-page').value.trim();
    if (!page) { toast('Give the ad a page or brand name.', 'warn'); return; }
    const headline = m.body.querySelector('#ma-headline').value.trim();
    const body = m.body.querySelector('#ma-body').value.trim();
    const cta = m.body.querySelector('#ma-cta').value.trim();
    const file = m.body.querySelector('#ma-file').files[0];
    const saveBtn = m.body.querySelector('#ma-save');
    saveBtn.disabled = true;
    try {
      if (file) {
        const fd = new FormData();
        fd.append('file', file);
        fd.append('page_name', page);
        fd.append('headline', headline);
        fd.append('body', body);
        fd.append('cta', cta);
        if (ctx.clientId) fd.append('client_id', ctx.clientId);
        await postForm('/api/reference-ads/upload', fd);
      } else {
        await postJSON('/api/reference-ads', {
          client_id: ctx.clientId ? Number(ctx.clientId) : undefined,
          page_name: page, headline, body, cta,
        });
      }
      toast('Reference ad saved.', 'success');
      m.close();
    } catch {
      saveBtn.disabled = false;
    }
  });
}
