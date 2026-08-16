/* cloner.js — 3-step ad-clone wizard: ingest → layout review → generate,
   plus past jobs and the generated-assets gallery.
   Security note: every dynamic value is HTML-escaped via esc() before rendering. */

import {
  getJSON, postJSON, postForm,
  esc, toast, modal,
  skeleton, emptyState, viewHead, statusBadge,
  fmtDateTime, mediaUrl, requireClient, clientName,
} from './api.js';

export default { render };

// Wizard state — survives re-renders within the session.
const wiz = {
  step: 1,
  job: null,        // { job_id, layout, source_image }
  blocks: [],
  results: [],
};

function resetWiz() {
  wiz.step = 1;
  wiz.job = null;
  wiz.blocks = [];
  wiz.results = [];
}

async function render(el, ctx) {
  // Deep links: #cloner?job=<id> resumes a job; #cloner?ref=<id> ingests a reference ad.
  if (ctx.query.job && (!wiz.job || String(wiz.job.job_id) !== String(ctx.query.job))) {
    await resumeJob(ctx.query.job, ctx);
  }

  el.innerHTML = viewHead(
    'Cloner',
    `${clientName(ctx, ctx.clientId)} · rebuild a winning layout with your own offer`,
  ) + `
    <div class="steps">
      ${stepPill(1, 'Ingest')}
      ${stepPill(2, 'Layout')}
      ${stepPill(3, 'Generate')}
    </div>
    <div id="wiz" class="section"></div>

    <div class="section">
      <div class="section-head"><h2>Past jobs</h2></div>
      <div id="jobs-list">${skeleton('rows', 2)}</div>
    </div>

    <div class="section">
      <div class="section-head"><h2>Generated assets</h2></div>
      <div id="assets-gallery">${skeleton('rows', 2)}</div>
    </div>`;

  renderStep(el, ctx);
  loadJobs(el, ctx);
  loadAssets(el, ctx);

  if (ctx.query.ref && !wiz.job) {
    ingest(el, ctx, { reference_ad_id: Number(ctx.query.ref) });
  }
}

function stepPill(n, label) {
  const cls = wiz.step === n ? 'active' : (wiz.step > n ? 'done' : '');
  return `<div class="step-pill ${cls}" data-step="${n}"><span class="step-n">${wiz.step > n ? '✓' : n}</span>${esc(label)}</div>`;
}

function refreshPills(el) {
  el.querySelectorAll('.step-pill').forEach((p) => {
    const n = Number(p.dataset.step);
    p.classList.toggle('active', wiz.step === n);
    p.classList.toggle('done', wiz.step > n);
    p.querySelector('.step-n').textContent = wiz.step > n ? '✓' : String(n);
  });
}

function renderStep(el, ctx) {
  refreshPills(el);
  const host = el.querySelector('#wiz');
  if (wiz.step === 1) renderStep1(host, el, ctx);
  else if (wiz.step === 2) renderStep2(host, el, ctx);
  else renderStep3(host, el, ctx);
}

/* ================= Step 1 — Ingest ================= */

function renderStep1(host, el, ctx) {
  host.innerHTML = `
    <div class="card">
      <div class="dropzone" id="dropzone">
        <p><strong>Drop an ad image here</strong> or click to browse</p>
        <p class="small">PNG or JPG of the ad you want to clone. The layout is analysed automatically.</p>
      </div>
      <input type="file" id="file-input" accept="image/*" class="hidden">
      <div class="or-sep">or paste an image URL</div>
      <div class="flex">
        <input type="url" id="url-input" class="input w-100" placeholder="https://…/competitor-ad.png">
        <button id="url-go" class="btn">Ingest URL</button>
      </div>
      <div class="or-sep">or start from a saved reference ad</div>
      <button id="pick-ref" class="btn">Pick a reference ad…</button>
      <div id="ingest-progress" class="hidden progress-panel">
        <div class="spinner spinner-lg"></div>
        <div>Analysing the layout…</div>
        <div class="small">This usually takes 15–40 seconds.</div>
      </div>
    </div>`;

  const dz = host.querySelector('#dropzone');
  const fileInput = host.querySelector('#file-input');
  dz.addEventListener('click', () => fileInput.click());
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('dragover'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
  dz.addEventListener('drop', (e) => {
    e.preventDefault();
    dz.classList.remove('dragover');
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) ingest(el, ctx, { file: f });
  });
  fileInput.addEventListener('change', () => {
    const f = fileInput.files && fileInput.files[0];
    if (f) ingest(el, ctx, { file: f });
  });

  host.querySelector('#url-go').addEventListener('click', () => {
    const url = host.querySelector('#url-input').value.trim();
    if (!url) { toast('Paste an image URL first.', 'warn'); return; }
    ingest(el, ctx, { url });
  });

  host.querySelector('#pick-ref').addEventListener('click', () => pickReference(el, ctx));
}

async function pickReference(el, ctx) {
  let ads = [];
  try {
    ads = await getJSON('/api/reference-ads', { client_id: ctx.clientId || undefined });
  } catch { return; }
  if (!ads.length) {
    toast('No reference ads saved yet — grab some via Ad Spy or Creatives first.', 'warn');
    return;
  }
  const m = modal('Pick a reference ad', `
    <div class="ref-list">
      ${ads.map((a) => `
        <div class="ref-item" data-ref="${esc(a.id)}">
          <div class="ref-page">${esc(a.page_name || 'Untitled')}</div>
          ${a.headline ? `<div class="ref-headline">${esc(a.headline)}</div>` : ''}
        </div>`).join('')}
    </div>`);
  m.body.querySelectorAll('.ref-item').forEach((item) => {
    item.addEventListener('click', () => {
      m.close();
      ingest(el, ctx, { reference_ad_id: Number(item.dataset.ref) });
    });
  });
}

async function ingest(el, ctx, source) {
  const progress = el.querySelector('#ingest-progress');
  if (progress) progress.classList.remove('hidden');

  let res;
  try {
    if (source.file) {
      const fd = new FormData();
      fd.append('file', source.file);
      res = await postForm('/api/cloner/ingest', fd);
    } else if (source.url) {
      res = await postJSON('/api/cloner/ingest', { url: source.url });
    } else {
      res = await postJSON('/api/cloner/ingest', { reference_ad_id: source.reference_ad_id });
    }
  } catch {
    if (progress) progress.classList.add('hidden');
    return;
  }

  wiz.job = res;
  wiz.blocks = (res.layout && res.layout.blocks) || [];
  wiz.results = [];
  wiz.step = 2;
  toast('Layout analysed — review the blocks, then generate.', 'success');
  renderStep(el, ctx);
  loadJobs(el, ctx);
}

async function resumeJob(jobId, ctx) {
  try {
    const jobs = await getJSON('/api/cloner/jobs', { client_id: ctx.clientId || undefined }, { silent: true });
    const job = (jobs || []).find((j) => String(j.id) === String(jobId));
    if (job) {
      wiz.job = { job_id: job.id, layout: job.layout, source_image: job.source_image };
      wiz.blocks = (job.layout && job.layout.blocks) || [];
      wiz.results = [];
      wiz.step = 2;
    }
  } catch { /* job list unavailable — stay on step 1 */ }
}

/* ================= Step 2 — Layout review ================= */

function renderStep2(host, el, ctx) {
  const layout = (wiz.job && wiz.job.layout) || {};
  const src = mediaUrl(wiz.job && wiz.job.source_image);
  host.innerHTML = `
    <div class="two-col">
      <div class="card">
        <div class="pane-title">Source</div>
        ${src ? `<img class="source-img" src="${esc(src)}" alt="Source ad">` : '<p class="muted small">No source image for this job.</p>'}
        ${layout.canvas_ratio ? `<p class="muted small mt-8">Canvas ratio: <strong>${esc(layout.canvas_ratio)}</strong></p>` : ''}
        ${layout.color_scheme ? `<p class="muted small">Colours: ${esc(typeof layout.color_scheme === 'string' ? layout.color_scheme : JSON.stringify(layout.color_scheme))}</p>` : ''}
        ${layout.composition_notes ? `<p class="muted small">${esc(layout.composition_notes)}</p>` : ''}
      </div>
      <div class="card">
        <div class="pane-title">Layout blocks — edit before generating</div>
        <div id="blocks">
          ${wiz.blocks.length ? wiz.blocks.map((b, i) => blockItem(b, i)).join('')
            : '<p class="muted small">No blocks detected — you can still continue and rely on the offer text.</p>'}
        </div>
        <div class="flex mt-16">
          <button id="back-1" class="btn btn-ghost">&larr; Start over</button>
          <div style="flex: 1;"></div>
          <button id="to-3" class="btn btn-primary">Continue &rarr;</button>
        </div>
      </div>
    </div>`;

  host.querySelectorAll('[data-bi]').forEach((input) => {
    input.addEventListener('input', () => {
      const i = Number(input.dataset.bi);
      const field = input.dataset.field;
      if (wiz.blocks[i]) wiz.blocks[i][field] = input.value;
    });
  });
  host.querySelector('#back-1').addEventListener('click', () => { resetWiz(); renderStep(el, ctx); });
  host.querySelector('#to-3').addEventListener('click', () => { wiz.step = 3; renderStep(el, ctx); });
}

function blockItem(b, i) {
  return `
    <div class="block-item">
      <div class="block-type">${esc(b.type || 'block')}${b.position ? ` · ${esc(b.position)}` : ''}</div>
      <textarea class="input" rows="2" data-bi="${i}" data-field="content" placeholder="Content">${esc(b.content || '')}</textarea>
      <input class="input" type="text" data-bi="${i}" data-field="style_notes" placeholder="Style notes" value="${esc(b.style_notes || '')}">
    </div>`;
}

/* ================= Step 3 — Generate ================= */

function renderStep3(host, el, ctx) {
  host.innerHTML = `
    <div class="card">
      <label class="field-label" for="offer-text">Your offer</label>
      <textarea id="offer-text" class="input" placeholder="e.g. Earn up to 12% p.a. fixed income — minimum investment R100 000. T&amp;Cs apply."></textarea>
      <div class="flex mt-16 flex-wrap">
        <label class="field-label" for="variant-count" style="margin: 0;">Variants</label>
        <select id="variant-count" class="select">
          <option value="1">1</option><option value="2" selected>2</option>
          <option value="3">3</option><option value="4">4</option>
        </select>
        <div style="flex: 1;"></div>
        <button id="back-2" class="btn btn-ghost">&larr; Back to layout</button>
        <button id="btn-generate" class="btn btn-primary">Generate images</button>
      </div>
      ${ctx.clientId ? '' : '<p class="muted small mt-8">Select a client (top left) first — generation applies the client&#39;s brand kit.</p>'}
      <div id="gen-progress" class="hidden progress-panel">
        <div class="spinner spinner-lg"></div>
        <div>Generating branded variants…</div>
        <div class="small">This can take up to 2 minutes. Leave this tab open.</div>
      </div>
      <div id="gen-results" class="mt-16"></div>
    </div>`;

  host.querySelector('#back-2').addEventListener('click', () => { wiz.step = 2; renderStep(el, ctx); });
  host.querySelector('#btn-generate').addEventListener('click', () => generate(host, el, ctx));

  if (wiz.results.length) showResults(host.querySelector('#gen-results'));
}

async function generate(host, el, ctx) {
  const clientId = requireClient(ctx);
  if (!clientId) return;
  if (!wiz.job || !wiz.job.job_id) { toast('No ingest job — start from step 1.', 'warn'); return; }

  const offer = host.querySelector('#offer-text').value.trim();
  if (!offer) { toast('Describe your offer first — it replaces the competitor copy.', 'warn'); return; }
  const variants = Number(host.querySelector('#variant-count').value) || 1;

  const btn = host.querySelector('#btn-generate');
  const progress = host.querySelector('#gen-progress');
  btn.disabled = true;
  progress.classList.remove('hidden');

  try {
    const res = await postJSON(`/api/cloner/${wiz.job.job_id}/generate`, {
      client_id: clientId,
      offer_text: offer,
      copy_overrides: wiz.blocks.length ? wiz.blocks : undefined,
      variants,
    });
    wiz.results = res.assets || [];
    toast(`Generated ${wiz.results.length} asset${wiz.results.length === 1 ? '' : 's'}.`, 'success');
    showResults(host.querySelector('#gen-results'));
    loadAssets(el, ctx);
    loadJobs(el, ctx);
  } catch { /* toast already shown by fetchJSON */ } finally {
    btn.disabled = false;
    progress.classList.add('hidden');
  }
}

function showResults(hostEl) {
  if (!hostEl) return;
  if (!wiz.results.length) { hostEl.innerHTML = ''; return; }
  hostEl.innerHTML = `
    <div class="asset-grid">
      ${wiz.results.map((a) => {
        const url = mediaUrl(a.file);
        return `
          <div class="card asset-card hoverable">
            ${url ? `<img src="${esc(url)}" alt="Generated asset">` : ''}
            <div class="asset-foot">
              <span>#${esc(a.id)}</span>
              ${url ? `<a class="btn btn-sm" href="${esc(url)}" download>Download</a>` : ''}
            </div>
          </div>`;
      }).join('')}
    </div>`;
}

/* ================= Past jobs & assets ================= */

async function loadJobs(el, ctx) {
  const host = el.querySelector('#jobs-list');
  if (!host) return;
  let jobs = [];
  try {
    jobs = await getJSON('/api/cloner/jobs', { client_id: ctx.clientId || undefined }, { silent: true });
  } catch {
    host.innerHTML = '<p class="muted small">Could not load jobs.</p>';
    return;
  }
  if (!jobs || !jobs.length) {
    host.innerHTML = emptyState(
      'copy',
      'No clone jobs yet',
      'Ingest an ad above — drop an image, paste a URL, or pick a saved reference ad — and its layout becomes a reusable template.',
    );
    return;
  }
  host.innerHTML = `
    <div class="table-wrap">
      <table class="table">
        <thead><tr><th>Job</th><th>Status</th><th>Offer</th><th>Created</th><th></th></tr></thead>
        <tbody>
          ${jobs.map((j) => `
            <tr>
              <td class="num">#${esc(j.id)}</td>
              <td>${statusBadge(j.status)}</td>
              <td class="muted small">${esc(String(j.offer_text || '—').slice(0, 80))}</td>
              <td class="muted small">${esc(fmtDateTime(j.created_at))}</td>
              <td class="right"><button class="btn btn-sm" data-resume="${esc(j.id)}">Open</button></td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
  host.querySelectorAll('button[data-resume]').forEach((b) => {
    b.addEventListener('click', async () => {
      await resumeJob(b.dataset.resume, ctx);
      renderStep(el, ctx);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  });
}

async function loadAssets(el, ctx) {
  const host = el.querySelector('#assets-gallery');
  if (!host) return;
  let assets = [];
  try {
    assets = await getJSON('/api/assets', { client_id: ctx.clientId || undefined }, { silent: true });
  } catch {
    host.innerHTML = '<p class="muted small">Could not load assets.</p>';
    return;
  }
  if (!assets || !assets.length) {
    host.innerHTML = '<p class="muted small">Generated images will appear here — from the Cloner and from brief image generation.</p>';
    return;
  }
  host.innerHTML = `
    <div class="asset-grid">
      ${assets.map((a) => {
        const url = mediaUrl(a.file);
        return `
          <div class="card asset-card hoverable">
            ${url ? `<img src="${esc(url)}" alt="Asset ${esc(a.id)}" loading="lazy">` : ''}
            <div class="asset-foot">
              <span title="${esc(a.prompt || '')}">${esc(a.kind || 'asset')} · ${esc(fmtDateTime(a.created_at))}</span>
              ${url ? `<a class="btn btn-sm" href="${esc(url)}" download>Get</a>` : ''}
            </div>
          </div>`;
      }).join('')}
    </div>`;
}
