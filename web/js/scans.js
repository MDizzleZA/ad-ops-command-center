/* scans.js — competitor scan runner, history, and detail view with
   per-competitor summaries, launch timeline, insights and the ads grid.
   Security note: every dynamic value is HTML-escaped via esc() before rendering. */

import {
  getJSON, postJSON,
  esc, toast,
  skeleton, emptyState, viewHead, statusBadge,
  fmtN, fmtDateTime, makeChart, hexA,
  refAdCard, requireClient, clientName, pollUntil, navTo,
} from './api.js';

export default { render };

let kind = 'ads'; // 'ads' | 'organic' — persists for the session

async function render(el, ctx) {
  if (ctx.query.id) return renderDetail(el, ctx, ctx.query.id);
  return renderList(el, ctx);
}

/* ================= List / launcher ================= */

async function renderList(el, ctx) {
  el.innerHTML = viewHead(
    'Competitor Scans',
    `${clientName(ctx, ctx.clientId)} · sweep competitor ad libraries and organic pages`,
    `
      <div class="seg" id="kind-seg">
        <button data-kind="ads" class="${kind === 'ads' ? 'active' : ''}">Ads</button>
        <button data-kind="organic" class="${kind === 'organic' ? 'active' : ''}">Organic</button>
      </div>
      <button id="btn-scan" class="btn btn-primary">Scan now</button>
    `,
  ) + `<div id="scan-history">${skeleton('rows', 4)}</div>`;

  el.querySelectorAll('#kind-seg button').forEach((b) => {
    b.addEventListener('click', () => {
      kind = b.dataset.kind;
      el.querySelectorAll('#kind-seg button').forEach((x) => x.classList.toggle('active', x === b));
    });
  });

  el.querySelector('#btn-scan').addEventListener('click', () => startScan(el, ctx));

  await loadHistory(el, ctx);
}

async function loadHistory(el, ctx) {
  const host = el.querySelector('#scan-history');
  if (!host) return;
  let scans = [];
  try {
    scans = await getJSON('/api/scans', { client_id: ctx.clientId || undefined });
  } catch {
    host.innerHTML = '<p class="muted small">Could not load scan history.</p>';
    return;
  }

  if (!scans.length) {
    host.innerHTML = emptyState(
      'radar',
      'No scans yet',
      ctx.clientId
        ? 'Press Scan now to sweep this client’s competitors — ads pulls the Meta Ad Library, organic pulls recent page posts. Competitors are managed on the client record.'
        : 'Select a client (top left), then press Scan now to sweep their competitors’ ads and organic posts.',
    );
    return;
  }

  host.innerHTML = `
    <div class="table-wrap">
      <table class="table">
        <thead><tr>
          <th>Kind</th><th>Status</th><th class="num">Total ads</th><th class="num">New</th>
          <th>Started</th><th>Finished</th><th></th>
        </tr></thead>
        <tbody>
          ${scans.map((s) => `
            <tr class="row-click" data-scan="${esc(s.id)}">
              <td><span class="chip">${esc(s.kind)}</span></td>
              <td>${statusBadge(s.status)}${s.error ? ` <span class="muted small" title="${esc(s.error)}">error</span>` : ''}</td>
              <td class="num">${fmtN(s.total_ads)}</td>
              <td class="num">${s.new_ads ? `<span class="badge new-badge">${fmtN(s.new_ads)} new</span>` : '0'}</td>
              <td class="muted small">${esc(fmtDateTime(s.started_at))}</td>
              <td class="muted small">${esc(fmtDateTime(s.finished_at))}</td>
              <td class="right muted small">View &rarr;</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;

  host.querySelectorAll('tr[data-scan]').forEach((tr) => {
    tr.addEventListener('click', () => navTo(`#scans?id=${tr.dataset.scan}`));
  });
}

async function startScan(el, ctx) {
  const clientId = requireClient(ctx);
  if (!clientId) return;

  const btn = el.querySelector('#btn-scan');
  btn.disabled = true;
  let res;
  try {
    res = await postJSON('/api/scans/run', { client_id: clientId, kind });
  } catch {
    btn.disabled = false;
    return;
  }
  toast(`${kind === 'ads' ? 'Ads' : 'Organic'} scan started — this runs in the background.`);

  const scanId = res.scan_id;
  pollUntil(async () => {
    const scans = await getJSON('/api/scans', { client_id: clientId }, { silent: true });
    const s = (scans || []).find((x) => String(x.id) === String(scanId));
    await loadHistory(el, ctx);
    if (!s || s.status === 'running') return false;
    btn.disabled = false;
    if (s.status === 'error') {
      toast(`Scan failed: ${s.error || 'unknown error'}`, 'error');
    } else {
      toast(`Scan complete — ${s.total_ads ?? 0} ads, ${s.new_ads ?? 0} new.`, 'success');
      navTo(`#scans?id=${scanId}`);
    }
    return true;
  }, 5000);
}

/* ================= Detail ================= */

async function renderDetail(el, ctx, id) {
  el.innerHTML = viewHead('Scan detail', 'Loading…') + skeleton('blocks', 3);

  const data = await getJSON(`/api/scans/${id}`);
  const scan = data.scan || {};
  const summary = data.summary || {};
  const ads = data.ads || [];

  const head = viewHead(
    `Scan #${id} · ${scan.kind || 'ads'}`,
    `${fmtDateTime(scan.started_at)} · ${scan.status || ''}`,
    '<a class="btn" href="#scans">&larr; All scans</a>',
  );

  if (scan.kind === 'organic') {
    el.innerHTML = head + '<div id="organic-host">' + skeleton('rows', 4) + '</div>';
    await renderOrganic(el.querySelector('#organic-host'), id);
    return;
  }

  const competitors = summary.per_competitor || [];

  el.innerHTML = head + `
    ${scan.error ? `<div class="error-detail section">${esc(scan.error)}</div>` : ''}

    <div class="section grid grid-cards-sm">
      ${competitors.length
        ? competitors.map(competitorCard).join('')
        : '<p class="muted">No competitor summary for this scan.</p>'}
    </div>

    <div class="section card chart-card">
      <div class="section-head"><h2>Ad launch timeline</h2></div>
      <div class="chart-wrap" style="height: 220px;"><canvas id="timeline-chart"></canvas></div>
    </div>

    ${summary.insights ? `
      <div class="section insights-block">
        <div class="section-head"><h2>Insights</h2></div>
        ${String(summary.insights).split(/\n\n+/).map((p) => `<p>${esc(p)}</p>`).join('')}
      </div>` : ''}

    <div class="section" id="landing-pages"></div>

    <div class="section" id="ads-by-competitor"></div>`;

  buildTimeline(el.querySelector('#timeline-chart'), competitors);
  renderLandingPages(el.querySelector('#landing-pages'), scan.client_id || ctx.clientId);
  renderAdsGrid(el.querySelector('#ads-by-competitor'), ads, ctx);
}

/* ================= Landing page intel ================= */

async function renderLandingPages(host, clientId) {
  if (!host || !clientId) return;
  let pages = [];
  try {
    pages = await getJSON('/api/landing', { client_id: clientId }, { silent: true });
  } catch { pages = []; }
  pages = (pages || []).filter((p) => p.status === 'done');
  if (!pages.length) return; // section stays empty until scans capture landing URLs
  const cards = pages.slice(0, 6).map((p) => {
    const a = p.analysis || {};
    return `
      <div class="card">
        <div class="flex mb-8">
          <strong>${esc(p.competitor_name || 'Competitor')}</strong>
          <a class="muted small" href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">page &nearr;</a>
        </div>
        ${a.offer ? `<p class="small"><strong>Offer:</strong> ${esc(a.offer)}</p>` : ''}
        ${a.hook_headline ? `<p class="small muted">“${esc(a.hook_headline)}”</p>` : ''}
        ${(a.takeaways || []).length ? `
          <div class="mt-8"><strong class="small">Test ideas</strong>
            <ul class="small muted" style="margin:4px 0 0 16px;">
              ${a.takeaways.slice(0, 4).map((t) => `<li>${esc(t)}</li>`).join('')}
            </ul></div>` : ''}
      </div>`;
  }).join('');
  host.innerHTML = `
    <div class="section-head"><h2>Landing page intel</h2>
      <span class="muted small">extracted from the pages behind competitor ads</span></div>
    <div class="grid grid-cards">${cards}</div>`;
}

function competitorCard(c) {
  const formats = c.formats || {};
  const fmtBits = Object.entries(formats)
    .filter(([, n]) => n)
    .map(([k, n]) => `${fmtN(n)} ${k}`)
    .join(' · ');
  return `
    <div class="card competitor-card hoverable">
      <div style="flex: 1;">
        <div class="flex mb-8">
          <strong>${esc(c.name)}</strong>
          ${c.new ? `<span class="badge new-badge">${fmtN(c.new)} NEW</span>` : ''}
        </div>
        <div class="muted small">${fmtN(c.total)} active ad${c.total === 1 ? '' : 's'}</div>
        ${fmtBits ? `<div class="muted small">${esc(fmtBits)}</div>` : ''}
        ${c.error ? `<div class="small" style="color: var(--neg);" title="${esc(c.error)}">Scan error</div>` : ''}
      </div>
    </div>`;
}

function buildTimeline(canvas, competitors) {
  if (!canvas) return;
  const byMonth = new Map();
  for (const c of competitors) {
    for (const t of (c.timeline || [])) {
      byMonth.set(t.month, (byMonth.get(t.month) || 0) + (t.count || 0));
    }
  }
  const months = [...byMonth.keys()].sort();
  if (!months.length) {
    const wrap = canvas.parentElement;
    wrap.innerHTML = '<p class="muted small" style="padding: 24px; text-align: center;">No launch dates available for a timeline.</p>';
    return;
  }
  makeChart(canvas, {
    type: 'bar',
    data: {
      labels: months,
      datasets: [{
        label: 'Ads launched',
        data: months.map((m) => byMonth.get(m)),
        backgroundColor: hexA('#6C8CFF', 0.55),
        borderColor: '#6C8CFF',
        borderWidth: 1,
        borderRadius: 3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false } },
        y: { ticks: { precision: 0 }, beginAtZero: true },
      },
    },
  });
}

function renderAdsGrid(host, ads, ctx) {
  if (!host) return;
  if (!ads.length) {
    host.innerHTML = emptyState(
      'radar',
      'No ads captured in this scan',
      'The competitors may have no active ads in the Ad Library right now, or the scan hit an error. Try re-running the scan.',
    );
    return;
  }

  const groups = new Map();
  for (const ad of ads) {
    const key = ad.competitor_name || ad.page_name || 'Unknown competitor';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(ad);
  }

  host.innerHTML = [...groups.entries()].map(([name, list]) => `
    <div class="scan-group-title">
      <h2>${esc(name)}</h2>
      <span class="muted small">${list.length} ad${list.length === 1 ? '' : 's'}</span>
    </div>
    <div class="grid grid-cards-sm">
      ${list.map((ad) => refAdCard(ad, {
        ribbon: !!ad.is_new,
        actions: `
          <button class="btn btn-sm" data-save-target="brief" data-ad="${esc(ad.id)}">&rarr; Brief</button>
          <button class="btn btn-sm" data-save-target="cloner" data-ad="${esc(ad.id)}">&rarr; Clone</button>`,
      })).join('')}
    </div>`).join('');

  host.querySelectorAll('button[data-save-target]').forEach((btn) => {
    btn.addEventListener('click', () => spySave(btn, ctx));
  });
}

async function spySave(btn, ctx) {
  const clientId = requireClient(ctx);
  if (!clientId) return;
  const target = btn.dataset.saveTarget;
  btn.disabled = true;
  try {
    const res = await postJSON('/api/spy/save', {
      reference_ad_id: Number(btn.dataset.ad),
      target,
      client_id: clientId,
    });
    if (target === 'cloner' && res.clone_job_id) {
      navTo(`#cloner?job=${res.clone_job_id}`);
    } else if (target === 'brief') {
      toast('Sent to the Brief Console.', 'success');
      navTo(`#briefs?ref=${btn.dataset.ad}`);
    } else {
      toast('Saved.', 'success');
    }
  } finally {
    btn.disabled = false;
  }
}

/* ================= Organic detail ================= */

async function renderOrganic(host, id) {
  let rows = [];
  try {
    rows = await getJSON(`/api/scans/${id}/organic`);
  } catch {
    host.innerHTML = '<p class="muted small">Could not load organic results.</p>';
    return;
  }
  if (!rows.length) {
    host.innerHTML = emptyState(
      'radar',
      'No organic posts captured',
      'This scan found no recent posts on the competitors’ pages — or the pages could not be read. Re-run the scan later.',
    );
    return;
  }
  host.innerHTML = `
    <div class="table-wrap">
      <table class="table">
        <thead><tr>
          <th>Competitor</th><th>Posted</th><th>Post</th>
          <th class="num">Likes</th><th class="num">Comments</th><th class="num">Shares</th><th></th>
        </tr></thead>
        <tbody>
          ${rows.map((r) => `
            <tr>
              <td>${esc(r.competitor_name)}</td>
              <td class="muted small">${esc(fmtDateTime(r.posted_at))}</td>
              <td class="muted small" style="max-width: 380px;">${esc(String(r.text || '').slice(0, 140))}${String(r.text || '').length > 140 ? '…' : ''}</td>
              <td class="num">${fmtN(r.likes)}</td>
              <td class="num">${fmtN(r.comments)}</td>
              <td class="num">${fmtN(r.shares)}</td>
              <td class="right">${r.post_url ? `<a class="btn btn-sm" href="${esc(r.post_url)}" target="_blank" rel="noopener noreferrer">Open &nearr;</a>` : ''}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}
