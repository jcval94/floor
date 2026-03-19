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

function safeJSON(value) {
  return JSON.stringify(value ?? {}, null, 2);
}

function listItems(rows) {
  if (!rows.length) return '<li>-</li>';
  return rows.map((x) => `<li>${x}</li>`).join('');
}

function fmtMaybe(value, digits = 3) {
  if (value === null || value === undefined || value === '') return '-';
  const n = Number(value);
  return Number.isFinite(n) ? fmt(n, digits) : String(value);
}

function normalizeState(state) {
  return String(state || '-').trim().toUpperCase();
}

function stateTone(state) {
  const normalized = normalizeState(state);
  if ([
    'RED', 'ALERT', 'ESCALATE', 'CRITICAL', 'HIGH', 'FAIL', 'FAILED', 'BAD', 'ERROR',
  ].includes(normalized)) return 'bad';
  if ([
    'YELLOW', 'WARN', 'WARNING', 'MEDIUM', 'PENDING', 'REVIEW', 'UNKNOWN',
  ].includes(normalized)) return 'warn';
  return 'ok';
}

function stateChip(label, state, score) {
  const tone = stateTone(state);
  const normalized = normalizeState(state);
  const scoreText = fmtMaybe(score, 2);
  return `<span class="state-chip ${tone}"><strong>${label}</strong><span>${normalized}</span><span>${scoreText}</span></span>`;
}

function _selectPrimaryForecast(rows) {
  const safeRows = rows || [];
  return safeRows.find((r) => r.horizon === 'd1')
    || safeRows.find((r) => r.horizon === 'q1')
    || safeRows[0]
    || {};
}

function _opportunityMetrics(primaryForecast) {
  const floor = Number(primaryForecast.floor_value);
  const ceiling = Number(primaryForecast.ceiling_value);
  const spreadAbs = Math.max(ceiling - floor, 0);
  const midpoint = (ceiling + floor) / 2;
  const spreadRel = spreadAbs / Math.max(Math.abs(midpoint), 1e-6);
  const floorProb = Number(primaryForecast.floor_time_probability || 0.5);
  const ceilingProb = Number(primaryForecast.ceiling_time_probability || 0.5);
  const confidence = Math.max(0, Math.min(1, (floorProb + ceilingProb) / 2));
  const score = spreadAbs * spreadRel * confidence;
  return { spreadAbs, spreadRel, confidence, score };
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
  const cards = Object.entries(grouped)
    .map(([symbol, rows]) => {
      const primaryForecast = _selectPrimaryForecast(rows);
      const m3 = _extractM3(rows);
      const metrics = _opportunityMetrics(primaryForecast);
      const current = (Number(primaryForecast.floor_value) + Number(primaryForecast.ceiling_value)) / 2;
      const horizon = String(primaryForecast.horizon || '-').toUpperCase();
      return {
        score: metrics.score,
        html: `<div class=\"card\"><h3>${symbol}</h3>
      <div class=\"small\">${horizon} ${badge(primaryForecast.floor_time_bucket || '-')} / ${badge(primaryForecast.ceiling_time_bucket || '-')}</div>
      ${rangeSvg(Number(primaryForecast.floor_value), current, Number(primaryForecast.ceiling_value))}
      <div class=\"small\"><strong>Oportunidad:</strong> abs=${fmt(metrics.spreadAbs)} · rel=${fmt(metrics.spreadRel * 100)}%</div>
      <div class=\"small\">Score objetivo=${fmt(metrics.score)} · confianza temporal=${fmt(metrics.confidence)}</div>
      <div class=\"small\"><strong>3M Downside Window:</strong> floor_m3=${fmt(m3.floor)} · ${m3.labelHuman}</div>
      <div class=\"small\">Rango semana prevista: ${m3.start || '-'} → ${m3.end || '-'} · conf=${fmt(m3.conf)} · ${m3.proximity}</div>
      <div class=\"small\">Δ vs snapshot previo: ${fmt(m3.delta)} ${m3.material ? '(material)' : ''}</div>
      ${m3WeekBarsSvg(m3.top3)}
      <a href=\"tickers.html?ticker=${symbol}\">Detalle ticker</a></div>`,
      };
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, 10)
    .map((x) => x.html);
  root.innerHTML = cards.length
    ? cards.join('')
    : '<div class="card"><h3>Sin pronósticos</h3><div class="small">No se encontraron filas en data/forecasts.json.</div></div>';

  const opportunities = data.top_opportunities.slice(0, 10).map((x) =>
    `<tr><td>${x.symbol}</td><td>${x.horizon}</td><td>${fmt(x.floor)}</td><td>${fmt(x.ceiling)}</td><td>${fmt(x.spread)}</td><td>${fmt((x.spread_relative_pct ?? 0))}%</td><td>${fmt(x.opportunity_score)}</td></tr>`).join('');
  document.getElementById('opps').innerHTML = opportunities || emptyState('No hay oportunidades para mostrar.', 7);

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
  const latestIntraday = forecasts.latest_intraday || {};
  const latestClose = forecasts.latest_close || {};
  const table = document.getElementById('tickersTable');
  const horizonFilter = document.getElementById('horizonFilter');
  let currentSort = { column: 12, direction: 'desc' };

  function pickByHorizon(rows, horizon) {
    if (horizon === 'm3') {
      return rows.find((r) => r.horizon === 'm3') || _selectPrimaryForecast(rows);
    }
    return rows.find((r) => r.horizon === horizon) || _selectPrimaryForecast(rows);
  }

  function pctDelta(value, reference) {
    const val = Number(value);
    const ref = Number(reference);
    if (!Number.isFinite(val) || !Number.isFinite(ref) || Math.abs(ref) < 1e-9) return null;
    return ((val - ref) / ref) * 100;
  }

  function rowScore(recent, floor, ceiling) {
    if (![recent, floor, ceiling].every(Number.isFinite)) return null;
    const toFloor = Math.max(recent - floor, 0);
    const toCeiling = Math.max(ceiling - recent, 0);
    const denom = Math.max(recent, 1e-6);
    return ((toCeiling / denom) * 100) - ((toFloor / denom) * 100);
  }

  function cellSortValue(rowElement, idx) {
    const text = (rowElement.children[idx]?.innerText || '').trim();
    const numeric = Number(text.replace('%', '').replace(/,/g, ''));
    return Number.isFinite(numeric) ? numeric : text.toLowerCase();
  }

  function sortRenderedRows() {
    const idx = Number(currentSort.column);
    const dir = currentSort.direction === 'asc' ? 1 : -1;
    const rows = [...table.closest('table').querySelectorAll('tbody tr')];
    rows.sort((a, b) => {
      const av = cellSortValue(a, idx);
      const bv = cellSortValue(b, idx);
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir;
      return av.localeCompare(bv, undefined, { numeric: true }) * dir;
    });
    rows.forEach((r) => table.appendChild(r));
  }

  function renderTable() {
    const selectedHorizon = (horizonFilter?.value || 'd1').toLowerCase();
    const rowsForTable = universe.symbols.map((s) => {
      const rows = grouped[s] || [];
      const chosen = pickByHorizon(rows, selectedHorizon);
      const d1 = rows.find((r) => r.horizon === 'd1') || chosen;
      const m3 = _extractM3(rows);
      const intradayFromEngine = Number(latestIntraday[s]?.price);
      const closeFromEngine = Number(latestClose[s]?.close);
      const fallbackMidpoint = (Number(d1?.floor_value) + Number(d1?.ceiling_value)) / 2;

      const recent = Number.isFinite(intradayFromEngine)
        ? intradayFromEngine
        : Number.isFinite(closeFromEngine) ? closeFromEngine : fallbackMidpoint;
      const closeDaily = Number.isFinite(closeFromEngine) ? closeFromEngine : null;
      const chosenFloor = Number(chosen?.floor_value);
      const chosenCeiling = Number(chosen?.ceiling_value);
      const deltaRecentVsClosePct = closeDaily == null ? null : pctDelta(recent, closeDaily);
      const deltaFloorPct = pctDelta(chosenFloor, recent);
      const deltaCeilingPct = pctDelta(chosenCeiling, recent);
      const deltaM3Pct = pctDelta(Number(m3.floor), recent);
      const score = selectedHorizon === 'm3'
        ? deltaM3Pct
        : rowScore(recent, chosenFloor, chosenCeiling);

        return {
          symbol: s,
          score,
          html: `<tr><td><a href="tickers.html?ticker=${s}">${s}</a></td><td>${fmt(recent)}</td><td>${closeDaily == null ? '-' : fmt(closeDaily)}</td><td>${deltaRecentVsClosePct == null ? '-' : `${fmt(deltaRecentVsClosePct)}%`}</td><td>${fmt(chosenFloor)}</td><td>${deltaFloorPct == null ? '-' : `${fmt(deltaFloorPct)}%`}</td><td>${fmt(chosenCeiling)}</td><td>${deltaCeilingPct == null ? '-' : `${fmt(deltaCeilingPct)}%`}</td><td>${fmt(m3.floor)}</td><td>${deltaM3Pct == null ? '-' : `${fmt(deltaM3Pct)}%`}</td><td>${m3WeekHumanLabel(m3.week)}</td><td>${m3.start || '-'} → ${m3.end || '-'}</td><td>${fmt(score)}</td></tr>`,
        };
      }).sort((a, b) => (Number(b.score) - Number(a.score)));

    table.innerHTML = rowsForTable.map((x) => x.html).join('');
    sortRenderedRows();
  }

  renderTable();
  horizonFilter?.addEventListener('change', renderTable);

  if (route.ticker) {
    const rows = grouped[route.ticker] || [];
    document.getElementById('tickerDetail').innerHTML = `<div class="card"><h3>${route.ticker}</h3>${rows.length ? rows.map((r) =>
      `<div>${r.horizon.toUpperCase()}: floor ${fmt(r.floor_value)} · ceiling ${fmt(r.ceiling_value)} · floor_t ${badge(String(r.floor_time_bucket || '-'))} · ceiling_t ${badge(String(r.ceiling_time_bucket || '-'))}</div>`
    ).join('') : '<div class="small">Ticker sin pronóstico disponible en el dataset actual.</div>'}</div>`;
  }

  document.querySelectorAll('th.sortable').forEach((th) => {
    th.addEventListener('click', () => {
      const idx = Number(th.dataset.col);
      if (currentSort.column === idx) {
        currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
      } else {
        currentSort.column = idx;
        currentSort.direction = 'asc';
      }
      sortRenderedRows();
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
  const models = await loadJSON('data/models.json', {
    champion: 'unknown',
    timeline: [],
    health: {},
    suite_status: 'UNKNOWN',
    suite_recommendation: 'PENDING',
    retraining_schedule: {},
    details: {},
  });
  document.getElementById('champion').textContent = models.champion;
  const health = models.health || {};
  document.getElementById('calibration').textContent = safeJSON(health);
  const hint = document.getElementById('modelsHint');
  const hasSeries = Array.isArray(health.series) && health.series.length > 0;
  if (hint) {
    hint.textContent = hasSeries
      ? ''
      : 'Sin métricas públicas todavía: falta generar data/metrics/public_metrics.json y reconstruir los datos del sitio.';
  }

  const schedule = models.retraining_schedule || {};
  const retrainingRoot = document.getElementById('retrainingSchedule');
  if (retrainingRoot) {
    retrainingRoot.innerHTML = `
      <div class="small">Cadencia: ${schedule.cadence_days ?? '-'} días</div>
      <div class="small">Última revisión: ${schedule.last_review_at || '-'}</div>
      <div class="small">Próxima revisión: ${schedule.next_review_at || '-'}</div>
      <div class="small">ETA: ${schedule.human_eta || '-'}</div>
      <div class="small">Vencido: ${schedule.is_overdue === null || schedule.is_overdue === undefined ? '-' : (schedule.is_overdue ? 'Sí' : 'No')}</div>
    `;
  }

  const suiteRoot = document.getElementById('suiteStatus');
  if (suiteRoot) {
    const sync = models.sync_status || {};
    suiteRoot.innerHTML = `
      <div class="small">Estado suite: ${badge(models.suite_status || 'UNKNOWN')}</div>
      <div class="small">Recomendación: ${badge(models.suite_recommendation || 'PENDING')}</div>
      <div class="small">Sync modelos↔resumen: ${sync.review_summary_stale ? badge('STALE') : badge('OK')}</div>
      <div class="small">Último artefacto modelo: ${sync.latest_model_artifact_at || '-'}</div>
    `;
  }

  const detailsRows = Object.values(models.details || {}).map((detail) => {
    const metricEntries = Object.entries(detail?.metrics?.current || {});
    const drift = detail?.drift_components || {};
    const paramsText = safeJSON(detail.artifact?.params || {});
    const recommendationTone = stateTone(detail.recommendation || 'UNKNOWN');
    return `<tr>
      <td>${detail.model_key || '-'}</td>
      <td>
        <div class="model-head">${detail.model_name || '-'}</div>
        <div class="small">${detail.current_version || '-'}</div>
      </td>
      <td>${detail.current_version || '-'}</td>
      <td><div class="status-group">${badge(detail.status || 'UNKNOWN')} ${badge(detail.drift_level || 'GREEN')}</div></td>
      <td>
        <div class="recommendation-pill ${recommendationTone}">${detail.recommendation || '-'}</div>
        <div class="small">${detail.auto_retrain ? 'Auto-retrain habilitado' : 'Auto-retrain deshabilitado'}</div>
      </td>
      <td>
        <div class="metrics-grid">${metricEntries.length
          ? metricEntries.map(([k, v]) => `<div class="metric-pill"><span>${k}</span><strong>${fmtMaybe(v)}</strong></div>`).join('')
          : '<div class="small">Sin métricas actuales.</div>'}
        </div>
      </td>
      <td><pre class="small params-block">${paramsText}</pre></td>
      <td>
        <div class="drift-grid">
          ${stateChip('shared', drift.shared_data?.state, drift.shared_data?.score)}
          ${stateChip('target', drift.target?.state, drift.target?.score)}
          ${stateChip('schema', drift.schema?.state, drift.schema?.score)}
          ${stateChip('perf', drift.performance?.state, drift.performance?.score)}
        </div>
        <div class="small reason-text">${detail.reason || '-'}</div>
      </td>
    </tr>`;
  }).join('');
  const detailsRoot = document.getElementById('modelDetails');
  if (detailsRoot) {
    detailsRoot.innerHTML = detailsRows || emptyState('Sin detalles de modelos disponibles.', 8);
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
