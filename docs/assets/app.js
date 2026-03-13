import { loadJSON, bySymbol, badge, fmt, m3WeekHumanLabel, m3ProximityLabel } from './utils.js';
import { lineSvg, rangeSvg, m3WeekBarsSvg } from './charts.js';
import { initRouter } from './router.js';

function setNav(page) {
  document.querySelectorAll('[data-nav]').forEach((a) => {
    if (a.dataset.nav === page) a.classList.add('active');
  });
}

function emptyState(message, colspan = 1) {
  return `<tr><td colspan="${colspan}" class="small">${message}</td></tr>`;
}

function _selectPrimaryForecast(rows) {
  const safeRows = rows || [];
  return safeRows.find((r) => r.horizon === 'd1')
    || safeRows.find((r) => r.horizon === 'q1')
    || safeRows[0]
    || {};
}

function _extractM3(rows) {
  const primaryForecast = _selectPrimaryForecast(rows);
  const week = Number(primaryForecast.floor_week_m3 || 0);
  const top3 = Array.isArray(primaryForecast.floor_week_m3_top3) ? primaryForecast.floor_week_m3_top3 : [];
  return {
    floor: Number(primaryForecast.floor_m3),
    week,
    conf: Number(primaryForecast.floor_week_m3_confidence || 0),
    start: primaryForecast.floor_week_m3_start_date || '',
    end: primaryForecast.floor_week_m3_end_date || '',
    labelHuman: primaryForecast.floor_week_m3_label_human || m3WeekHumanLabel(week),
    top3,
    delta: Number(primaryForecast.m3_delta_vs_prev || 0),
    material: String(primaryForecast.m3_material_change || '').toLowerCase() === 'yes',
    proximity: primaryForecast.m3_week_proximity || m3ProximityLabel(week),
  };
}

async function home() {
  const [dashboard, drift, incidents, forecasts] = await Promise.all([
    loadJSON('data/dashboard.json', {}),
    loadJSON('data/drift.json', {}),
    loadJSON('data/incidents.json', {}),
    loadJSON('data/forecasts.json', { rows: [] }),
  ]);
  const grouped = bySymbol(forecasts.rows || []);
  const m3Rows = Object.values(grouped).map((rows) => _extractM3(rows));
  const nearCount = m3Rows.filter((x) => x.proximity === 'cerca').length;
  const materialCount = m3Rows.filter((x) => Boolean(x.material)).length;

  document.getElementById('overview').innerHTML = `
    <div class="grid">
      <div class="card"><h3>System</h3>${badge(dashboard.system_health || 'UNKNOWN')}<div class="small">Predictions: ${dashboard.prediction_files || 0}</div></div>
      <div class="card"><h3>Drift</h3>${badge(drift.drift_level || 'GREEN')}<div class="small">Decision: ${drift.decision || '-'}</div></div>
      <div class="card"><h3>Incidents</h3>${badge(incidents.status || 'OK')}<div class="small">Severity: ${(incidents.severity || '-')}</div></div>
      <div class="card"><h3>3M Downside Window</h3><div class="small">Semanas m3 cercanas (1..2): ${nearCount}</div><div class="small">Cambios m3 materiales: ${materialCount}</div></div>
    </div>`;
}

async function forecasts() {
  const data = await loadJSON('data/forecasts.json', { rows: [], top_opportunities: [] });
  const grouped = bySymbol(data.rows);
  const root = document.getElementById('forecastCards');
  const cards = Object.entries(grouped).map(([symbol, rows]) => {
    const primaryForecast = _selectPrimaryForecast(rows);
    const m3 = _extractM3(rows);
    const current = (Number(primaryForecast.floor_value) + Number(primaryForecast.ceiling_value)) / 2;
    const horizon = String(primaryForecast.horizon || '-').toUpperCase();
    return `<div class="card"><h3>${symbol}</h3>
      <div class="small">${horizon} ${badge(primaryForecast.floor_time_bucket || '-')} / ${badge(primaryForecast.ceiling_time_bucket || '-')}</div>
      ${rangeSvg(Number(primaryForecast.floor_value), current, Number(primaryForecast.ceiling_value))}
      <div class="small"><strong>3M Downside Window:</strong> floor_m3=${fmt(m3.floor)} · ${m3.labelHuman}</div>
      <div class="small">Rango semana prevista: ${m3.start || '-'} → ${m3.end || '-'} · conf=${fmt(m3.conf)} · ${m3.proximity}</div>
      <div class="small">Δ vs snapshot previo: ${fmt(m3.delta)} ${m3.material ? '(material)' : ''}</div>
      ${m3WeekBarsSvg(m3.top3)}
      <a href="tickers.html?ticker=${symbol}">Detalle ticker</a></div>`;
  });
  root.innerHTML = cards.length
    ? cards.join('')
    : '<div class="card"><h3>Sin pronósticos</h3><div class="small">No se encontraron filas en data/forecasts.json.</div></div>';

  const opportunities = data.top_opportunities.slice(0, 10).map((x) =>
    `<tr><td>${x.symbol}</td><td>${x.horizon}</td><td>${fmt(x.floor)}</td><td>${fmt(x.ceiling)}</td><td>${fmt(x.spread)}</td></tr>`).join('');
  document.getElementById('opps').innerHTML = opportunities || emptyState('No hay oportunidades para mostrar.', 5);

  const m3Rows = Object.entries(grouped).map(([symbol, rows]) => ({ symbol, m3: _extractM3(rows) }));
  const m3Table = m3Rows.map(({ symbol, m3 }) =>
    `<tr><td>${symbol}</td><td>${fmt(m3.floor)}</td><td>${m3.labelHuman}</td><td>${m3.start || '-'} → ${m3.end || '-'}</td><td>${m3WeekBarsSvg(m3.top3)}</td><td>${m3.material ? 'Sí' : 'No'}</td></tr>`
  ).join('');
  const m3Root = document.getElementById('m3TopWeeks');
  if (m3Root) m3Root.innerHTML = m3Table || emptyState('No hay datos m3 para mostrar.', 6);
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
    const primaryForecast = _selectPrimaryForecast(rows);
    const m3 = _extractM3(rows);
    return `<tr><td><a href="tickers.html?ticker=${s}">${s}</a></td><td>${fmt(primaryForecast.floor_value)}</td><td>${fmt(primaryForecast.ceiling_value)}</td><td>${fmt(m3.floor)}</td><td>${m3WeekHumanLabel(m3.week)}</td><td>${m3.start || '-'} → ${m3.end || '-'}</td></tr>`;
  }).join('');

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
  const hint = document.getElementById('strategyHint');
  if (hint) {
    hint.textContent = curve.length
      ? ''
      : 'Sin datos: este panel requiere data/reports/strategy.json con una curva de equity (se genera al correr backtest/estrategias y publicar ese reporte).';
  }
  document.getElementById('equityCurve').innerHTML = lineSvg(curve.map((x) => ({ value: x.equity ?? x.value ?? 0 })));
  document.getElementById('drawdownCurve').innerHTML = lineSvg(curve.map((x) => ({ value: x.drawdown ?? 0 })));
}

async function models() {
  const [models, forecasts] = await Promise.all([
    loadJSON('data/models.json', { champion: 'unknown', timeline: [], health: {} }),
    loadJSON('data/forecasts.json', { rows: [] }),
  ]);
  document.getElementById('champion').textContent = models.champion;
  const health = models.health || {};
  document.getElementById('calibration').textContent = JSON.stringify(health, null, 2);
  const hint = document.getElementById('modelsHint');
  const hasSeries = Array.isArray(health.series) && health.series.length > 0;
  if (hint) {
    hint.textContent = hasSeries
      ? ''
      : 'Sin métricas públicas todavía: falta generar data/metrics/public_metrics.json y reconstruir los datos del sitio.';
  }
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
