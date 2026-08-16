/* pipeline.js — daily psychology-based creative pipeline: awareness-stage ad
   batches with 3-ratio image generation, thumbs feedback loop, product-asset
   compositing, and the data-driven persona miner.
   Security note: every dynamic value is HTML-escaped via esc() before rendering,
   matching the app-wide escape-then-render convention. */

import {
  getJSON, postJSON, postForm, fetchJSON,
  esc, toast, modal,
  skeleton, emptyState, viewHead, statusBadge,
  requireClient, pollUntil, navTo,
} from './api.js';

export default { render };

const STAGE_LABELS = {
  unaware: 'Unaware', problem_aware: 'Problem aware', solution_aware: 'Solution aware',
  product_aware: 'Product aware', most_aware: 'Most aware',
};
const STAGE_COLORS = {
  unaware: '#8A93A6', problem_aware: '#FBBF24', solution_aware: '#22D3EE',
  product_aware: '#A78BFA', most_aware: '#4ADE80',
};

function stageChip(s) {
  const color = STAGE_COLORS[s] || '#8A93A6';
  return `<span class="badge" style="background:${color}22;color:${color};">${esc(STAGE_LABELS[s] || s)}</span>`;
}

async function render(el, ctx) {
  el.innerHTML = viewHead(
    'Daily Pipeline',
    'Psychology-based ad batches across the five stages of customer awareness — rate them to teach the model your taste',
    `<div class="flex" style="gap:8px;">
       <button id="btn-products" class="btn">Product images</button>
       <button id="btn-personas" class="btn">Mine personas</button>
       <button id="btn-generate" class="btn btn-primary">Generate today's batch</button>
     </div>`,
  ) + `
    <div class="tabs">
      <button data-tab="ads" class="active">Ad batches</button>
      <button data-tab="personas">Personas</button>
    </div>
    <div id="tab-ads"><div id="pipe-ads">${skeleton('cards', 3)}</div></div>
    <div id="tab-personas" class="hidden"><div id="pipe-personas">${skeleton('rows', 2)}</div></div>`;

  const clientId = ctx.clientId ? Number(ctx.clientId) : null;

  el.querySelectorAll('.tabs button').forEach((b) => {
    b.addEventListener('click', () => {
      el.querySelectorAll('.tabs button').forEach((x) => x.classList.toggle('active', x === b));
      el.querySelector('#tab-ads').classList.toggle('hidden', b.dataset.tab !== 'ads');
      el.querySelector('#tab-personas').classList.toggle('hidden', b.dataset.tab !== 'personas');
    });
  });

  el.querySelector('#btn-generate').addEventListener('click', () => startBatch(el, ctx));
  el.querySelector('#btn-personas').addEventListener('click', () => minePersonas(el, ctx));
  el.querySelector('#btn-products').addEventListener('click', () => manageProducts(ctx));

  if (!clientId) {
    el.querySelector('#pipe-ads').innerHTML = emptyState(
      'alert', 'Pick a client', 'Select a client in the top bar to see their daily ad batches.');
    el.querySelector('#pipe-personas').innerHTML = '';
    return;
  }
  await Promise.all([loadAds(el, ctx, clientId), loadPersonas(el, ctx, clientId)]);
}

/* ================= Ad batches ================= */

async function loadAds(el, ctx, clientId) {
  const host = el.querySelector('#pipe-ads');
  if (!host) return;
  let data;
  try {
    data = await getJSON('/api/pipeline/ads', { client_id: clientId }, { silent: true });
  } catch {
    host.innerHTML = emptyState('alert', 'Could not load', 'Backend unreachable.');
    return;
  }
  const ads = data.ads || [];
  if (data.generating) {
    host.innerHTML = `
      <div class="progress-panel card">
        <div class="spinner spinner-lg"></div>
        <div>Generating today's batch — copy first, then compliance checks…</div>
        <div class="small">Usually 30–90 seconds. Images can be generated per ad afterwards.</div>
      </div>`;
    pollUntil(async () => {
      const d = await getJSON('/api/pipeline/ads', { client_id: clientId }, { silent: true });
      if (d && !d.generating) {
        loadAds(el, ctx, clientId);
        return true;
      }
      return false;
    }, 4000);
    return;
  }
  if (!ads.length) {
    host.innerHTML = emptyState('file', 'No ads yet',
      'Generate the first batch — five ads, one per awareness stage, written from the brand profile, personas and compliance rules.');
    return;
  }

  // group by batch_date
  const groups = new Map();
  for (const ad of ads) {
    if (!groups.has(ad.batch_date)) groups.set(ad.batch_date, []);
    groups.get(ad.batch_date).push(ad);
  }

  host.innerHTML = [...groups.entries()].map(([day, list]) => `
    <div class="section">
      <div class="section-head"><h2>${esc(day)}</h2><span class="muted small">${list.length} ads</span></div>
      <div class="grid grid-cards">
        ${list.map((ad) => adCard(ad)).join('')}
      </div>
    </div>`).join('');

  wireAdCards(host, el, ctx, clientId);
}

function adCard(ad) {
  const img = ad.image_1x1 || ad.image_4x5 || ad.image_9x16;
  const comp = ad.compliance_status;
  return `
    <div class="card ad-card" data-ad-id="${esc(ad.id)}">
      ${img ? `<div class="ad-media"><img src="${esc(img)}" alt="" loading="lazy" onerror="this.remove()"></div>` : ''}
      <div class="ad-body">
        <div class="ad-meta-row">
          ${stageChip(ad.awareness_stage)}
          ${comp ? statusBadge(comp) : '<span class="muted small">unchecked</span>'}
          ${ad.feedback === 1 ? '<span class="badge done">👍</span>' : ''}
          ${ad.feedback === -1 ? '<span class="badge error">👎</span>' : ''}
        </div>
        ${ad.angle ? `<div class="muted small">${esc(ad.angle)}</div>` : ''}
        <div class="ad-headline">${esc(ad.headline || '')}</div>
        <div class="ad-text">${esc((ad.primary_text || '').slice(0, 220))}${(ad.primary_text || '').length > 220 ? '…' : ''}</div>
        <div class="ad-meta-row">
          ${ad.cta ? `<span class="chip">${esc(ad.cta)}</span>` : ''}
          ${ad.description ? `<span class="muted small">${esc(ad.description.slice(0, 60))}</span>` : ''}
        </div>
        <div class="ad-meta-row muted small">
          Images:
          ${['1x1', '4x5', '9x16'].map((r) => (ad[`image_${r}`]
            ? `<a href="${esc(ad[`image_${r}`])}" target="_blank">${r}</a>`
            : `<button class="btn btn-sm" data-genimg="${esc(ad.id)}" data-ratio="${r}">${r}</button>`)).join(' ')}
        </div>
        <div class="card-actions">
          <button class="btn btn-sm" data-vote="1" data-ad="${esc(ad.id)}" title="More like this">👍</button>
          <button class="btn btn-sm" data-vote="-1" data-ad="${esc(ad.id)}" title="Avoid this pattern">👎</button>
          <button class="btn btn-sm" data-brief="${esc(ad.id)}">&rarr; Brief</button>
        </div>
      </div>
    </div>`;
}

function wireAdCards(host, el, ctx, clientId) {
  host.querySelectorAll('button[data-vote]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const vote = Number(btn.dataset.vote);
      let note = null;
      if (vote === -1) note = window.prompt('What should the model avoid here? (optional)') || null;
      btn.disabled = true;
      try {
        await postJSON(`/api/pipeline/ads/${btn.dataset.ad}/feedback`, { vote, note });
        toast(vote === 1 ? 'Noted — more like this.' : 'Noted — pattern flagged to avoid.', 'success');
        loadAds(el, ctx, clientId);
      } finally { btn.disabled = false; }
    });
  });
  host.querySelectorAll('button[data-genimg]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = '…';
      try {
        await postJSON(`/api/pipeline/ads/${btn.dataset.genimg}/image`, { ratio: btn.dataset.ratio });
        toast(`${btn.dataset.ratio} image generated.`, 'success');
        loadAds(el, ctx, clientId);
      } catch {
        btn.disabled = false;
        btn.textContent = btn.dataset.ratio;
      }
    });
  });
  host.querySelectorAll('button[data-brief]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        const res = await postJSON(`/api/pipeline/ads/${btn.dataset.brief}/to-brief`);
        if (res.brief_id) navTo(`#briefs?id=${res.brief_id}`);
      } finally { btn.disabled = false; }
    });
  });
}

async function startBatch(el, ctx) {
  const clientId = requireClient(ctx);
  if (!clientId) return;
  try {
    await postJSON('/api/pipeline/generate', { client_id: Number(clientId) });
    toast('Batch generation started.');
    loadAds(el, ctx, Number(clientId));
  } catch { /* fetchJSON already toasts */ }
}

/* ================= Personas ================= */

async function loadPersonas(el, ctx, clientId) {
  const host = el.querySelector('#pipe-personas');
  if (!host) return;
  let data;
  try {
    data = await getJSON('/api/personas', { client_id: clientId }, { silent: true });
  } catch {
    host.innerHTML = '<p class="muted small">Could not load personas.</p>';
    return;
  }
  const personas = data.personas || [];
  const miningNote = data.mining
    ? '<div class="progress-panel card"><div class="spinner"></div><div>Mining Reddit + review data… this can take a few minutes.</div></div>'
    : '';
  if (data.mining) {
    pollUntil(async () => {
      const d = await getJSON('/api/personas', { client_id: clientId }, { silent: true });
      if (d && !d.mining) { loadPersonas(el, ctx, clientId); return true; }
      return false;
    }, 5000);
  }
  if (!personas.length) {
    host.innerHTML = miningNote + emptyState('search', 'No personas yet',
      'Mine data-driven personas from Reddit discussions and review pages, or seed them from the vault.');
    return;
  }
  host.innerHTML = miningNote + `
    <div class="grid grid-cards">
      ${personas.map((p) => `
        <div class="card">
          <div class="section-head"><h2>${esc(p.name)}</h2>
            <button class="btn btn-sm" data-del-persona="${esc(p.id)}" title="Delete">✕</button></div>
          <p class="small">${esc(p.headline || '')}</p>
          ${p.source_path && String(p.source_path).startsWith('miner:')
            ? `<span class="chip">mined ${esc(String(p.source_path).slice(6))}</span>` : ''}
          ${block('Demographics', p.demographics)}
          ${block('Pain points', p.pain_points)}
          ${block('Triggers', p.triggers)}
          ${block('Objections', p.objections)}
        </div>`).join('')}
    </div>`;
  host.querySelectorAll('button[data-del-persona]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!window.confirm('Delete this persona?')) return;
      await fetchJSON(`/api/personas/${btn.dataset.delPersona}`, { method: 'DELETE' });
      loadPersonas(el, ctx, clientId);
    });
  });
}

function block(title, items) {
  if (!items || !items.length) return '';
  return `<div class="mt-8"><strong class="small">${esc(title)}</strong>
    <ul class="small muted" style="margin:4px 0 0 16px;">
      ${items.slice(0, 5).map((i) => `<li>${esc(i)}</li>`).join('')}
    </ul></div>`;
}

function minePersonas(el, ctx) {
  const clientId = requireClient(ctx);
  if (!clientId) return;
  const m = modal('Mine buyer personas', `
    <p class="muted small mb-16">Scrapes Reddit discussions (via Apify) and any review pages you list,
    then distils structured personas with Gemini. Mined personas feed the Brief Console and Daily Pipeline.</p>
    <div class="mb-16">
      <label class="field-label" for="pm-keywords">Reddit search keywords (comma-separated)</label>
      <input id="pm-keywords" class="input w-100" type="text" placeholder="e.g. living annuity, retirement income South Africa">
    </div>
    <div class="mb-16">
      <label class="field-label" for="pm-urls">Review page URLs (optional, one per line)</label>
      <textarea id="pm-urls" class="input" rows="3" placeholder="https://www.hellopeter.com/..."></textarea>
    </div>
    <div class="mb-16">
      <label class="field-label" for="pm-count">Personas to build</label>
      <input id="pm-count" class="input" type="number" value="3" min="1" max="6" style="width:80px;">
    </div>
    <button id="pm-start" class="btn btn-primary">Start mining</button>`);
  m.body.querySelector('#pm-start').addEventListener('click', async () => {
    const keywords = m.body.querySelector('#pm-keywords').value.trim();
    if (!keywords) { toast('Give at least one search keyword.', 'warn'); return; }
    const btn = m.body.querySelector('#pm-start');
    btn.disabled = true;
    try {
      await postJSON('/api/personas/mine', {
        client_id: Number(clientId),
        keywords,
        review_urls: m.body.querySelector('#pm-urls').value.trim(),
        count: Number(m.body.querySelector('#pm-count').value) || 3,
      });
      toast('Mining started — check the Personas tab in a few minutes.', 'success');
      m.close();
      loadPersonas(el, ctx, Number(clientId));
    } catch {
      btn.disabled = false;
    }
  });
}

/* ================= Product images ================= */

async function manageProducts(ctx) {
  const clientId = requireClient(ctx);
  if (!clientId) return;
  const m = modal('Product images for compositing', `
    <p class="muted small mb-16">Uploaded product shots are passed to the image model as references,
    so generated visuals feature the real product (shape, label, colours preserved).</p>
    <div id="prod-list" class="mb-16">${skeleton('rows', 1)}</div>
    <input id="prod-file" class="input w-100" type="file" accept="image/*">`);

  const refresh = async () => {
    const listEl = m.body.querySelector('#prod-list');
    let items = [];
    try {
      items = await getJSON('/api/pipeline/product-images', { client_id: clientId }, { silent: true });
    } catch { items = []; }
    listEl.innerHTML = items.length
      ? `<div class="flex" style="flex-wrap:wrap;gap:8px;">
          ${items.map((it) => `
            <div style="position:relative;">
              <img src="${esc(it.url || '')}" alt="" style="width:84px;height:84px;object-fit:cover;border-radius:8px;">
              <button class="btn btn-sm" data-del="${esc(it.path)}" style="position:absolute;top:2px;right:2px;">✕</button>
            </div>`).join('')}
        </div>`
      : '<p class="muted small">No product images yet.</p>';
    listEl.querySelectorAll('button[data-del]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        await fetchJSON(`/api/pipeline/product-images?client_id=${clientId}&path=${encodeURIComponent(btn.dataset.del)}`,
          { method: 'DELETE' });
        refresh();
      });
    });
  };
  await refresh();

  m.body.querySelector('#prod-file').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    fd.append('client_id', clientId);
    try {
      await postForm('/api/pipeline/product-images', fd);
      toast('Product image added.', 'success');
      refresh();
    } catch { /* toasted */ }
  });
}
