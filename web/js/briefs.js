/* briefs.js — 3-pane Brief Console: reference picker, iteration axes,
   generated variants, saved briefs with compliance badges + detail modal.
   Security note: every dynamic value is HTML-escaped via esc() before rendering. */

import {
  getJSON, postJSON, putJSON,
  esc, toast, modal,
  skeleton, emptyState, viewHead, statusBadge,
  fmtDateTime, mediaUrl, requireClient, clientName,
} from './api.js';

export default { render };

const AXES = [
  { id: 'hook', name: 'Hooks', desc: 'Same offer, five different opening angles.' },
  { id: 'persona', name: 'Personas', desc: 'Rewrite the ad for each saved client persona.' },
  { id: 'pain_point', name: 'Pain points', desc: 'Lead with a different customer pain each time.' },
  { id: 'visual_format', name: 'Visual formats', desc: 'Adapt the concept to new visual treatments.' },
  { id: 'asset_type', name: 'Asset types', desc: 'Static, carousel, video script and more.' },
];

// Console selection state — survives re-renders within the session.
const sel = { tab: 'refs', refId: null, axis: 'hook' };
let lastVariants = [];

async function render(el, ctx) {
  if (ctx.query.ref) {
    sel.refId = Number(ctx.query.ref);
    sel.tab = 'refs';
  }

  el.innerHTML = viewHead(
    'Brief Console',
    `${clientName(ctx, ctx.clientId)} · pick a reference, choose an axis, generate variants`,
  ) + `
    <div class="panes section">
      <div class="pane">
        <div class="pane-title">1 · Reference</div>
        <div class="tabs">
          <button data-tab="refs" class="${sel.tab === 'refs' ? 'active' : ''}">Reference ads</button>
          <button data-tab="text" class="${sel.tab === 'text' ? 'active' : ''}">Paste text</button>
        </div>
        <div id="tab-refs" class="${sel.tab === 'refs' ? '' : 'hidden'}">
          <div class="ref-list" id="ref-list">${skeleton('rows', 4)}</div>
        </div>
        <div id="tab-text" class="${sel.tab === 'text' ? '' : 'hidden'}">
          <textarea id="src-text" class="input" placeholder="Paste ad copy, a landing-page section, or a rough concept to iterate from…"></textarea>
        </div>
      </div>

      <div class="pane">
        <div class="pane-title">2 · Iteration axis</div>
        <div id="axis-list">
          ${AXES.map((a) => `
            <div class="axis-card ${sel.axis === a.id ? 'selected' : ''}" data-axis="${a.id}">
              <div class="axis-name">${esc(a.name)}</div>
              <div class="axis-desc">${esc(a.desc)}</div>
            </div>`).join('')}
        </div>
        <button id="btn-generate" class="btn btn-primary w-100 mt-8">Generate variants</button>
        ${ctx.clientId ? '' : '<p class="muted small mt-8">Select a client (top left) to generate — briefs are client-specific.</p>'}
      </div>

      <div class="pane">
        <div class="pane-title">3 · Variants</div>
        <div id="variant-results">
          <p class="muted small">Pick a reference and an axis, then press Generate. Each variant can be saved as a brief and is checked against the client&#39;s compliance rules.</p>
        </div>
      </div>
    </div>

    <div class="section">
      <div class="section-head"><h2>Saved briefs</h2></div>
      <div id="saved-briefs">${skeleton('rows', 3)}</div>
    </div>`;

  // Tabs
  el.querySelectorAll('.tabs button').forEach((b) => {
    b.addEventListener('click', () => {
      sel.tab = b.dataset.tab;
      el.querySelectorAll('.tabs button').forEach((x) => x.classList.toggle('active', x === b));
      el.querySelector('#tab-refs').classList.toggle('hidden', sel.tab !== 'refs');
      el.querySelector('#tab-text').classList.toggle('hidden', sel.tab !== 'text');
    });
  });

  // Axis cards
  el.querySelectorAll('.axis-card').forEach((c) => {
    c.addEventListener('click', () => {
      sel.axis = c.dataset.axis;
      el.querySelectorAll('.axis-card').forEach((x) => x.classList.toggle('selected', x === c));
    });
  });

  el.querySelector('#btn-generate').addEventListener('click', () => generate(el, ctx));

  await Promise.all([loadRefs(el, ctx), loadSaved(el, ctx)]);
}

/* ---------------- Reference ads list ---------------- */

async function loadRefs(el, ctx) {
  const list = el.querySelector('#ref-list');
  let ads = [];
  try {
    ads = await getJSON('/api/reference-ads', { client_id: ctx.clientId || undefined });
  } catch {
    list.innerHTML = '<p class="muted small">Could not load reference ads.</p>';
    return;
  }
  if (!ads.length) {
    list.innerHTML = `
      <p class="muted small">No reference ads yet. Save winners from <a href="#creatives">Creatives</a>,
      grab competitor ads via <a href="#spy">Ad Spy</a>, or use the Paste text tab.</p>`;
    return;
  }
  list.innerHTML = ads.map((a) => `
    <div class="ref-item ${String(sel.refId) === String(a.id) ? 'selected' : ''}" data-ref="${esc(a.id)}">
      <div class="ref-page">${esc(a.page_name || 'Untitled')}</div>
      ${a.headline ? `<div class="ref-headline">${esc(a.headline)}</div>` : ''}
      <div class="muted small">${esc(a.source || '')}${a.saved_at ? ` · ${esc(String(a.saved_at).slice(0, 10))}` : ''}</div>
    </div>`).join('');
  list.querySelectorAll('.ref-item').forEach((item) => {
    item.addEventListener('click', () => {
      sel.refId = Number(item.dataset.ref);
      list.querySelectorAll('.ref-item').forEach((x) => x.classList.toggle('selected', x === item));
    });
  });
}

/* ---------------- Generate variants ---------------- */

async function generate(el, ctx) {
  const clientId = requireClient(ctx);
  if (!clientId) return;

  const payload = { client_id: clientId, axis: sel.axis };
  if (sel.tab === 'refs') {
    if (!sel.refId) { toast('Pick a reference ad first (or switch to Paste text).', 'warn'); return; }
    payload.reference_ad_id = sel.refId;
  } else {
    const text = el.querySelector('#src-text').value.trim();
    if (!text) { toast('Paste some source text to iterate from.', 'warn'); return; }
    payload.source_text = text;
  }

  const results = el.querySelector('#variant-results');
  const btn = el.querySelector('#btn-generate');
  btn.disabled = true;
  results.innerHTML = `
    <div class="progress-panel">
      <div class="spinner spinner-lg"></div>
      <div>Generating variants along the <strong>${esc(axisName(sel.axis))}</strong> axis…</div>
      <div class="small">This usually takes 15–40 seconds.</div>
    </div>`;

  let res;
  try {
    res = await postJSON('/api/briefs/iterate', payload);
  } catch (err) {
    results.innerHTML = `<p class="muted small">Generation failed: ${esc(err.message)}. Try again.</p>`;
    btn.disabled = false;
    return;
  }
  btn.disabled = false;

  lastVariants = res.variants || [];
  if (!lastVariants.length) {
    results.innerHTML = '<p class="muted small">No variants came back — try a different axis or reference.</p>';
    return;
  }
  results.innerHTML = lastVariants.map((v, i) => variantCard(v, i)).join('');
  results.querySelectorAll('button[data-save]').forEach((b) => {
    b.addEventListener('click', () => saveVariant(el, ctx, Number(b.dataset.save), b));
  });
}

function axisName(id) {
  return (AXES.find((a) => a.id === id) || {}).name || id;
}

function vf(label, value) {
  if (value === null || value === undefined || value === '') return '';
  return `
    <div class="variant-field">
      <div class="vf-label">${esc(label)}</div>
      <div class="vf-value">${esc(value)}</div>
    </div>`;
}

function variantCard(v, i) {
  return `
    <div class="card variant-card">
      <div class="flex mb-8"><span class="chip">${esc(v.axis_value || `Variant ${i + 1}`)}</span></div>
      ${vf('Hook', v.hook)}
      ${vf('Headline', v.headline)}
      ${vf('Primary text', v.primary_text)}
      ${vf('CTA', v.cta)}
      ${vf('Visual direction', v.visual_direction)}
      ${vf('Format', v.format_spec)}
      ${vf('Compliance notes', v.compliance_notes)}
      <div class="card-actions"><button class="btn btn-primary btn-sm" data-save="${i}">Save brief</button></div>
    </div>`;
}

async function saveVariant(el, ctx, i, btn) {
  const clientId = requireClient(ctx);
  if (!clientId) return;
  const v = lastVariants[i];
  if (!v) return;

  btn.disabled = true;
  btn.textContent = 'Saving…';
  const title = `${axisName(sel.axis)}: ${v.axis_value || `Variant ${i + 1}`}`;
  try {
    const res = await postJSON('/api/briefs', {
      client_id: clientId,
      reference_ad_id: sel.tab === 'refs' ? sel.refId : undefined,
      axis: sel.axis,
      axis_value: v.axis_value,
      title,
      brief: {
        hook: v.hook,
        primary_text: v.primary_text,
        headline: v.headline,
        cta: v.cta,
        visual_direction: v.visual_direction,
        format_spec: v.format_spec,
        compliance_notes: v.compliance_notes,
      },
    });
    const status = res.compliance && res.compliance.status;
    if (status === 'block') toast('Brief saved, but compliance BLOCKED it — open it to see the violations.', 'error');
    else if (status === 'warn') toast('Brief saved with compliance warnings — review before use.', 'warn');
    else toast('Brief saved — compliance pass.', 'success');
    btn.textContent = 'Saved ✓';
    loadSaved(el, ctx);
  } catch {
    btn.disabled = false;
    btn.textContent = 'Save brief';
  }
}

/* ---------------- Saved briefs ---------------- */

async function loadSaved(el, ctx) {
  const host = el.querySelector('#saved-briefs');
  if (!host) return;
  let rows = [];
  try {
    rows = await getJSON('/api/briefs', { client_id: ctx.clientId || undefined });
  } catch {
    host.innerHTML = '<p class="muted small">Could not load saved briefs.</p>';
    return;
  }
  if (!rows.length) {
    host.innerHTML = emptyState(
      'file',
      'No saved briefs yet',
      "Generate variants above and press Save brief — each saved brief is automatically screened against the client's FAIS/compliance rules.",
    );
    return;
  }
  host.innerHTML = `
    <div class="table-wrap">
      <table class="table">
        <thead><tr>
          <th>Title</th><th>Axis</th><th>Compliance</th><th>Status</th><th>Created</th>
        </tr></thead>
        <tbody>
          ${rows.map((b) => `
            <tr class="row-click" data-brief="${esc(b.id)}">
              <td>${esc(b.title)}</td>
              <td><span class="chip">${esc(axisName(b.axis))}</span> <span class="muted small">${esc(b.axis_value || '')}</span></td>
              <td>${b.compliance_status ? statusBadge(String(b.compliance_status).toUpperCase()) : '<span class="muted small">not checked</span>'}</td>
              <td class="muted small">${esc(b.status || '—')}</td>
              <td class="muted small">${esc(fmtDateTime(b.created_at))}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
  host.querySelectorAll('tr[data-brief]').forEach((tr) => {
    tr.addEventListener('click', () => openBrief(Number(tr.dataset.brief), () => loadSaved(el, ctx)));
  });
}

/* ---------------- Brief detail modal ---------------- */

async function openBrief(id, onChange) {
  let b;
  try {
    b = await getJSON(`/api/briefs/${id}`);
  } catch { return; }

  const brief = b.brief || {};
  const comp = b.compliance;
  const blocked = !comp || comp.status === 'block';
  const approveNote = !comp
    ? 'Run a compliance check before approving.'
    : (comp.status === 'block' ? 'Blocked by compliance — fix the violations first.' : '');
  const m = modal(b.title || 'Brief', `
    <div class="flex mb-16">
      <span class="muted small">Status:</span> ${statusBadge(String(b.status || 'draft').toUpperCase())}
      <span style="flex:1"></span>
      ${b.status === 'approved'
    ? '<button id="brief-unapprove" class="btn btn-sm">Back to draft</button>'
    : `<button id="brief-approve" class="btn btn-primary btn-sm" ${blocked ? 'disabled' : ''}>Approve</button>
       <span class="muted small">${esc(approveNote)}</span>`}
    </div>
    <div class="mb-16">
      ${vf('Hook', brief.hook)}
      ${vf('Headline', brief.headline)}
      ${vf('Primary text', brief.primary_text)}
      ${vf('CTA', brief.cta)}
      ${vf('Visual direction', brief.visual_direction)}
      ${vf('Format', brief.format_spec)}
      ${vf('Compliance notes', brief.compliance_notes)}
    </div>
    ${complianceSection(comp)}
    ${b.reference_ad ? `
      <div class="mb-16 variant-field">
        <div class="vf-label">Reference ad</div>
        <p class="muted small">${esc(b.reference_ad.page_name || '')} — ${esc(b.reference_ad.headline || '')}</p>
      </div>` : ''}
    <div class="flex">
      <button id="gen-img" class="btn btn-primary">Generate image</button>
      <span id="gen-img-note" class="muted small"></span>
    </div>
    <div id="gen-img-result" class="mt-16"></div>
  `, { wide: true });

  const setStatus = async (status) => {
    try {
      await putJSON(`/api/briefs/${id}`, { status });
      toast(status === 'approved' ? 'Brief approved.' : 'Brief moved back to draft.', 'success');
      m.close();
      if (onChange) onChange();
    } catch (err) {
      toast(`Could not update status: ${err.message}`, 'error');
    }
  };
  const approveBtn = m.body.querySelector('#brief-approve');
  if (approveBtn) approveBtn.addEventListener('click', () => setStatus('approved'));
  const unapproveBtn = m.body.querySelector('#brief-unapprove');
  if (unapproveBtn) unapproveBtn.addEventListener('click', () => setStatus('draft'));

  const btn = m.body.querySelector('#gen-img');
  const note = m.body.querySelector('#gen-img-note');
  const result = m.body.querySelector('#gen-img-result');
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    note.innerHTML = '<span class="spinner"></span> Generating — this can take a minute…';
    try {
      const res = await postJSON(`/api/briefs/${id}/generate-image`);
      note.textContent = '';
      const url = mediaUrl(res.file);
      result.innerHTML = url
        ? `<img class="source-img" src="${esc(url)}" alt="Generated ad image">
           <div class="mt-8"><a class="btn btn-sm" href="${esc(url)}" download>Download</a></div>`
        : '<p class="muted small">Image generated but no file was returned.</p>';
      toast('Image generated.', 'success');
    } catch (err) {
      note.textContent = `Failed: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  });
}

function complianceSection(comp) {
  if (!comp) return '<p class="muted small mb-16">No compliance check recorded for this brief.</p>';
  const violations = comp.violations || [];
  return `
    <div class="mb-16">
      <div class="flex mb-8">
        <span class="pane-title" style="margin: 0;">Compliance</span>
        ${statusBadge(String(comp.status || '—').toUpperCase())}
      </div>
      ${violations.length ? `
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>Rule</th><th>Severity</th><th>Phrase</th><th>Suggested fix</th></tr></thead>
            <tbody>
              ${violations.map((vio) => `<tr>
                <td>${esc(vio.rule)}</td>
                <td>${statusBadge(vio.severity)}</td>
                <td class="muted">${esc(vio.phrase || '—')}</td>
                <td>${esc(vio.fix || '—')}</td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>` : '<p class="muted small">No violations found.</p>'}
    </div>`;
}
