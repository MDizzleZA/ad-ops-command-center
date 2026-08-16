/* dashboard.js — aggregate (all clients) and single-client performance views.
   All dynamic values escaped via esc() before rendering. */

import {
  getJSON, rangeParams,
  fmtR, fmtN, fmtPct, esc, deltaChip,
  skeleton, emptyState, viewHead, makeChart, hexA,
  platformChip, PLATFORM_COLORS, PLATFORM_LABELS,
} from './api.js';

export default { render };

async function render(el, ctx) {
  if (!ctx.clientId) return renderAggregate(el, ctx);
  return renderClient(el, ctx);
}

/* ================= All clients — aggregate ================= */

async function renderAggregate(el, ctx) {
  el.innerHTML = viewHead('Dashboard', `All clients · ${ctx.dateFrom} to ${ctx.dateTo}`) + skeleton('cards', 6);
  const data = await getJSON('/api/dashboard/aggregate', { date_from: ctx.dateFrom, date_to: ctx.dateTo });
  const rows = data.clients || [];

  if (!rows.length) {
    el.innerHTML = viewHead('Dashboard', 'All clients') + emptyState(
      'chart',
      'No client data yet',
      'Add clients to the database, connect their ad accounts, then press Sync now (top right) to pull spend and lead data.',
    );
    return;
  }

  el.innerHTML = viewHead('Dashboard', `All clients · ${ctx.dateFrom} to ${ctx.dateTo}`) + `
    <div class="grid grid-cards" id="agg-grid">
      ${rows.map((c) => aggCard(c)).join('')}
    </div>`;

  el.querySelectorAll('[data-client]').forEach((card) => {
    card.addEventListener('click', () => ctx.selectClient(card.dataset.client));
  });
}

function aggCard(c) {
  const cur = c.current || {};
  const prev = c.previous || {};
  return `
    <div class="card kpi-card clickable hoverable" data-client="${esc(c.client_id)}">
      <div class="flex" style="justify-content: space-between;">
        <strong>${esc(c.name)}</strong>
        ${c.status ? `<span class="chip">${esc(c.status)}</span>` : ''}
      </div>
      <div class="metric-cols">
        <div>
          <span class="m-label">Spend</span>
          <span class="m-val">${fmtR(cur.spend)}</span>
          <div>${deltaChip(cur.spend, prev.spend)}</div>
        </div>
        <div>
          <span class="m-label">Leads</span>
          <span class="m-val">${fmtN(cur.leads)}</span>
          <div>${deltaChip(cur.leads, prev.leads)}</div>
        </div>
        <div>
          <span class="m-label">CPL</span>
          <span class="m-val">${fmtR(cur.cpl)}</span>
          <div>${deltaChip(cur.cpl, prev.cpl, { invert: true })}</div>
        </div>
      </div>
    </div>`;
}

/* ================= Single client ================= */

async function renderClient(el, ctx) {
  const name = (ctx.clients.find((c) => String(c.id) === String(ctx.clientId)) || {}).name || 'Client';
  el.innerHTML = viewHead(name, `${ctx.dateFrom} to ${ctx.dateTo} · vs previous period`) + skeleton('blocks', 3);

  const d = await getJSON('/api/dashboard', rangeParams(ctx));
  const cur = (d.total && d.total.current) || {};
  const prev = (d.total && d.total.previous) || {};
  const series = d.series || [];

  if (!series.length && !(cur.spend > 0)) {
    el.innerHTML = viewHead(name, `${ctx.dateFrom} to ${ctx.dateTo}`) + emptyState(
      'chart',
      'No performance data for this period',
      'Press Sync now (top right) to pull data from Meta, Google, Microsoft, LinkedIn and GA4 — or import a CSV under Settings. Then try a wider date range.',
    );
    return;
  }

  el.innerHTML = viewHead(name, `${ctx.dateFrom} to ${ctx.dateTo} · vs previous period`) + `
    <div class="section grid grid-kpi">
      ${kpiCard('Spend', fmtR(cur.spend), deltaChip(cur.spend, prev.spend))}
      ${kpiCard('Leads', fmtN(cur.leads), deltaChip(cur.leads, prev.leads))}
      ${kpiCard('CPL', fmtR(cur.cpl), deltaChip(cur.cpl, prev.cpl, { invert: true }), cplTargetNote(d.kpi, cur.cpl))}
      ${kpiCard('CTR', fmtPct(cur.ctr), deltaChip(cur.ctr, prev.ctr))}
      ${kpiCard('Conversions', fmtN(cur.conversions), deltaChip(cur.conversions, prev.conversions))}
    </div>

    <div class="section card chart-card">
      <div class="section-head"><h2>Daily spend by platform &amp; leads</h2></div>
      <div class="chart-wrap"><canvas id="dash-chart"></canvas></div>
    </div>

    <div class="section">
      <div class="section-head"><h2>Platform breakdown</h2></div>
      ${platformTable(d.platforms || {})}
    </div>

    <div class="section">
      <div class="section-head"><h2>Top campaigns</h2></div>
      ${campaignsTable(d.top_campaigns || [])}
    </div>`;

  if (series.length) {
    buildSeriesChart(el.querySelector('#dash-chart'), series);
  } else {
    el.querySelector('.chart-wrap').innerHTML =
      '<p class="muted" style="padding: 24px; text-align: center;">No daily series for this range.</p>';
  }
}

function kpiCard(label, value, delta, extra = '') {
  return `
    <div class="card kpi-card hoverable">
      <div class="kpi-label">${esc(label)}</div>
      <div class="kpi-value">${value}</div>
      <div class="kpi-foot">${delta}${extra}</div>
    </div>`;
}

function cplTargetNote(kpi, cpl) {
  const band = kpi && kpi.cpl_target && kpi.cpl_target.blended;
  if (!Array.isArray(band) || band.length < 2 || cpl === null || cpl === undefined) return '';
  const [lo, hi] = band;
  let cls = 'ok';
  let label = `On target (R ${fmtPlain(lo)}–${fmtPlain(hi)})`;
  if (cpl > hi) { cls = 'bad'; label = `Above target band R ${fmtPlain(lo)}–${fmtPlain(hi)}`; }
  else if (cpl < lo) { label = `Below target band R ${fmtPlain(lo)}–${fmtPlain(hi)}`; }
  return `<span class="kpi-target ${cls}">${esc(label)}</span>`;
}

function fmtPlain(n) {
  return String(Math.round(Number(n))).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

/* ---------------- Chart ---------------- */

function buildSeriesChart(canvas, series) {
  const dates = [...new Set(series.map((r) => r.date))].sort();
  const platforms = [...new Set(series.map((r) => r.platform))];
  const map = new Map(series.map((r) => [`${r.platform}|${r.date}`, r]));

  const datasets = platforms.map((p) => {
    const color = PLATFORM_COLORS[p] || '#8A93A6';
    return {
      label: PLATFORM_LABELS[p] || p,
      data: dates.map((dt) => {
        const row = map.get(`${p}|${dt}`);
        return row ? (row.spend || 0) : 0;
      }),
      borderColor: color,
      backgroundColor: hexA(color, 0.18),
      fill: true,
      tension: 0.3,
      borderWidth: 1.5,
      pointRadius: 0,
      pointHitRadius: 8,
      yAxisID: 'y',
    };
  });

  datasets.push({
    label: 'Leads',
    data: dates.map((dt) => platforms.reduce((s, p) => {
      const row = map.get(`${p}|${dt}`);
      return s + (row ? (row.leads || 0) : 0);
    }, 0)),
    borderColor: '#E6E9F0',
    borderDash: [5, 4],
    borderWidth: 1.5,
    pointRadius: 0,
    pointHitRadius: 8,
    tension: 0.3,
    fill: false,
    yAxisID: 'y1',
  });

  makeChart(canvas, {
    type: 'line',
    data: { labels: dates, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 12 } },
        y: {
          stacked: true,
          ticks: { callback: (v) => `R ${fmtPlain(v)}` },
          title: { display: true, text: 'Spend' },
        },
        y1: {
          position: 'right',
          grid: { drawOnChartArea: false },
          ticks: { precision: 0 },
          title: { display: true, text: 'Leads' },
          beginAtZero: true,
        },
      },
    },
  });
}

/* ---------------- Tables ---------------- */

function platformTable(platforms) {
  const keys = Object.keys(platforms);
  if (!keys.length) return '<p class="muted">No platform data for this range.</p>';
  return `
    <div class="table-wrap">
      <table class="table">
        <thead><tr>
          <th>Platform</th>
          <th class="num">Spend</th><th class="num">vs prev</th>
          <th class="num">Impressions</th><th class="num">Clicks</th><th class="num">CTR</th>
          <th class="num">Leads</th><th class="num">vs prev</th>
          <th class="num">CPL</th><th class="num">vs prev</th>
        </tr></thead>
        <tbody>
          ${keys.map((p) => {
            const cur = platforms[p].current || {};
            const prev = platforms[p].previous || {};
            return `<tr>
              <td>${platformChip(p)}</td>
              <td class="num">${fmtR(cur.spend)}</td>
              <td class="num">${deltaChip(cur.spend, prev.spend)}</td>
              <td class="num">${fmtN(cur.impressions)}</td>
              <td class="num">${fmtN(cur.clicks)}</td>
              <td class="num">${fmtPct(cur.ctr)}</td>
              <td class="num">${fmtN(cur.leads)}</td>
              <td class="num">${deltaChip(cur.leads, prev.leads)}</td>
              <td class="num">${fmtR(cur.cpl)}</td>
              <td class="num">${deltaChip(cur.cpl, prev.cpl, { invert: true })}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>`;
}

function campaignsTable(rows) {
  if (!rows.length) return '<p class="muted">No campaign activity in this range.</p>';
  return `
    <div class="table-wrap">
      <table class="table">
        <thead><tr>
          <th>Campaign</th><th>Platform</th>
          <th class="num">Spend</th><th class="num">Impressions</th>
          <th class="num">Clicks</th><th class="num">Leads</th><th class="num">CPL</th>
        </tr></thead>
        <tbody>
          ${rows.map((r) => `<tr>
            <td>${esc(r.name)}</td>
            <td>${platformChip(r.platform)}</td>
            <td class="num">${fmtR(r.spend)}</td>
            <td class="num">${fmtN(r.impressions)}</td>
            <td class="num">${fmtN(r.clicks)}</td>
            <td class="num">${fmtN(r.leads)}</td>
            <td class="num">${fmtR(r.cpl)}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}
