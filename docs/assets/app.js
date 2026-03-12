import { loadJSON, bySymbol, badge, fmt } from './utils.js';
import { lineSvg, rangeSvg } from './charts.js';
import { initRouter } from './router.js';

function setNav(page) {
  document.querySelectorAll('[data-nav]').forEach((a) => {
    if (a.dataset.nav === page) a.classList.add('active');
  });
}

async function home() {
  const [dashboard, drift, incidents] = await Promise.all([
    loadJSON('data/dashboard.json', {}),
    loadJSON('data/drift.json', {}),
    loadJSON('data/incidents.json', {}),
  ]);
  document.getElementById('overview').innerHTML = `
    <div class="grid">
      <div class="card"><h3>System</h3>${badge(dashboard.system_health || 'UNKNOWN')}<div class="small">Predictions: ${dashboard.prediction_files || 0}</div></div>
      <div class="card"><h3>Drift</h3>${badge(drift.drift_level || 'GREEN')}<div class="small">Decision: ${drift.decision || '-'}</div></div>
      <div class="card"><h3>Incidents</h3>${badge(incidents.status || 'OK')}<div class="small">Severity: ${(incidents.severity || '-')}</div></div>
    </div>`;
}

async function forecasts() {
  const data = await loadJSON('data/forecasts.json', { rows: [], top_opportunities: [] });
  const grouped = bySymbol(data.rows);
  const root = document.getElementById('forecastCards');
  root.innerHTML = Object.entries(grouped).map(([symbol, rows]) => {
    const d1 = rows.find((r) => r.horizon === 'd1') || rows[0];
    const current = (Number(d1.floor_value) + Number(d1.ceiling_value)) / 2;
    return `<div class="card"><h3>${symbol}</h3>
      <div class="small">D1 ${badge(d1.floor_time_bucket || '-')} / ${badge(d1.ceiling_time_bucket || '-')}</div>
      ${rangeSvg(Number(d1.floor_value), current, Number(d1.ceiling_value))}
      <a href="tickers.html?ticker=${symbol}">Detalle ticker</a></div>`;
  }).join('');

  document.getElementById('opps').innerHTML = data.top_opportunities.slice(0, 10).map((x) =>
    `<tr><td>${x.symbol}</td><td>${x.horizon}</td><td>${fmt(x.floor)}</td><td>${fmt(x.ceiling)}</td><td>${fmt(x.spread)}</td></tr>`).join('');
}

async function tickers() {
  const [universe, forecasts] = await Promise.all([
    loadJSON('data/universe.json', { symbols: [] }),
    loadJSON('data/forecasts.json', { rows: [] }),
  ]);
  const route = initRouter();
  const grouped = bySymbol(forecasts.rows);
  const table = document.getElementById('tickersTable');
  table.innerHTML = universe.symbols.map((s) => {
    const rows = grouped[s] || [];
    const d1 = rows.find((r) => r.horizon === 'd1') || {};
    return `<tr><td><a href="tickers.html?ticker=${s}">${s}</a></td><td>${fmt(d1.floor_value)}</td><td>${fmt(d1.ceiling_value)}</td><td>${d1.floor_time_bucket || '-'}</td><td>${d1.ceiling_time_bucket || '-'}</td></tr>`;
  }).join('');

  if (route.ticker) {
    const rows = grouped[route.ticker] || [];
    document.getElementById('tickerDetail').innerHTML = `<div class="card"><h3>${route.ticker}</h3>${rows.map((r) =>
      `<div>${r.horizon.toUpperCase()}: floor ${fmt(r.floor_value)} · ceiling ${fmt(r.ceiling_value)} · floor_t ${badge(String(r.floor_time_bucket || '-'))} · ceiling_t ${badge(String(r.ceiling_time_bucket || '-'))}</div>`
    ).join('')}</div>`;
  }

  document.querySelectorAll('th.sortable').forEach((th) => {
    th.addEventListener('click', () => {
      const idx = Number(th.dataset.col);
      const rows = [...table.closest('table').querySelectorAll('tbody tr')];
      rows.sort((a, b) => a.children[idx].innerText.localeCompare(b.children[idx].innerText, undefined, { numeric: true }));
      rows.forEach((r) => table.appendChild(r));
    });
  });
}

async function strategies() {
  const strategy = await loadJSON('data/strategy.json', { status: 'no_strategy_report', equity_curve: [] });
  document.getElementById('strategyStatus').innerHTML = badge(strategy.status || 'UNKNOWN');
  const curve = strategy.equity_curve || [];
  document.getElementById('equityCurve').innerHTML = lineSvg(curve.map((x) => ({ value: x.equity ?? x.value ?? 0 })));
  document.getElementById('drawdownCurve').innerHTML = lineSvg(curve.map((x) => ({ value: x.drawdown ?? 0 })));
}

async function models() {
  const models = await loadJSON('data/models.json', { champion: 'unknown', timeline: [], health: {} });
  document.getElementById('champion').textContent = models.champion;
  document.getElementById('calibration').textContent = JSON.stringify(models.health).slice(0, 220);
  document.getElementById('timeline').innerHTML = (models.timeline || []).map((x) =>
    `<tr><td>${x.as_of || '-'}</td><td>${x.model_name || '-'}</td><td>${x.action || '-'}</td><td>${x.drift_level || '-'}</td></tr>`).join('');
}

async function drift() {
  const d = await loadJSON('data/drift.json', { drift_level: 'GREEN', decision: '-', thresholds: [] });
  document.getElementById('driftLight').innerHTML = badge(d.drift_level || 'GREEN');
  document.getElementById('decision').textContent = d.decision || '-';
  document.getElementById('thresholds').innerHTML = (d.thresholds || []).map((t) =>
    `<tr><td>${t.name}</td><td>${t.observed}</td><td>${t.threshold}</td><td>${t.severity}</td></tr>`).join('');
}

async function incidents() {
  const i = await loadJSON('data/incidents.json', { status: 'OK', severity: 'SEV4', summary: {}, impact: {} });
  document.getElementById('status').innerHTML = `${badge(i.status || 'OK')} ${badge(i.severity || 'SEV4')}`;
  document.getElementById('symptom').textContent = i.summary?.symptom || '-';
  document.getElementById('impact').innerHTML = Object.entries(i.impact || {}).map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join('');
}

const page = document.body.dataset.page;
setNav(page);
({ home, forecasts, tickers, strategies, models, drift, incidents }[page] || (() => {}))();
