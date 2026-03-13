import { loadJSON, bySymbol, badge, fmt } from './utils.js';
import { lineSvg, rangeSvg } from './charts.js';
import { initRouter } from './router.js';

function setNav(page) {
  document.querySelectorAll('[data-nav]').forEach((a) => {
    if (a.dataset.nav === page) a.classList.add('active');
  });
}

function emptyState(message, colspan = 1) {
  return `<tr><td colspan="${colspan}" class="small">${message}</td></tr>`;
}

function primaryForecast(rows = []) {
  return rows.find((r) => r.horizon === 'd1') || rows.find((r) => r.horizon === 'q1') || rows[0] || null;
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
  const cards = Object.entries(grouped).map(([symbol, rows]) => {
    const forecast = primaryForecast(rows);
    const current = (Number(forecast.floor_value) + Number(forecast.ceiling_value)) / 2;
    return `<div class="card"><h3>${symbol}</h3>
      <div class="small">${String(forecast.horizon || '-').toUpperCase()} ${badge(forecast.floor_time_bucket || '-')} / ${badge(forecast.ceiling_time_bucket || '-')}</div>
      ${rangeSvg(Number(forecast.floor_value), current, Number(forecast.ceiling_value))}
      <a href="tickers.html?ticker=${symbol}">Detalle ticker</a></div>`;
  });
  root.innerHTML = cards.length
    ? cards.join('')
    : '<div class="card"><h3>Sin pronósticos</h3><div class="small">No se encontraron filas en data/forecasts.json.</div></div>';

  const opportunities = data.top_opportunities.slice(0, 10).map((x) =>
    `<tr><td>${x.symbol}</td><td>${x.horizon}</td><td>${fmt(x.floor)}</td><td>${fmt(x.ceiling)}</td><td>${fmt(x.spread)}</td></tr>`).join('');
  document.getElementById('opps').innerHTML = opportunities || emptyState('No hay oportunidades para mostrar.', 5);
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
    const forecast = primaryForecast(rows) || {};
    return `<tr><td><a href="tickers.html?ticker=${s}">${s}</a></td><td>${fmt(forecast.floor_value)}</td><td>${fmt(forecast.ceiling_value)}</td><td>${forecast.floor_time_bucket || '-'}</td><td>${forecast.ceiling_time_bucket || '-'}</td></tr>`;
  }).join('');

  const covered = universe.symbols.filter((s) => (grouped[s] || []).length > 0);
  const missing = universe.symbols.filter((s) => (grouped[s] || []).length === 0);
  const coverage = document.getElementById('tickerCoverage');
  if (coverage) {
    const missingPreview = missing.slice(0, 12).join(', ');
    const suffix = missing.length > 12 ? '…' : '';
    coverage.innerHTML = `Cobertura de pronósticos: <strong>${covered.length}/${universe.symbols.length}</strong> tickers con datos (${missing.length} faltantes).${missing.length ? ` Faltan: ${missingPreview}${suffix}` : ''}`;
  }

  if (route.ticker) {
    const rows = grouped[route.ticker] || [];
    document.getElementById('tickerDetail').innerHTML = `<div class="card"><h3>${route.ticker}</h3>${rows.length ? rows.map((r) =>
      `<div>${r.horizon.toUpperCase()}: floor ${fmt(r.floor_value)} · ceiling ${fmt(r.ceiling_value)} · floor_t ${badge(String(r.floor_time_bucket || '-'))} · ceiling_t ${badge(String(r.ceiling_time_bucket || '-'))}</div>`
    ).join('') : '<div class="small">Ticker sin pronóstico disponible en el dataset actual.</div>'}</div>`;
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
  document.getElementById('calibration').innerHTML = `<pre>${JSON.stringify(models.health || {}, null, 2)}</pre>`;
  const timeline = (models.timeline || []).map((x) =>
    `<tr><td>${x.as_of || '-'}</td><td>${x.model_name || '-'}</td><td>${x.action || '-'}</td><td>${x.drift_level || '-'}</td></tr>`).join('');
  document.getElementById('timeline').innerHTML = timeline || emptyState('Sin eventos de timeline.', 4);
}

async function drift() {
  const d = await loadJSON('data/drift.json', { drift_level: 'GREEN', decision: '-', thresholds: [] });
  document.getElementById('driftLight').innerHTML = badge(d.drift_level || 'GREEN');
  document.getElementById('decision').textContent = d.decision || '-';
  const thresholds = (d.thresholds || []).map((t) =>
    `<tr><td>${t.name}</td><td>${t.observed}</td><td>${t.threshold}</td><td>${t.severity}</td></tr>`).join('');
  document.getElementById('thresholds').innerHTML = thresholds || emptyState('Sin umbrales reportados.', 4);
}

async function incidents() {
  const i = await loadJSON('data/incidents.json', { status: 'OK', severity: 'SEV4', summary: {}, impact: {} });
  document.getElementById('status').innerHTML = `${badge(i.status || 'OK')} ${badge(i.severity || 'SEV4')}`;
  document.getElementById('symptom').textContent = i.summary?.symptom || '-';
  const impact = Object.entries(i.impact || {}).map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join('');
  document.getElementById('impact').innerHTML = impact || emptyState('Sin impacto reportado.', 2);
}

const page = document.body.dataset.page;
setNav(page);
({ home, forecasts, tickers, strategies, models, drift, incidents }[page] || (() => {}))();
