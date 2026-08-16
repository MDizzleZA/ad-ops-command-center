/* organic.js — per-client organic/analytics view: GA4, Search Console, Business Profile.
   Rendering uses innerHTML by house convention (see dashboard.js): every dynamic
   value is escaped via esc() / numeric formatters before interpolation. */

import {
  getJSON, rangeParams,
  fmtR, fmtN, fmtPct, esc, deltaChip,
  skeleton, emptyState, viewHead, makeChart, hexA,
  PLATFORM_COLORS,
} from './api.js';

export default { render };

async function render(el, ctx) {
  if (!ctx.clientId) {
    el.innerHTML = viewHead('Organic', 'GA4 · Search Console · Business Profile') + emptyState(
      'chart',
      'Select a client',
      'Organic data is per-property — pick a client in the sidebar to see their GA4, Search Console and Business Profile performance.',
    );
    return;
  }

  const name = (ctx.clients.find((c) => String(c.id) === String(ctx.clientId)) || {}).name || 'Client';
  el.innerHTML = viewHead(`${name} — Organic`, `${ctx.dateFrom} to ${ctx.dateTo} · vs previous period`) + skeleton('blocks', 3);

  const d = await getJSON('/api/organic', rangeParams(ctx));
  const ga4 = d.ga4 || {};
  const gsc = d.gsc || {};
  const gbp = d.gbp || {};
  const hasGa4 = (ga4.series || []).length > 0;
  const hasGsc = (gsc.series || []).length > 0;
  const hasGbp = (gbp.series || []).length > 0;

  if (!hasGa4 && !hasGsc && !hasGbp) {
    el.innerHTML = viewHead(`${name} — Organic`, `${ctx.dateFrom} to ${ctx.dateTo}`) + emptyState(
      'chart',
      'No organic data for this period',
      'Press Sync now (top right) to pull GA4, Search Console and Business Profile data — '
      + 'or check Settings > Sync runs if a property still needs access granted.',
    );
    return;
  }

  const g4cur = (ga4.totals && ga4.totals.current) || {};
  const g4prev = (ga4.totals && ga4.totals.previous) || {};
  const gscur = (gsc.totals && gsc.totals.current) || {};
  const gsprev = (gsc.totals && gsc.totals.previous) || {};
  const gbcur = (gbp.totals && gbp.totals.current) || {};
  const gbprev = (gbp.totals && gbp.totals.previous) || {};

  el.innerHTML = viewHead(`${name} — Organic`, `${ctx.dateFrom} to ${ctx.dateTo} · vs previous period`) + `
    <div class="section grid grid-kpi">
      ${kpiCard('Sessions', fmtN(g4cur.sessions), deltaChip(g4cur.sessions, g4prev.sessions))}
      ${kpiCard('GSC Clicks', fmtN(gscur.clicks), deltaChip(gscur.clicks, gsprev.clicks))}
      ${kpiCard('Avg Position', gscur.position != null ? esc(gscur.position) : '—',
                deltaChip(gscur.position, gsprev.position, { invert: true }))}
      ${kpiCard('GBP Calls', fmtN(gbcur.calls), deltaChip(gbcur.calls, gbprev.calls))}
      ${kpiCard('Directions', fmtN(gbcur.direction_requests),
                deltaChip(gbcur.direction_requests, gbprev.direction_requests))}
    </div>

    ${hasGa4 ? `
    <div class="section card chart-card">
      <div class="section-head"><h2>GA4 — sessions &amp; users${(g4cur.revenue || 0) > 0 ? ' &amp; revenue' : ''}</h2></div>
      <div class="chart-wrap"><canvas id="organic-ga4-chart"></canvas></div>
    </div>
    <div class="section grid grid-2col">
      <div class="card">
        <div class="section-head"><h2>Channels</h2></div>
        <div class="chart-wrap chart-sm"><canvas id="organic-channels-chart"></canvas></div>
      </div>
      <div class="card">
        <div class="section-head"><h2>Devices</h2></div>
        <div class="chart-wrap chart-sm"><canvas id="organic-devices-chart"></canvas></div>
      </div>
    </div>
    <div class="section">
      <div class="section-head"><h2>Top landing pages</h2></div>
      ${landingTable(ga4.top_landing_pages || [])}
    </div>` : sectionEmpty('GA4', 'No GA4 rows for this range — check the property grant and sync.')}

    ${hasGsc ? `
    <div class="section card chart-card">
      <div class="section-head"><h2>Search Console — clicks, impressions &amp; position</h2></div>
      <p class="muted" style="margin: 0 0 8px;">Search data finalises ~2–3 days late; the most recent days under-report.</p>
      <div class="chart-wrap"><canvas id="organic-gsc-chart"></canvas></div>
    </div>
    <div class="section grid grid-2col">
      <div class="card">
        <div class="section-head"><h2>Top queries</h2></div>
        ${gscTable(gsc.top_queries || [], 'q', 'Query')}
      </div>
      <div class="card">
        <div class="section-head"><h2>Top pages</h2></div>
        ${gscTable(gsc.top_pages || [], 'page', 'Page')}
      </div>
    </div>` : sectionEmpty('Search Console', 'No Search Console rows — add the service account as a user on the property, then sync.')}

    ${hasGbp ? `
    <div class="section card chart-card">
      <div class="section-head"><h2>Business Profile — calls, directions &amp; website clicks</h2></div>
      <div class="chart-wrap"><canvas id="organic-gbp-chart"></canvas></div>
    </div>` : sectionEmpty('Business Profile', 'No Business Profile data — GBP API access may still be pending Google approval. See Settings > Sync runs for status.')}`;

  if (hasGa4) {
    buildGa4Chart(el.querySelector('#organic-ga4-chart'), ga4.series);
    buildDoughnut(el.querySelector('#organic-channels-chart'), ga4.channels || {});
    buildDoughnut(el.querySelector('#organic-devices-chart'), ga4.devices || {});
  }
  if (hasGsc) buildGscChart(el.querySelector('#organic-gsc-chart'), gsc.series);
  if (hasGbp) buildGbpChart(el.querySelector('#organic-gbp-chart'), gbp.series);
}

function kpiCard(label, value, delta, extra = '') {
  return `
    <div class="card kpi-card hoverable">
      <div class="kpi-label">${esc(label)}</div>
      <div class="kpi-value">${value}</div>
      <div class="kpi-foot">${delta}${extra}</div>
    </div>`;
}

function sectionEmpty(title, msg) {
  return `
    <div class="section card">
      <div class="section-head"><h2>${esc(title)}</h2></div>
      <p class="muted" style="padding: 12px 0;">${esc(msg)}</p>
    </div>`;
}

/* ---------------- Charts ---------------- */

const PALETTE = ['#6C8CFF', '#4ADE80', '#FBBF24', '#F472B6', '#A78BFA', '#22D3EE', '#34D399', '#FB923C', '#8A93A6'];

function baseLine(label, data, color, opts = {}) {
  return {
    label, data,
    borderColor: color, backgroundColor: hexA(color, 0.18),
    fill: false, tension: 0.3, borderWidth: 1.5, pointRadius: 0, pointHitRadius: 8,
    ...opts,
  };
}

function buildGa4Chart(canvas, series) {
  const dates = series.map((r) => r.date);
  const ga4Color = PLATFORM_COLORS.ga4 || '#FBBF24';
  const datasets = [
    baseLine('Sessions', series.map((r) => r.sessions), ga4Color, { fill: true, yAxisID: 'y' }),
    baseLine('Users', series.map((r) => r.users), '#6C8CFF', { yAxisID: 'y' }),
  ];
  const scales = {
    x: { grid: { display: false }, ticks: { maxTicksLimit: 12 } },
    y: { beginAtZero: true, ticks: { precision: 0 } },
  };
  if (series.some((r) => (r.revenue || 0) > 0)) {
    datasets.push(baseLine('Revenue', series.map((r) => r.revenue), '#4ADE80',
      { borderDash: [5, 4], yAxisID: 'y1' }));
    scales.y1 = {
      position: 'right', grid: { drawOnChartArea: false }, beginAtZero: true,
      title: { display: true, text: 'Revenue (R)' },
    };
  }
  makeChart(canvas, {
    type: 'line',
    data: { labels: dates, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true } } },
      scales,
    },
  });
}

function buildDoughnut(canvas, breakdown) {
  const entries = Object.entries(breakdown).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return;
  makeChart(canvas, {
    type: 'doughnut',
    data: {
      labels: entries.map(([k]) => k),
      datasets: [{
        data: entries.map(([, v]) => v),
        backgroundColor: entries.map((_, i) => hexA(PALETTE[i % PALETTE.length], 0.75)),
        borderWidth: 0,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'right', labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true } } },
    },
  });
}

function buildGscChart(canvas, series) {
  const dates = series.map((r) => r.date);
  const gscColor = PLATFORM_COLORS.gsc || '#F472B6';
  makeChart(canvas, {
    type: 'line',
    data: {
      labels: dates,
      datasets: [
        baseLine('Clicks', series.map((r) => r.clicks), gscColor, { fill: true, yAxisID: 'y' }),
        baseLine('Impressions', series.map((r) => r.impressions), '#8A93A6', { yAxisID: 'y1' }),
        baseLine('Position', series.map((r) => r.position), '#FBBF24',
          { borderDash: [5, 4], yAxisID: 'y2' }),
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true } } },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 12 } },
        y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: 'Clicks' } },
        y1: {
          position: 'right', grid: { drawOnChartArea: false }, beginAtZero: true,
          ticks: { precision: 0 }, title: { display: true, text: 'Impressions' },
        },
        y2: { display: false, reverse: true },
      },
    },
  });
}

function buildGbpChart(canvas, series) {
  const dates = series.map((r) => r.date);
  const gbpColor = PLATFORM_COLORS.gbp || '#34D399';
  makeChart(canvas, {
    type: 'line',
    data: {
      labels: dates,
      datasets: [
        baseLine('Calls', series.map((r) => r.calls), gbpColor, { fill: true }),
        baseLine('Directions', series.map((r) => r.direction_requests), '#22D3EE'),
        baseLine('Website clicks', series.map((r) => r.website_clicks), '#A78BFA'),
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true } } },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 12 } },
        y: { beginAtZero: true, ticks: { precision: 0 } },
      },
    },
  });
}

/* ---------------- Tables ---------------- */

function landingTable(rows) {
  if (!rows.length) return '<p class="muted">No landing-page data for this range.</p>';
  return `
    <div class="table-wrap">
      <table class="table">
        <thead><tr><th>Landing page</th><th class="num">Sessions</th></tr></thead>
        <tbody>
          ${rows.map((r) => `<tr>
            <td class="mono">${esc(r.path)}</td>
            <td class="num">${fmtN(r.sessions)}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

function gscTable(rows, key, label) {
  if (!rows.length) return '<p class="muted">No data for this range.</p>';
  return `
    <div class="table-wrap">
      <table class="table">
        <thead><tr>
          <th>${esc(label)}</th>
          <th class="num">Clicks</th><th class="num">Impressions</th>
          <th class="num">CTR</th><th class="num">Position</th>
        </tr></thead>
        <tbody>
          ${rows.map((r) => `<tr>
            <td>${esc(r[key])}</td>
            <td class="num">${fmtN(r.clicks)}</td>
            <td class="num">${fmtN(r.impressions)}</td>
            <td class="num">${fmtPct(r.ctr)}</td>
            <td class="num">${r.position != null ? esc(r.position) : '—'}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}
