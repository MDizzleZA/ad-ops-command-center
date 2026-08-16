/* grader.js — algorithmic ad grading (A–F) with a recommend-and-confirm
   pause/scale action queue. Grades come from synced metrics vs client CPL/ROAS
   targets; Apply executes on the platform (Meta) only after human confirmation.
   Security note: every dynamic value is HTML-escaped via esc() before rendering,
   matching the app-wide escape-then-render convention. */

import {
  getJSON, postJSON,
  esc, toast,
  skeleton, emptyState, viewHead, statusBadge, platformChip,
  fmtR, fmtN,
} from './api.js';

export default { render };

const GRADE_COLORS = {
  A: '#4ADE80', B: '#A3E635', C: '#FBBF24', D: '#FB923C', F: '#F87171',
};

function gradePill(g) {
  const color = GRADE_COLORS[g] || '#8A93A6';
  return `<span class="badge" style="background:${color}22;color:${color};font-weight:700;min-width:26px;text-align:center;">${esc(g)}</span>`;
}

async function render(el, ctx) {
  el.innerHTML = viewHead(
    'Ad Grader',
    'Every ad graded A–F against the client’s CPL/ROAS targets, with pause and scale recommendations you confirm before anything executes',
    '<button id="btn-queue" class="btn btn-primary">Refresh recommendations</button>',
  ) + `
    <div id="grader-actions" class="section"></div>
    <div class="section">
      <div class="section-head"><h2>Grades (last 14 days)</h2></div>
      <div id="grader-table">${skeleton('rows', 5)}</div>
    </div>`;

  const clientId = ctx.clientId ? Number(ctx.clientId) : null;
  if (!clientId) {
    el.querySelector('#grader-table').innerHTML = emptyState(
      'alert', 'Pick a client', 'Select a client in the top bar to grade their ads.');
    el.querySelector('#grader-actions').innerHTML = '';
    el.querySelector('#btn-queue').disabled = true;
    return;
  }

  el.querySelector('#btn-queue').addEventListener('click', async () => {
    const btn = el.querySelector('#btn-queue');
    btn.disabled = true;
    btn.textContent = 'Grading…';
    try {
      await postJSON('/api/grader/queue', { client_id: clientId });
      toast('Recommendations refreshed.', 'success');
      await loadActions(el, ctx, clientId);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Refresh recommendations';
    }
  });

  await Promise.all([loadActions(el, ctx, clientId), loadGrades(el, clientId)]);
}

/* ================= Action queue ================= */

async function loadActions(el, ctx, clientId) {
  const host = el.querySelector('#grader-actions');
  if (!host) return;
  let actions = [];
  try {
    actions = await getJSON('/api/grader/actions', { client_id: clientId }, { silent: true });
  } catch { actions = []; }
  const pending = actions.filter((a) => a.status === 'pending');
  const resolved = actions.filter((a) => a.status !== 'pending').slice(0, 8);

  if (!pending.length && !resolved.length) {
    host.innerHTML = `
      <div class="section-head"><h2>Action queue</h2></div>
      <p class="muted small">No recommendations yet — click "Refresh recommendations" to grade and queue pause/scale actions.</p>`;
    return;
  }

  const row = (a, withActions) => `
    <tr>
      <td>${platformChip(a.platform)}</td>
      <td>${gradePill(a.grade)}</td>
      <td><span class="badge ${a.action === 'pause' ? 'error' : 'done'}">${esc(a.action.toUpperCase())}</span></td>
      <td><strong>${esc(a.entity_name || a.entity_external_id)}</strong>
        <div class="muted small">${esc(a.reason || '')}</div></td>
      <td class="num">${fmtR(a.metrics?.spend)}</td>
      <td class="num">${a.metrics?.cpl != null ? fmtR(a.metrics.cpl) : '—'}</td>
      <td>${withActions
        ? `<div class="flex" style="gap:6px;">
             <button class="btn btn-sm btn-primary" data-apply="${esc(a.id)}">Apply</button>
             <button class="btn btn-sm" data-dismiss="${esc(a.id)}">Dismiss</button>
           </div>`
        : `${statusBadge(a.status)}${a.error ? `<div class="muted small" title="${esc(a.error)}">${esc(a.error.slice(0, 60))}…</div>` : ''}`}
      </td>
    </tr>`;

  host.innerHTML = `
    <div class="section-head"><h2>Action queue</h2>
      <span class="muted small">${pending.length} pending — nothing executes without your confirmation</span></div>
    <div class="table-wrap card">
      <table class="table">
        <thead><tr><th>Platform</th><th>Grade</th><th>Action</th><th>Entity</th>
          <th class="num">Spend</th><th class="num">CPL</th><th></th></tr></thead>
        <tbody>
          ${pending.map((a) => row(a, true)).join('')}
          ${resolved.map((a) => row(a, false)).join('')}
        </tbody>
      </table>
    </div>`;

  host.querySelectorAll('button[data-apply]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const a = pending.find((x) => String(x.id) === btn.dataset.apply);
      if (!a) return;
      const verb = a.action === 'pause' ? 'PAUSE' : 'SCALE (+ budget)';
      if (!window.confirm(`${verb} "${a.entity_name || a.entity_external_id}" on ${a.platform}?\n\nThis executes on the live ad account.`)) return;
      btn.disabled = true;
      try {
        const res = await postJSON(`/api/grader/actions/${a.id}/apply`);
        if (res.status === 'applied') toast(`Applied: ${res.detail || 'done'}.`, 'success');
        else toast(res.error || 'Action failed — see the queue for details.', 'warn', 7000);
      } finally {
        loadActions(el, ctx, clientId);
      }
    });
  });
  host.querySelectorAll('button[data-dismiss]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      await postJSON(`/api/grader/actions/${btn.dataset.dismiss}/dismiss`);
      loadActions(el, ctx, clientId);
    });
  });
}

/* ================= Grades table ================= */

async function loadGrades(el, clientId) {
  const host = el.querySelector('#grader-table');
  let grades = [];
  try {
    grades = await getJSON('/api/grader', { client_id: clientId });
  } catch {
    host.innerHTML = emptyState('alert', 'Could not grade', 'Check that metrics have synced for this client.');
    return;
  }
  if (!grades.length) {
    host.innerHTML = emptyState('chart', 'No spend found',
      'No ads with spend in the lookback window — run a sync first (top bar).');
    return;
  }
  host.innerHTML = `
    <div class="table-wrap card">
      <table class="table">
        <thead><tr><th></th><th>Platform</th><th>Ad / campaign</th>
          <th class="num">Spend</th><th class="num">Leads</th><th class="num">CPL</th>
          <th class="num">CTR</th><th>Signal</th></tr></thead>
        <tbody>
          ${grades.map((g) => `
            <tr>
              <td>${gradePill(g.grade)}</td>
              <td>${platformChip(g.platform)}</td>
              <td><strong>${esc((g.entity_name || '').slice(0, 60))}</strong>
                <div class="muted small">${esc(g.reason)}</div></td>
              <td class="num">${fmtR(g.spend)}</td>
              <td class="num">${fmtN(g.leads)}</td>
              <td class="num">${g.cpl != null ? fmtR(g.cpl) : '—'}</td>
              <td class="num">${g.ctr != null ? `${g.ctr}%` : '—'}</td>
              <td>${g.recommendation
                ? `<span class="badge ${g.recommendation === 'pause' ? 'error' : 'done'}">${esc(g.recommendation)}</span>`
                : (g.fatigue ? `<span class="badge watch">${esc(g.fatigue)}</span>` : '<span class="muted small">keep</span>')}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}
