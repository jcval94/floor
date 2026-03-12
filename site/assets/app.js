import { loadJSON, bySymbol, badge, fmt, m3WeekHumanLabel, m3ProximityLabel } from './utils.js';
import { lineSvg, rangeSvg, m3WeekBarsSvg } from './charts.js';
import { initRouter } from './router.js';

function setNav(page) {
  document.querySelectorAll('[data-nav]').forEach((a) => {
    if (a.dataset.nav === page) a.classList.add('active');
  });
}

function _extractM3(rows = []) {
  const row = rows.find((r) => r.horizon === 'm3') || rows[0] || {};
  const week = Number(row.floor_week_m3 ?? row.floor_time_bucket ?? 0);
  return {
    floor: row.floor_m3 ?? row.floor_value,
    week,
    start: row.floor_week_m3_start_date,
    end: row.floor_week_m3_end_date,
    conf: row.floor_week_m3_confidence ?? row.floor_time_probability,
    top3: row.floor_week_m3_top3 || [],
    delta: row.m3_delta_vs_prev,
    material: row.m3_material_change,
    labelHuman: row.floor_week_m3_label_human || m3WeekHumanLabel(week),
    proximity: row.m3_week_proximity || m3ProximityLabel(week),
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
  root.innerHTML = Object.entries(grouped).map(([symbol, rows]) => {
    const d1 = rows.find((r) => r.horizon === 'd1') || rows[0] || {};
    const current = (Number(d1.floor_value || 0) + Number(d1.ceiling_value || 0)) / 2;
    const m3 = _extractM3(rows);
    return `<div class="card"><h3>${symbol}</h3>
      <div class="small">D1 ${badge(d1.floor_time_bucket || '-')} / ${badge(d1.ceiling_time_bucket || '-')}</div>
      ${rangeSvg(Number(d1.floor_value), current, Number(d1.ceiling_value))}
      <div class="small"><strong>3M Downside Window:</strong> floor_m3=${fmt(m3.floor)} · ${m3.labelHuman}</div>
      <div class="small">Rango semana prevista: ${m3.start || '-'} → ${m3.end || '-'} · conf=${fmt(m3.conf)} · ${m3.proximity}</div>
      <div class="small">Δ vs snapshot previo: ${fmt(m3.delta)} ${m3.material ? '(material)' : ''}</div>
      ${m3WeekBarsSvg(m3.top3)}
      <a href="tickers.html?ticker=${symbol}">Detalle ticker</a></div>`;
  }).join('');

  document.getElementById('opps').innerHTML = data.top_opportunities.slice(0, 10).map((x) =>
    `<tr><td>${x.symbol}</td><td>${x.horizon}</td><td>${fmt(x.floor)}</td><td>${fmt(x.ceiling)}</td><td>${fmt(x.spread)}</td></tr>`).join('');

  const m3Table = document.getElementById('m3TopWeeks');
  if (m3Table) {
    m3Table.innerHTML = Object.entries(grouped).map(([symbol, rows]) => {
      const m3 = _extractM3(rows);
      const top3 = (m3.top3 || []).slice(0, 3).map((w) => `W${String(w.week || '-').padStart(2, '0')} ${(100 * Number(w.probability || w.prob || 0)).toFixed(1)}%`).join(' · ');
      return `<tr><td>${symbol}</td><td>${fmt(m3.floor)}</td><td>${m3.labelHuman}</td><td>${m3.start || '-'} → ${m3.end || '-'}</td><td>${top3 || '-'}</td><td>${m3.material ? 'Sí' : 'No'}</td></tr>`;
    }).join('');
  }
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
    const m3 = _extractM3(rows);
    return `<tr><td><a href="tickers.html?ticker=${s}">${s}</a></td><td>${fmt(d1.floor_value)}</td><td>${fmt(d1.ceiling_value)}</td><td>${fmt(m3.floor)}</td><td>${m3WeekHumanLabel(m3.week)}</td><td>${m3.start || '-'} → ${m3.end || '-'}</td></tr>`;
  }).join('');

  if (route.ticker) {
    const rows = grouped[route.ticker] || [];
    const m3 = _extractM3(rows);
    document.getElementById('tickerDetail').innerHTML = `<div class="card"><h3>${route.ticker}</h3>${rows.map((r) =>
      `<div>${r.horizon.toUpperCase()}: floor ${fmt(r.floor_value)} · ceiling ${fmt(r.ceiling_value)} · floor_t ${badge(String(r.floor_time_bucket || '-'))} · ceiling_t ${badge(String(r.ceiling_time_bucket || '-'))}</div>`
    ).join('')}
      <hr/><div><strong>3M Downside Window</strong></div>
      <div>${m3WeekHumanLabel(m3.week)} · floor_m3 ${fmt(m3.floor)} · conf ${fmt(m3.conf)}</div>
      <div>Rango de fechas: ${m3.start || '-'} → ${m3.end || '-'} · proximidad: ${m3.proximity}</div>
      <div>Δ forecast m3 vs snapshot previo: ${fmt(m3.delta)} ${m3.material ? '(material)' : ''}</div>
      ${m3WeekBarsSvg(m3.top3)}
    </div>`;
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
  const [models, forecasts] = await Promise.all([
    loadJSON('data/models.json', { champion: 'unknown', timeline: [], health: {} }),
    loadJSON('data/forecasts.json', { rows: [] }),
  ]);
  document.getElementById('champion').textContent = models.champion;
  document.getElementById('calibration').textContent = JSON.stringify(models.health).slice(0, 220);
  document.getElementById('timeline').innerHTML = (models.timeline || []).map((x) =>
    `<tr><td>${x.as_of || '-'}</td><td>${x.model_name || '-'}</td><td>${x.action || '-'}</td><td>${x.drift_level || '-'}</td></tr>`).join('');

  const grouped = bySymbol(forecasts.rows || []);
  const m3Stability = document.getElementById('m3Stability');
  if (m3Stability) {
    m3Stability.innerHTML = Object.entries(grouped).map(([symbol, rows]) => {
      const m3 = _extractM3(rows);
      return `<tr><td>${symbol}</td><td>${fmt(m3.floor)}</td><td>${m3WeekHumanLabel(m3.week)}</td><td>${fmt(m3.delta)}</td><td>${m3.material ? 'Sí' : 'No'}</td></tr>`;
    }).join('');
  }
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
