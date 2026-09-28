import {
  badge,
  bySymbol,
  confidenceLabel,
  escapeHTML,
  fmt,
  fmtDateTime,
  fmtPct,
  fmtRatioPct,
  horizonCode,
  horizonLabel,
  loadJSON,
  loadJSONState,
  m3ProximityLabel,
  m3WeekHumanLabel,
  normalizeState,
  relativeDelta,
  stateTone,
} from './utils.js';
import { filterPointsByWindow, lineSvg, m3WeekBarsSvg, rangeSvg } from './charts.js';
import { initRouter } from './router.js';

const HORIZON_ORDER = ['d1', 'w1', 'q1', 'm3'];

function setNav(page) {
  const primary = ['models', 'drift', 'incidents'].includes(page) ? 'system' : page;
  document.querySelectorAll('[data-nav]').forEach((link) => {
    const active = link.dataset.nav === primary;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
}

function initNavigation() {
  const toggle = document.querySelector('[data-nav-toggle]');
  const nav = document.querySelector('[data-primary-nav]');
  if (!toggle || !nav) return;
  toggle.addEventListener('click', () => {
    const expanded = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', String(!expanded));
    nav.classList.toggle('open', !expanded);
  });
}

function emptyState(title, body = '') {
  return `<div class="empty-state"><strong>${escapeHTML(title)}</strong>${body ? `<p>${escapeHTML(body)}</p>` : ''}</div>`;
}

function emptyRow(message, colspan = 1) {
  return `<tr><td colspan="${colspan}">${emptyState(message)}</td></tr>`;
}

function safeJSON(value) {
  return escapeHTML(JSON.stringify(value ?? {}, null, 2));
}

function centralCoverageFromForecast(row) {
  const central = Number(row?.central_interval_coverage);
  if (Number.isFinite(central) && central >= 0 && central <= 1) return central;
  const breach = Number(row?.breach_probability ?? row?.breach_prob);
  if (Number.isFinite(breach) && breach >= 0 && breach <= 1) return 1 - breach;
  return null;
}

function riskCoverageFromForecast(row) {
  const jointRisk = Number(row?.risk_empirical_joint_coverage);
  if (Number.isFinite(jointRisk) && jointRisk >= 0 && jointRisk <= 1) return jointRisk;

  const explicit = Number(row?.confidence_score ?? row?.confidence);
  const semantics = String(row?.confidence_semantics || '').toLowerCase();
  if (
    semantics.includes('joint_risk')
    && Number.isFinite(explicit)
    && explicit >= 0
    && explicit <= 1
  ) {
    return explicit;
  }
  return null;
}

function confidenceFromForecast(row) {
  return riskCoverageFromForecast(row);
}

function centralCoverageChip(row) {
  const raw = centralCoverageFromForecast(row);
  const numeric = raw == null ? NaN : Number(raw);
  if (!Number.isFinite(numeric)) return '';
  return `<span class="confidence-chip neutral" title="Cobertura empírica OOS del forecast central; no pertenece al risk envelope">Central OOS · ${(numeric * 100).toFixed(0)}%</span>`;
}

function riskCoverageChip(row) {
  const raw = riskCoverageFromForecast(row);
  const numeric = raw == null ? NaN : Number(raw);
  if (!Number.isFinite(numeric)) {
    return '<span class="confidence-chip neutral">Risk OOS · no disponible</span>';
  }
  const info = confidenceLabel(numeric);
  return `<span class="confidence-chip ${info.tone}" title="Cobertura conjunta OOS exclusivamente del risk envelope">Risk OOS · ${(numeric * 100).toFixed(0)}%</span>`;
}


function confidenceChip(value) {
  const info = confidenceLabel(value);
  const numeric = Number(value);
  const suffix = Number.isFinite(numeric) ? ` · ${(numeric * 100).toFixed(0)}%` : '';
  return `<span class="confidence-chip ${info.tone}">${escapeHTML(info.label)}${suffix}</span>`;
}

function freshnessText(audit) {
  const generated = audit?.generated_at || audit?.batch?.as_of || null;
  return generated ? fmtDateTime(generated) : 'Sin timestamp verificable';
}

function publicationState(audit) {
  const status = normalizeState(audit?.status || 'UNKNOWN');
  const publishable = Boolean(audit?.publishable_forecasts);
  if (!publishable) return { status: 'BLOCKED', label: 'Pronósticos no publicables', tone: 'bad' };
  if (status === 'DEGRADED') return { status, label: 'Datos verificados con advertencias', tone: 'warn' };
  if (status === 'OK') return { status, label: 'Datos verificados', tone: 'ok' };
  return { status: 'UNKNOWN', label: 'Estado de publicación desconocido', tone: 'warn' };
}

function metricCard(label, value, detail = '', tone = 'neutral') {
  return `<article class="metric-card ${tone}">
    <span class="metric-label">${escapeHTML(label)}</span>
    <strong class="metric-value">${escapeHTML(value)}</strong>
    ${detail ? `<span class="metric-detail">${escapeHTML(detail)}</span>` : ''}
  </article>`;
}

function healthRow(component, status, detail = '') {
  return `<tr>
    <td><strong>${escapeHTML(component)}</strong>${detail ? `<div class="table-subtext">${escapeHTML(detail)}</div>` : ''}</td>
    <td>${badge(status)}</td>
  </tr>`;
}

function selectForecast(rows, horizon) {
  const safe = rows || [];
  return safe.find((row) => String(row?.horizon || '').toLowerCase() === horizon) || null;
}

function referencePrice(data, symbol, row) {
  const closeRaw = data?.latest_close?.[symbol]?.close;
  const close = closeRaw == null ? NaN : Number(closeRaw);
  if (Number.isFinite(close) && close > 0) return { value: close, source: 'Último cierre diario' };
  const floor = row?.floor_value == null ? NaN : Number(row.floor_value);
  const ceiling = row?.ceiling_value == null ? NaN : Number(row.ceiling_value);
  if (Number.isFinite(floor) && Number.isFinite(ceiling)) {
    return { value: (floor + ceiling) / 2, source: 'Punto medio del rango' };
  }
  return { value: null, source: 'No disponible' };
}

function forecastRange(row, reference) {
  const floor = Number(row?.floor_value);
  const ceiling = Number(row?.ceiling_value);
  const ref = Number(reference);
  const downside = Number.isFinite(ref) ? relativeDelta(floor, ref) : null;
  const upside = Number.isFinite(ref) ? relativeDelta(ceiling, ref) : null;
  return { floor, ceiling, downside, upside };
}

function riskRange(row, reference) {
  const available = row?.risk_geometry_available !== false;
  const floor = Number(row?.risk_floor_value);
  const ceiling = Number(row?.risk_ceiling_value);
  const ref = Number(reference);
  const valid = available && Number.isFinite(floor) && Number.isFinite(ceiling);
  const downside = valid && Number.isFinite(ref) ? relativeDelta(floor, ref) : null;
  const upside = valid && Number.isFinite(ref) ? relativeDelta(ceiling, ref) : null;
  return {
    available: valid,
    floor: valid ? floor : null,
    ceiling: valid ? ceiling : null,
    downside,
    upside,
  };
}

function compactRange(range) {
  if (!range || !Number.isFinite(Number(range.floor)) || !Number.isFinite(Number(range.ceiling))) return '—';
  return `${fmt(range.floor)} → ${fmt(range.ceiling)}`;
}

function rangeDeltas(range) {
  if (!range) return '';
  const down = range.downside == null ? '—' : fmtPct(range.downside);
  const up = range.upside == null ? '—' : fmtPct(range.upside);
  return `<span class="geometry-deltas"><span class="negative">${down}</span><span aria-hidden="true">/</span><span class="positive">${up}</span></span>`;
}

function geometryStack(symbol, horizon, row, reference) {
  const central = forecastRange(row, reference);
  const risk = riskRange(row, reference);
  const centralChip = centralCoverageChip(row);
  return `<div class="geometry-stack">
    <section class="geometry-block central-geometry" aria-label="Forecast central">
      <div class="geometry-heading">
        <div><span class="geometry-kicker">Forecast central</span><small>Estimación típica · no es el intervalo de cobertura</small></div>
        ${centralChip}
      </div>
      ${rangeSvg(central.floor, reference, central.ceiling, `${symbol} ${horizonLabel(horizon)} forecast central`)}
    </section>
    <section class="geometry-block risk-geometry" aria-label="Risk envelope">
      <div class="geometry-heading">
        <div><span class="geometry-kicker">Risk envelope</span><small>Intervalo más amplio para cobertura OOS</small></div>
        ${riskCoverageChip(row)}
      </div>
      ${risk.available
        ? rangeSvg(risk.floor, reference, risk.ceiling, `${symbol} ${horizonLabel(horizon)} risk envelope`)
        : '<div class="empty-inline">Risk envelope no disponible</div>'}
    </section>
  </div>`;
}


function extractM3(rows) {
  const source = (rows || []).find((row) => row?.horizon === 'm3') || (rows || [])[0] || {};
  const week = Number(source?.floor_week_m3);
  const confidence = Number(source?.floor_week_m3_confidence);
  const status = String(source?.m3_status || (Number.isFinite(week) ? 'ok' : 'unknown'));
  return {
    floor: Number(source?.floor_m3),
    week: Number.isFinite(week) && week > 0 ? week : null,
    confidence: Number.isFinite(confidence) ? confidence : null,
    top3: Array.isArray(source?.floor_week_m3_top3) ? source.floor_week_m3_top3 : [],
    start: source?.floor_week_m3_start_date || '',
    end: source?.floor_week_m3_end_date || '',
    delta: Number(source?.m3_delta_vs_prev),
    material: String(source?.m3_material_change || '').toLowerCase() === 'yes',
    status,
    blockReason: source?.m3_block_reason || '',
    proximity: Number.isFinite(week) ? m3ProximityLabel(week) : 'desconocida',
  };
}

function m3TimingText(m3) {
  if (m3.status === 'timing_abstained' || !m3.week) return 'Timing no disponible';
  return m3WeekHumanLabel(m3.week);
}

function renderForecastCard(symbol, row, data, rowsForSymbol) {
  const ref = referencePrice(data, symbol, row);
  const range = forecastRange(row, ref.value);
  const m3 = extractM3(rowsForSymbol);
  const horizon = String(row?.horizon || '').toLowerCase();
  const timingFloor = row?.floor_time_bucket || '—';
  const timingCeiling = row?.ceiling_time_bucket || '—';
  const skillVsDummy = Number(row?.central_skill_vs_best_dummy);
  const floorGap = ref.source === 'Último cierre diario' && Number.isFinite(range.downside)
    ? Math.abs(range.downside)
    : NaN;
  const skillText = Number.isFinite(skillVsDummy)
    ? `${skillVsDummy >= 0 ? '+' : ''}${(skillVsDummy * 100).toFixed(1)}%`
    : '—';
  return `<article class="forecast-card">
    <div class="card-head">
      <div>
        <a class="ticker-link" href="tickers.html?ticker=${encodeURIComponent(symbol)}">${escapeHTML(symbol)}</a>
        <div class="eyebrow">${escapeHTML(horizonLabel(horizon))} <span class="code-label">${escapeHTML(horizonCode(horizon))}</span></div>
      </div>
    </div>
    <div class="forecast-price-row">
      <div><span class="metric-label">Referencia</span><strong>${fmt(ref.value)}</strong><small>${escapeHTML(ref.source)}</small></div>
      <div><span class="metric-label">Piso central</span><strong>${fmt(range.floor)}</strong><small class="negative">${range.downside == null ? '—' : fmtPct(range.downside)}</small></div>
      <div><span class="metric-label">Techo central</span><strong>${fmt(range.ceiling)}</strong><small class="positive">${range.upside == null ? '—' : fmtPct(range.upside)}</small></div>
    </div>
    ${geometryStack(symbol, horizon, row, ref.value)}
    <div class="forecast-meta-grid">
      <div><span>Piso esperado</span><strong>${escapeHTML(String(timingFloor))}</strong></div>
      <div><span>Techo esperado</span><strong>${escapeHTML(String(timingCeiling))}</strong></div>
      <div><span>3M downside</span><strong>${fmt(m3.floor)}</strong></div>
      <div><span>Timing 3M</span><strong>${escapeHTML(m3TimingText(m3))}</strong></div>
      <div title="Distancia absoluta entre el último cierre diario y el piso central"><span>Dist. al piso</span><strong>${Number.isFinite(floorGap) ? fmtPct(floorGap) : '—'}</strong></div>
      <div title="Reducción de MAE de spread frente al mejor baseline global-median/ATR-only"><span>Skill vs dummy</span><strong class="${Number.isFinite(skillVsDummy) && skillVsDummy < 0 ? 'negative' : 'positive'}">${escapeHTML(skillText)}</strong></div>
    </div>
    <div class="card-actions"><a href="tickers.html?ticker=${encodeURIComponent(symbol)}">Ver detalle</a></div>
  </article>`;
}


const HOME_STRATEGY_LABELS = {
  weekly_opportunity_ridge: 'Weekly Opportunity',
  breakout_protected_by_floor: 'Momentum + Floor',
  mean_reversion_floor_w1: 'Mean Reversion + Floor',
  cross_horizon_asymmetry: 'Cross-Horizon',
  capital_allocation_challenger: 'Capital Challenger',
};

function homeOperationRows(rows, tone) {
  if (!Array.isArray(rows) || !rows.length) {
    return '<tr><td colspan="5"><div class="empty-state"><strong>Aún no hay operaciones cerradas.</strong><p>La tabla se poblará automáticamente conforme la liga cierre posiciones.</p></div></td></tr>';
  }
  return rows.map((row) => {
    const pnl = Number(row?.net_pnl);
    const efficiency = Number(row?.net_pnl_per_session);
    const netReturn = Number(row?.net_return);
    const strategy = HOME_STRATEGY_LABELS[row?.strategy] || row?.strategy || '—';
    return `<tr>
      <td><strong>${escapeHTML(row?.symbol || '—')}</strong><div class="table-subtext">${escapeHTML(strategy)}</div></td>
      <td class="${tone}">$ ${fmt(pnl, 2)}</td>
      <td class="${tone}">$ ${fmt(efficiency, 2)}/ses.</td>
      <td class="${Number.isFinite(netReturn) && netReturn >= 0 ? 'positive' : 'negative'}">${Number.isFinite(netReturn) ? fmtPct(netReturn * 100, 2) : '—'}</td>
      <td>${escapeHTML(String(row?.holding_sessions ?? '—'))}</td>
    </tr>`;
  }).join('');
}

function homeOperationsPanel(title, eyebrow, rows, tone) {
  return `<article class="panel">
    <div class="section-heading"><div><span class="eyebrow">${escapeHTML(eyebrow)}</span><h3>${escapeHTML(title)}</h3></div></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Operación</th><th>P&L neto</th><th>P&L / sesión</th><th>Retorno</th><th>Ses.</th></tr></thead>
      <tbody>${homeOperationRows(rows, tone)}</tbody>
    </table></div>
  </article>`;
}

function operationBucketKey(session, granularity) {
  const raw = String(session || '');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(raw) || granularity === 'daily') return raw;
  const date = new Date(`${raw}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return raw;
  if (granularity === 'monthly') {
    return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}-01`;
  }
  const day = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() - day + 1);
  return date.toISOString().slice(0, 10);
}

function aggregateOperationHistory(rows, windowKey, granularity) {
  const visible = filterPointsByWindow(Array.isArray(rows) ? rows : [], windowKey);
  const buckets = new Map();
  visible.forEach((row) => {
    const key = operationBucketKey(row?.session, granularity);
    if (!key) return;
    const bucket = buckets.get(key) || {
      session: key,
      net_pnl: 0,
      closed_operations: 0,
      wins: 0,
      losses: 0,
    };
    bucket.net_pnl += Number(row?.net_pnl || 0);
    bucket.closed_operations += Number(row?.closed_operations || 0);
    bucket.wins += Number(row?.wins || 0);
    bucket.losses += Number(row?.losses || 0);
    buckets.set(key, bucket);
  });
  return [...buckets.values()].sort((a, b) => String(a.session).localeCompare(String(b.session)));
}

function homeOperationsKpis(rows) {
  const pnl = rows.reduce((total, row) => total + Number(row?.net_pnl || 0), 0);
  const closed = rows.reduce((total, row) => total + Number(row?.closed_operations || 0), 0);
  const wins = rows.reduce((total, row) => total + Number(row?.wins || 0), 0);
  const losses = rows.reduce((total, row) => total + Number(row?.losses || 0), 0);
  const chip = (label, value) => `<span class="operations-kpi"><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong></span>`;
  return [
    chip('P&L visible', `$ ${fmt(pnl, 2)}`),
    chip('Cierres', String(closed)),
    chip('Ganadoras', String(wins)),
    chip('Perdedoras', String(losses)),
  ].join('');
}

function homeOpenLossRows(rows) {
  if (!Array.isArray(rows) || !rows.length) {
    return '<tr><td colspan="7"><div class="empty-state"><strong>No hay posiciones abiertas en pérdida.</strong><p>Cuando una estrategia mantenga una posición con mark-to-market negativo aparecerá aquí.</p></div></td></tr>';
  }
  return rows.map((row) => {
    const strategy = HOME_STRATEGY_LABELS[row?.strategy] || row?.strategy || '—';
    const pnl = Number(row?.unrealized_pnl);
    const ret = Number(row?.unrealized_return);
    return `<tr>
      <td><strong>${escapeHTML(row?.symbol || '—')}</strong><div class="table-subtext">${escapeHTML(strategy)}</div></td>
      <td class="negative">$ ${fmt(pnl, 2)}</td>
      <td class="negative">${Number.isFinite(ret) ? fmtPct(ret * 100, 2) : '—'}</td>
      <td>$ ${fmt(Number(row?.avg_entry_fill), 2)}</td>
      <td>$ ${fmt(Number(row?.last_price), 2)}</td>
      <td>${escapeHTML(String(row?.holding_sessions ?? '—'))}</td>
      <td>${row?.stop_price == null ? '—' : `$ ${fmt(Number(row.stop_price), 2)}`}</td>
    </tr>`;
  }).join('');
}

function homeOpenLossPanel(data) {
  const rows = Array.isArray(data?.open_losing_positions) ? data.open_losing_positions : [];
  const total = Number(data?.open_losing_unrealized_pnl || 0);
  return `<article class="panel operations-open-losses">
    <div class="section-heading"><div><span class="eyebrow">Posiciones aún abiertas</span><h3>Pérdidas no realizadas</h3><p>${rows.length} posición${rows.length === 1 ? '' : 'es'} actualmente en rojo · pérdida flotante combinada $ ${fmt(total, 2)}.</p></div></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Posición</th><th>Pérdida flotante</th><th>Retorno</th><th>Entrada</th><th>Último</th><th>Ses.</th><th>Stop</th></tr></thead>
      <tbody>${homeOpenLossRows(rows)}</tbody>
    </table></div>
    <p class="small loss-note">Mark-to-market del último EOD. Incluye costos de entrada ya pagados; no descuenta costos futuros de salida porque la posición sigue abierta.</p>
  </article>`;
}

function renderHomeOperations(payload) {
  const root = document.getElementById('homeOperations');
  const historyRoot = document.getElementById('homeOperationsHistoryChart');
  const historyMetrics = document.getElementById('homeOperationsHistoryMetrics');
  const openLossesRoot = document.getElementById('homeOpenLosses');
  const windowControl = document.getElementById('homeOperationsWindow');
  const granularityControl = document.getElementById('homeOperationsGranularity');
  if (!root && !historyRoot && !openLossesRoot) return;
  const data = payload || {};
  if (root) {
    root.innerHTML = [
      homeOperationsPanel('Más ganancia en menos tiempo', 'Top 10 operaciones', data.top_operations || [], 'positive'),
      homeOperationsPanel('Peor pérdida por tiempo expuesto', 'Bottom 10 operaciones', data.bottom_operations || [], 'negative'),
    ].join('');
  }
  if (openLossesRoot) openLossesRoot.innerHTML = homeOpenLossPanel(data);

  function renderHistory() {
    const windowKey = String(windowControl?.value || '2w');
    const granularity = String(granularityControl?.value || 'daily');
    const history = aggregateOperationHistory(data.operations_history || [], windowKey, granularity);
    if (historyMetrics) historyMetrics.innerHTML = homeOperationsKpis(history);
    if (historyRoot) {
      historyRoot.innerHTML = lineSvg(
        history.map((row) => ({ session: row.session, value: row.net_pnl })),
        {
          title: 'P&L neto realizado por periodo',
          valueFormat: 'money',
          valueDigits: 0,
          baseline: 0,
          baselineLabel: 'Break-even',
        },
      );
    }
  }

  if (windowControl) windowControl.onchange = renderHistory;
  if (granularityControl) granularityControl.onchange = renderHistory;
  renderHistory();
}

async function home() {
  const [dashboardR, driftR, incidentsR, forecastsR, auditR, modelsR, operationsR] = await Promise.all([
    loadJSONState('data/dashboard.json', {}),
    loadJSONState('data/drift.json', {}),
    loadJSONState('data/incidents.json', {}),
    loadJSONState('data/forecasts.json', { rows: [] }),
    loadJSONState('data/audit.json', {}),
    loadJSONState('data/models.json', {}),
    loadJSONState('data/strategy_league_operations.json', { status: 'WAITING_FOR_CLOSED_TRADES', top_operations: [], bottom_operations: [], operations_history: [], open_losing_positions: [] }),
  ]);
  const dashboard = dashboardR.data || {};
  const drift = driftR.data || {};
  const incidents = incidentsR.data || {};
  const forecasts = forecastsR.data || { rows: [] };
  const audit = auditR.data || {};
  const models = modelsR.data || {};
  const operations = operationsR.data || { status: 'WAITING_FOR_CLOSED_TRADES', top_operations: [], bottom_operations: [], operations_history: [], open_losing_positions: [] };
  const grouped = bySymbol(forecasts.rows || []);
  const symbols = Object.keys(grouped);
  const expected = Number(audit?.expected_prediction_rows || audit?.batch?.expected_rows || 0);
  const observed = Number(audit?.batch?.observed_rows || (forecasts.rows || []).length);
  const pub = publicationState(audit);

  const hero = document.getElementById('heroStatus');
  if (hero) {
    hero.innerHTML = `<div class="trust-strip ${pub.tone}">
      <div>${badge(pub.status, pub.label)}<span class="trust-time">Actualizado: ${escapeHTML(freshnessText(audit))}</span></div>
      <span class="trust-detail">Batch ${observed}${expected ? ` / ${expected}` : ''} filas · modelos ${escapeHTML(models.suite_status || 'UNKNOWN')}</span>
    </div>`;
  }

  const m3Rows = symbols.map((symbol) => extractM3(grouped[symbol]));
  const material = m3Rows.filter((item) => item.material).length;
  const near = m3Rows.filter((item) => item.proximity === 'cerca').length;
  const metrics = document.getElementById('overviewMetrics');
  if (metrics) {
    metrics.innerHTML = [
      metricCard('Activos monitoreados', String(symbols.length || '—'), 'Universo con forecast'),
      metricCard('Batch de forecasts', expected ? `${observed}/${expected}` : String(observed || '—'), audit?.publishable_forecasts ? 'Completo y publicable' : 'Revisar auditoría', audit?.publishable_forecasts ? 'ok' : 'warn'),
      metricCard('Horizontes', '4', '1, 5, 10 sesiones y 3 meses'),
      metricCard('Cambios materiales', String(material), '3M vs snapshot anterior', material ? 'warn' : 'neutral'),
    ].join('');
  }

  const snapshot = document.getElementById('forecastSnapshot');
  if (snapshot) {
    const preferred = symbols.slice(0, 8).map((symbol) => {
      const rows = grouped[symbol];
      const row = selectForecast(rows, 'w1') || selectForecast(rows, 'q1') || selectForecast(rows, 'd1');
      if (!row) return '';
      const ref = referencePrice(forecasts, symbol, row);
      const range = forecastRange(row, ref.value);
      const risk = riskRange(row, ref.value);
      return `<tr>
        <td><a class="ticker-link compact" href="tickers.html?ticker=${encodeURIComponent(symbol)}">${escapeHTML(symbol)}</a></td>
        <td>${fmt(ref.value)}</td>
        <td>${escapeHTML(horizonLabel(row.horizon))}</td>
        <td><strong class="range-text">${compactRange(range)}</strong>${rangeDeltas(range)}<div class="table-subtext">Forecast central</div></td>
        <td><strong class="range-text">${risk.available ? compactRange(risk) : '—'}</strong><div class="table-subtext">Risk envelope</div>${riskCoverageChip(row)}</td>
      </tr>`;
    }).filter(Boolean).join('');
    snapshot.innerHTML = preferred || emptyRow('No hay forecasts publicables para mostrar.', 7);
  }

  const changes = document.getElementById('changesSnapshot');
  if (changes) {
    const items = symbols
      .map((symbol) => ({ symbol, m3: extractM3(grouped[symbol]) }))
      .filter(({ m3 }) => m3.material || (Number.isFinite(m3.delta) && Math.abs(m3.delta) > 0))
      .sort((a, b) => Math.abs(b.m3.delta || 0) - Math.abs(a.m3.delta || 0))
      .slice(0, 6);
    changes.innerHTML = items.length ? items.map(({ symbol, m3 }) => `
      <a class="change-row" href="tickers.html?ticker=${encodeURIComponent(symbol)}">
        <span><strong>${escapeHTML(symbol)}</strong><small>Floor 3M</small></span>
        <span class="${m3.delta < 0 ? 'negative' : 'positive'}">${Number.isFinite(m3.delta) ? `${m3.delta >= 0 ? '+' : ''}${fmt(m3.delta)}` : '—'}</span>
        <span>${m3.material ? badge('WARN', 'Cambio material') : '<span class="muted">Cambio menor</span>'}</span>
      </a>`).join('') : emptyState('Sin cambios materiales', 'No hay variaciones relevantes frente al snapshot anterior.');
  }

  const health = document.getElementById('homeHealth');
  if (health) {
    const driftState = driftR.ok ? (drift.drift_level || 'UNKNOWN') : 'UNKNOWN';
    const incidentState = incidentsR.ok ? (incidents.status || 'UNKNOWN') : 'UNKNOWN';
    const modelState = modelsR.ok ? (models.suite_status || 'UNKNOWN') : 'UNKNOWN';
    health.innerHTML = [
      healthRow('Publicación', pub.status, auditR.ok ? freshnessText(audit) : 'audit.json no disponible'),
      healthRow('Modelos', modelState, models.suite_recommendation || ''),
      healthRow('Drift', driftState, drift.decision || ''),
      healthRow('Incidentes', incidentState, incidents.severity || ''),
      healthRow('Pipeline', dashboardR.ok ? (dashboard.system_health || 'UNKNOWN') : 'UNKNOWN', dashboardR.ok ? 'Dashboard cargado' : 'dashboard.json no disponible'),
    ].join('');
  }

  renderHomeOperations(operations);

  const watch = document.getElementById('m3Watch');
  if (watch) {
    watch.innerHTML = `<div class="watch-summary"><strong>${near}</strong><span>activos con timing 3M cercano</span></div><div class="watch-summary"><strong>${material}</strong><span>cambios materiales 3M</span></div>`;
  }
}

async function forecasts() {
  const dataResult = await loadJSONState('data/forecasts.json', { rows: [], top_opportunities: [] });
  const data = dataResult.data || { rows: [] };
  const grouped = bySymbol(data.rows || []);
  const search = document.getElementById('forecastSearch');
  const horizon = document.getElementById('forecastHorizon');
  const confidence = document.getElementById('forecastConfidence');
  const sort = document.getElementById('forecastSort');
  const root = document.getElementById('forecastCards');
  const summary = document.getElementById('forecastSummary');
  const tableRoot = document.getElementById('forecastTable');
  const m3Root = document.getElementById('m3WatchTable');

  function render() {
    const query = String(search?.value || '').trim().toUpperCase();
    const selectedHorizon = String(horizon?.value || 'w1').toLowerCase();
    const minConfidence = Number(confidence?.value || 0);
    const selectedSort = String(sort?.value || 'floor_proximity');
    const items = Object.entries(grouped).map(([symbol, rows]) => {
      const row = selectForecast(rows, selectedHorizon);
      if (!row) return null;
      const conf = confidenceFromForecast(row);
      if (query && !symbol.toUpperCase().includes(query)) return null;
      if (minConfidence > 0 && (!Number.isFinite(conf) || conf < minConfidence)) return null;

      const ref = referencePrice(data, symbol, row);
      const range = forecastRange(row, ref.value);
      const downside = Number.isFinite(range.downside) ? Math.abs(range.downside) : NaN;
      const floorGap = ref.source === 'Último cierre diario' && Number.isFinite(downside) ? downside : NaN;
      const upside = Number.isFinite(range.upside) ? range.upside : NaN;
      const skew = Number.isFinite(upside) && Number.isFinite(downside) ? upside - downside : NaN;
      const width = Number.isFinite(upside) && Number.isFinite(downside) ? upside + downside : NaN;
      const rawSkill = row?.central_skill_vs_best_dummy;
      const skill = rawSkill == null || rawSkill === '' ? NaN : Number(rawSkill);

      return { symbol, rows, row, conf, ref, range, downside, floorGap, upside, skew, width, skill };
    }).filter(Boolean);

    const finiteCompare = (a, b, direction = 'desc') => {
      const af = Number.isFinite(a);
      const bf = Number.isFinite(b);
      if (af && !bf) return -1;
      if (!af && bf) return 1;
      if (!af && !bf) return 0;
      return direction === 'asc' ? a - b : b - a;
    };

    items.sort((a, b) => {
      let cmp = 0;
      if (selectedSort === 'floor_proximity') {
        cmp = finiteCompare(a.floorGap, b.floorGap, 'asc');
        if (!cmp) cmp = finiteCompare(a.skill, b.skill, 'desc');
        if (!cmp) cmp = finiteCompare(a.conf, b.conf, 'desc');
      } else if (selectedSort === 'floor_distance') cmp = finiteCompare(a.floorGap, b.floorGap, 'desc');
      else if (selectedSort === 'right_skew') cmp = finiteCompare(a.skew, b.skew, 'desc');
      else if (selectedSort === 'left_skew') cmp = finiteCompare(a.skew, b.skew, 'asc');
      else if (selectedSort === 'balanced') cmp = finiteCompare(Math.abs(a.skew), Math.abs(b.skew), 'asc');
      else if (selectedSort === 'upside') cmp = finiteCompare(a.upside, b.upside, 'desc');
      else if (selectedSort === 'upside_asc') cmp = finiteCompare(a.upside, b.upside, 'asc');
      else if (selectedSort === 'wide') cmp = finiteCompare(a.width, b.width, 'desc');
      else if (selectedSort === 'narrow') cmp = finiteCompare(a.width, b.width, 'asc');
      else if (selectedSort === 'skill') cmp = finiteCompare(a.skill, b.skill, 'desc');
      else if (selectedSort === 'skill_asc') cmp = finiteCompare(a.skill, b.skill, 'asc');
      else if (selectedSort === 'coverage_asc') cmp = finiteCompare(a.conf, b.conf, 'asc');
      else if (selectedSort === 'ticker') cmp = a.symbol.localeCompare(b.symbol);
      else if (selectedSort === 'ticker_desc') cmp = b.symbol.localeCompare(a.symbol);
      else cmp = finiteCompare(a.conf, b.conf, 'desc');

      return cmp || a.symbol.localeCompare(b.symbol);
    });

    if (summary) {
      const orderLabel = sort?.selectedOptions?.[0]?.textContent || 'Mayor cobertura';
      summary.innerHTML = `<strong>${items.length}</strong> activos · ${escapeHTML(horizonLabel(selectedHorizon))} · orden: ${escapeHTML(orderLabel)}`;
    }
    if (root) {
      root.innerHTML = items.length
        ? items.slice(0, 12).map(({ symbol, row, rows }) => renderForecastCard(symbol, row, data, rows)).join('')
        : emptyState(dataResult.ok ? 'Sin resultados' : 'No se pudieron cargar los forecasts', dataResult.ok ? 'Prueba con otros filtros.' : dataResult.error);
    }
    if (tableRoot) {
      tableRoot.innerHTML = items.map(({ symbol, row, ref, range, floorGap }) => {
        const risk = riskRange(row, ref.value);
        return `<tr>
          <td><a class="ticker-link compact" href="tickers.html?ticker=${encodeURIComponent(symbol)}">${escapeHTML(symbol)}</a></td>
          <td>${fmt(ref.value)}<div class="table-subtext">${escapeHTML(ref.source)}</div></td>
          <td><strong>${Number.isFinite(floorGap) ? fmtPct(floorGap) : '—'}</strong><div class="table-subtext">abs. vs referencia</div></td>
          <td><strong class="range-text">${compactRange(range)}</strong>${rangeDeltas(range)}<div class="table-subtext">Forecast central</div>${centralCoverageChip(row)}</td>
          <td><strong class="range-text">${risk.available ? compactRange(risk) : '—'}</strong>${risk.available ? rangeDeltas(risk) : ''}<div class="table-subtext">Risk envelope</div>${riskCoverageChip(row)}</td>
        </tr>`;
      }).join('') || emptyRow('No hay datos que coincidan con los filtros.', 5);
    }
  }

  [search, horizon, confidence, sort].forEach((control) => {
    control?.addEventListener(control === search ? 'input' : 'change', render);
  });
  render();

  if (m3Root) {
    const rows = Object.entries(grouped).map(([symbol, symbolRows]) => ({ symbol, m3: extractM3(symbolRows) }))
      .sort((a, b) => (a.m3.week || 99) - (b.m3.week || 99) || a.symbol.localeCompare(b.symbol));
    m3Root.innerHTML = rows.map(({ symbol, m3 }) => `<tr>
      <td><a class="ticker-link compact" href="tickers.html?ticker=${encodeURIComponent(symbol)}">${escapeHTML(symbol)}</a></td>
      <td>${fmt(m3.floor)}</td>
      <td>${escapeHTML(m3TimingText(m3))}</td>
      <td>${m3.confidence == null ? '—' : confidenceChip(m3.confidence)}</td>
      <td>${m3.start || m3.end ? `${escapeHTML(m3.start || '—')} → ${escapeHTML(m3.end || '—')}` : '—'}</td>
      <td>${m3.material ? badge('WARN', 'Material') : '<span class="muted">Sin cambio material</span>'}</td>
    </tr>`).join('') || emptyRow('Sin datos 3M.', 6);
  }
}

async function tickers() {
  const [universeR, forecastsR] = await Promise.all([
    loadJSONState('data/universe.json', { symbols: [] }),
    loadJSONState('data/forecasts.json', { rows: [] }),
  ]);
  const universe = universeR.data || { symbols: [] };
  const data = forecastsR.data || { rows: [] };
  const grouped = bySymbol(data.rows || []);
  const search = document.getElementById('tickerSearch');
  const horizon = document.getElementById('horizonFilter');
  const confidenceFilter = document.getElementById('tickerConfidence');
  const table = document.getElementById('tickersTable');
  const detail = document.getElementById('tickerDetail');
  const count = document.getElementById('tickerCount');
  const route = initRouter();
  let currentSort = { key: 'confidence', direction: 'desc' };

  if (route.ticker && search) search.value = route.ticker;

  function buildRows() {
    const selected = String(horizon?.value || 'w1').toLowerCase();
    const query = String(search?.value || '').trim().toUpperCase();
    const minConfidence = Number(confidenceFilter?.value || 0);
    return (universe.symbols || Object.keys(grouped)).map((symbol) => {
      const rows = grouped[symbol] || [];
      const row = selectForecast(rows, selected);
      if (!row) return null;
      const ref = referencePrice(data, symbol, row);
      const range = forecastRange(row, ref.value);
      const risk = riskRange(row, ref.value);
      const conf = confidenceFromForecast(row);
      if (query && !String(symbol).toUpperCase().includes(query)) return null;
      if (minConfidence > 0 && (!Number.isFinite(conf) || conf < minConfidence)) return null;
      return { symbol, row, rows, ref, range, risk, confidence: conf };
    }).filter(Boolean);
  }

  function sortRows(rows) {
    const direction = currentSort.direction === 'asc' ? 1 : -1;
    return rows.sort((a, b) => {
      const value = (item) => {
        if (currentSort.key === 'symbol') return item.symbol;
        if (currentSort.key === 'price') return item.ref.value ?? -Infinity;
        if (currentSort.key === 'downside') return item.range.downside ?? -Infinity;
        if (currentSort.key === 'upside') return item.range.upside ?? -Infinity;
        return item.confidence ?? -Infinity;
      };
      const av = value(a);
      const bv = value(b);
      if (typeof av === 'string') return av.localeCompare(String(bv)) * direction;
      return (Number(av) - Number(bv)) * direction;
    });
  }

  function renderDetail(symbol) {
    if (!detail) return;
    const rows = grouped[symbol] || [];
    if (!rows.length) {
      detail.innerHTML = emptyState('Ticker sin forecast', 'No hay datos disponibles para este activo en el batch actual.');
      return;
    }
    const blocks = HORIZON_ORDER.map((key) => {
      const row = selectForecast(rows, key);
      if (!row) return '';
      if (key === 'm3') {
        const m3 = extractM3(rows);
        return `<article class="detail-horizon">
          <div class="detail-title"><strong>${escapeHTML(horizonLabel(key))}</strong><span class="code-label">${escapeHTML(key)}</span></div>
          <div class="detail-metrics"><span>Floor 3M <strong>${fmt(m3.floor)}</strong></span><span>Timing <strong>${escapeHTML(m3TimingText(m3))}</strong></span><span>Confianza timing <strong>${m3.confidence == null ? '—' : `${fmt(m3.confidence * 100, 0)}%`}</strong></span></div>
          ${m3WeekBarsSvg(m3.top3)}
          ${m3.blockReason ? `<p class="table-subtext">${escapeHTML(m3.blockReason)}</p>` : ''}
        </article>`;
      }
      const ref = referencePrice(data, symbol, row);
      const range = forecastRange(row, ref.value);
      const skill = Number(row?.central_skill_vs_best_dummy);
      const skillLabel = Number.isFinite(skill)
        ? `${skill >= 0 ? '+' : ''}${(skill * 100).toFixed(1)}%`
        : '—';
      const risk = riskRange(row, ref.value);
      return `<article class="detail-horizon">
        <div class="detail-title"><strong>${escapeHTML(horizonLabel(key))}</strong><span class="code-label">${escapeHTML(key)}</span></div>
        ${geometryStack(symbol, key, row, ref.value)}
        <div class="detail-metrics"><span>Piso central <strong>${fmt(range.floor)}</strong></span><span>Techo central <strong>${fmt(range.ceiling)}</strong></span><span>Piso risk <strong>${risk.available ? fmt(risk.floor) : '—'}</strong></span><span>Techo risk <strong>${risk.available ? fmt(risk.ceiling) : '—'}</strong></span><span>Timing piso <strong>${escapeHTML(row.floor_time_bucket || '—')}</strong></span><span>Timing techo <strong>${escapeHTML(row.ceiling_time_bucket || '—')}</strong></span><span>Skill vs dummy <strong>${escapeHTML(skillLabel)}</strong></span></div>
      </article>`;
    }).join('');
    detail.innerHTML = `<section class="ticker-detail-card">
      <div class="section-heading"><div><span class="eyebrow">Detalle del activo</span><h2>${escapeHTML(symbol)}</h2></div><a class="text-link" href="tickers.html">Cerrar detalle</a></div>
      <div class="detail-grid">${blocks}</div>
      <details class="advanced-details"><summary>Métricas técnicas y payload</summary><pre>${safeJSON(rows)}</pre></details>
    </section>`;
  }

  function render() {
    const rows = sortRows(buildRows());
    if (count) count.textContent = `${rows.length} activos`;
    if (table) {
      table.innerHTML = rows.map((item) => `<tr>
        <td><a class="ticker-link compact" href="tickers.html?ticker=${encodeURIComponent(item.symbol)}">${escapeHTML(item.symbol)}</a></td>
        <td>${fmt(item.ref.value)}<div class="table-subtext">${escapeHTML(item.ref.source)}</div></td>
        <td><strong class="range-text">${compactRange(item.range)}</strong>${rangeDeltas(item.range)}<div class="table-subtext">Forecast central</div></td>
        <td><strong class="range-text">${item.risk.available ? compactRange(item.risk) : '—'}</strong>${item.risk.available ? rangeDeltas(item.risk) : ''}<div class="table-subtext">Risk envelope</div></td>
        <td>${riskCoverageChip(item.row)}</td>
      </tr>`).join('') || emptyRow(forecastsR.ok ? 'No hay activos que coincidan con los filtros.' : 'No se pudieron cargar los forecasts.', 5);
    }
    document.querySelectorAll('[data-sort]').forEach((button) => {
      const th = button.closest('th');
      if (th) th.setAttribute('aria-sort', currentSort.key === button.dataset.sort ? (currentSort.direction === 'asc' ? 'ascending' : 'descending') : 'none');
    });
  }

  document.querySelectorAll('[data-sort]').forEach((button) => {
    button.addEventListener('click', () => {
      const key = button.dataset.sort;
      if (currentSort.key === key) currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
      else currentSort = { key, direction: 'desc' };
      render();
    });
  });
  [search, horizon, confidenceFilter].forEach((control) => control?.addEventListener(control === search ? 'input' : 'change', render));
  render();
  if (route.ticker) renderDetail(route.ticker);
}

async function strategies() {
  const [result, leagueR, liveR] = await Promise.all([
    loadJSONState('data/strategy.json', { status: 'UNKNOWN', equity_curve: [] }),
    loadJSONState('data/strategy_league.json', { status: 'UNKNOWN', rows: [] }),
    loadJSONState('data/strategy_live.json', { status: 'UNKNOWN', rows: [] }),
  ]);
  const strategy = result.data || {};
  const league = leagueR.data || {};
  const live = liveR.data || {};
  const status = document.getElementById('strategyStatus');
  const metrics = document.getElementById('strategyMetrics');
  const curve = Array.isArray(strategy.equity_curve) ? strategy.equity_curve : [];
  if (status) {
    const historicalEnd = curve[curve.length - 1]?.session || '—';
    const officialEod = league?.last_session || '—';
    const liveUpdated = live?.generated_at ? fmtDateTime(live.generated_at) : '—';
    status.innerHTML = `<div class="trust-strip ${league?.status === 'RUNNING' ? 'ok' : 'warn'}">
      <div>${result.ok ? badge(strategy.status || 'UNKNOWN', 'Backtest histórico') : badge('UNKNOWN', 'Backtest no disponible')}<span class="trust-time">Histórico hasta ${escapeHTML(historicalEnd)}</span></div>
      <span class="trust-detail">EOD prospectivo ${escapeHTML(officialEod)} · Intradía ${escapeHTML(liveUpdated)}</span>
    </div>`;
  }

  const start = Number(curve[0]?.equity ?? curve[0]?.value);
  const end = Number(curve[curve.length - 1]?.equity ?? curve[curve.length - 1]?.value);
  const totalReturn = Number.isFinite(start) && Number.isFinite(end) && Math.abs(start) > 1e-9 ? ((end - start) / start) * 100 : null;
  const maxDrawdown = curve.reduce((min, point) => Math.min(min, Number(point?.drawdown ?? 0)), 0);
  if (metrics) {
    metrics.innerHTML = [
      metricCard('Retorno del backtest', totalReturn == null ? '—' : fmtPct(totalReturn), curve.length ? `${curve.length} observaciones` : 'Sin serie disponible', totalReturn != null && totalReturn < 0 ? 'bad' : 'neutral'),
      metricCard('Máx. drawdown', curve.length ? fmtPct(maxDrawdown * (Math.abs(maxDrawdown) <= 1 ? 100 : 1)) : '—', 'Peor caída registrada', maxDrawdown < -0.1 ? 'bad' : 'neutral'),
      metricCard('Estado', String(strategy.status || 'UNKNOWN'), result.ok ? 'Reporte cargado' : 'No fue posible cargar strategy.json', result.ok ? 'neutral' : 'warn'),
    ].join('');
  }

  const equity = document.getElementById('equityCurve');
  const drawdown = document.getElementById('drawdownCurve');
  const windowControl = document.getElementById('strategyHistoryWindow');
  const windowMetrics = document.getElementById('strategyChartMetrics');
  const hint = document.getElementById('strategyHint');

  const chartMoney = (value) => Number.isFinite(Number(value))
    ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(Number(value))
    : '—';
  const chartKpi = (label, value) => `<span class="chart-kpi"><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong></span>`;

  function renderWindow() {
    const windowKey = String(windowControl?.value || '3m');
    const filtered = filterPointsByWindow(curve, windowKey);
    const windowStart = Number(filtered[0]?.equity ?? filtered[0]?.value);
    const windowEnd = Number(filtered[filtered.length - 1]?.equity ?? filtered[filtered.length - 1]?.value);
    const windowReturn = Number.isFinite(windowStart) && Number.isFinite(windowEnd) && Math.abs(windowStart) > 1e-9
      ? (windowEnd / windowStart - 1.0)
      : null;
    const windowMaxDrawdown = filtered.reduce(
      (worst, point) => Math.min(worst, Number(point?.drawdown ?? 0)),
      0,
    );
    const firstSession = filtered[0]?.session || '—';
    const lastSession = filtered[filtered.length - 1]?.session || '—';

    if (windowMetrics) {
      windowMetrics.innerHTML = [
        chartKpi('Periodo', filtered.length ? `${firstSession} → ${lastSession}` : '—'),
        chartKpi('Retorno', windowReturn == null ? '—' : `${windowReturn >= 0 ? '+' : ''}${fmtPct(windowReturn * 100)}`),
        chartKpi('NAV final', chartMoney(windowEnd)),
        chartKpi('Máx. DD', filtered.length ? fmtPct(windowMaxDrawdown * 100) : '—'),
        chartKpi('Sesiones', String(filtered.length || 0)),
      ].join('');
    }

    if (equity) {
      equity.innerHTML = lineSvg(
        filtered.map((point) => ({ session: point.session, value: point.equity ?? point.value })),
        {
          title: 'Curva de equity',
          valueFormat: 'money',
          baseline: Number.isFinite(windowStart) ? windowStart : undefined,
          baselineLabel: 'Inicio ventana',
          annotateEnd: true,
        },
      );
    }
    if (drawdown) {
      drawdown.innerHTML = lineSvg(
        filtered.map((point) => ({ session: point.session, value: point.drawdown })),
        {
          title: 'Drawdown',
          valueFormat: 'percent',
          baseline: 0,
          baselineLabel: '0%',
          thresholds: [{ value: -0.15, label: 'Límite revisión −15%' }],
          annotateExtrema: true,
          minLabel: filtered.length ? `Máx DD ${fmtPct(windowMaxDrawdown * 100)}` : 'Máx DD —',
          annotateEnd: false,
        },
      );
    }
    if (hint) {
      hint.textContent = filtered.length
        ? `Ventana visible: ${filtered.length} sesiones. Resultados históricos del reporte de estrategia; no representan rendimiento futuro.`
        : 'Aún no hay una curva de backtest publicable.';
    }
  }

  if (windowControl) windowControl.onchange = renderWindow;
  renderWindow();
}

function flattenNumericMetrics(value, prefix = '') {
  if (!value || typeof value !== 'object') return [];
  const rows = [];
  Object.entries(value).forEach(([key, item]) => {
    const label = prefix ? `${prefix}.${key}` : key;
    const numeric = Number(item);
    if (item !== null && item !== '' && Number.isFinite(numeric)) rows.push([label, numeric]);
    else if (item && typeof item === 'object' && !Array.isArray(item)) rows.push(...flattenNumericMetrics(item, label));
  });
  return rows;
}

function publicModelMetricSet(detail) {
  const monitoring = flattenNumericMetrics(detail?.monitoring_metrics || detail?.metrics?.current || {});
  if (monitoring.length) return { source: 'Monitoring actual', rows: monitoring.slice(0, 4) };
  const validation = flattenNumericMetrics(detail?.validation_metrics || {});
  if (validation.length) return { source: 'Validación del champion', rows: validation.slice(0, 4) };
  return { source: 'Sin métricas públicas', rows: [] };
}

function modelGovernanceRows(detail) {
  const serving = detail?.serving || {};
  const selection = detail?.selection || {};
  const monitoring = detail?.monitoring || {};
  const rows = [];

  rows.push(`<div><span>Serving</span><strong>${serving.active ? badge('OK', 'Champion activo') : badge(serving.status || 'UNKNOWN')}</strong></div>`);

  if (selection.has_evidence) {
    const skill = Number(selection.skill_vs_atr);
    const label = selection.status === 'MODEL_SUPERIOR'
      ? 'Gate ATR validado'
      : selection.status === 'ATR_ONLY_SUPERIOR'
        ? 'ATR-only superior'
        : 'Gate ATR';
    const tone = selection.status === 'MODEL_SUPERIOR' ? 'OK' : selection.status === 'ATR_ONLY_SUPERIOR' ? 'WARN' : 'UNKNOWN';
    const suffix = Number.isFinite(skill) ? ` · ${fmtRatioPct(skill)}` : '';
    rows.push(`<div><span>Selección</span><strong>${badge(tone, label)}<small>${escapeHTML(suffix)}</small></strong></div>`);
  } else {
    rows.push(`<div><span>Selección</span><strong>${badge('NOT_APPLICABLE', 'Sin gate ATR publicado')}</strong></div>`);
  }

  if (monitoring.covered) {
    rows.push(`<div><span>Monitoring</span><strong>${badge(monitoring.status || 'UNKNOWN')} ${monitoring.drift_level ? badge(monitoring.drift_level, `Drift ${monitoring.drift_level}`) : ''}</strong><small>${escapeHTML(monitoring.recommendation || 'Sin acción')}</small></div>`);
  } else {
    rows.push(`<div><span>Monitoring</span><strong>${badge('NOT_COVERED', 'No cubierto por retraining review')}</strong><small>Esto no invalida el champion ni su gate de selección.</small></div>`);
  }

  return rows.join('');
}

function modelCards(models) {
  return Object.values(models?.details || {}).map((detail) => {
    const metricSet = publicModelMetricSet(detail);
    const serving = detail?.serving || {};
    return `<article class="model-card">
      <div class="card-head"><div><span class="eyebrow">${escapeHTML(detail.model_key || 'Modelo')}</span><h3>${escapeHTML(detail.model_name || 'Sin nombre')}</h3></div>${serving.active ? badge('OK', 'Champion activo') : badge(serving.status || detail.status || 'UNKNOWN')}</div>
      <div class="model-version">Versión ${escapeHTML(detail.current_version || '—')} · <span class="muted">${escapeHTML(metricSet.source)}</span></div>
      <div class="model-metrics">${metricSet.rows.length ? metricSet.rows.map(([key, value]) => `<div><span>${escapeHTML(key)}</span><strong>${fmt(value, 3)}</strong></div>`).join('') : '<span class="muted">Sin métricas públicas disponibles.</span>'}</div>
      <div class="model-governance">${modelGovernanceRows(detail)}</div>
      <details class="advanced-details"><summary>Detalles técnicos</summary><pre>${safeJSON(detail.artifact?.params || {})}</pre><p>${escapeHTML(detail.reason || '')}</p></details>
    </article>`;
  }).join('');
}

async function models() {
  const models = await loadJSON('data/models.json', { details: {}, timeline: [], suite_status: 'UNKNOWN', suite_recommendation: 'PENDING' });
  const champion = document.getElementById('champion');
  if (champion) champion.textContent = models.champion || 'No disponible';
  const suite = document.getElementById('suiteStatus');
  if (suite) {
    const serving = models.serving || {};
    const review = models.suite_review || {};
    const schedule = models.retraining_schedule || {};
    const servingBadge = serving.status === 'ACTIVE'
      ? badge('OK', `Serving activo · ${Number(serving.active_champion_count || 0)} champions`)
      : badge(serving.status || 'UNKNOWN', 'Serving no confirmado');
    let reviewBadge = '';
    let reviewAction = '';
    let scheduleText = '';
    if (review.covered) {
      reviewBadge = badge(review.status || 'UNKNOWN', `Review ${review.status || 'UNKNOWN'}`);
      reviewAction = review.recommendation
        ? badge(review.recommendation, review.recommendation)
        : '';
      scheduleText = schedule.human_eta
        ? `<div class="small" style="margin-top:8px">${escapeHTML(schedule.human_eta)}</div>`
        : '';
    } else {
      reviewBadge = badge(
        review.status === 'STALE' ? 'STALE' : 'NOT_COVERED',
        review.status === 'STALE'
          ? 'Review de retraining desactualizado'
          : 'Review de retraining no disponible',
      );
    }
    suite.innerHTML = `${servingBadge} ${reviewBadge} ${reviewAction}${scheduleText}`;
  }
  const cards = document.getElementById('modelCards');
  if (cards) cards.innerHTML = modelCards(models) || emptyState('Sin modelos publicables');

  const centralSkill = document.getElementById('centralSkillTable');
  if (centralSkill) {
    centralSkill.innerHTML = ['d1', 'w1'].map((horizon) => {
      const detail = models?.details?.[horizon] || {};
      const benchmark = detail?.central_benchmark || {};
      const stability = benchmark?.temporal_stability || {};
      const modelMae = Number(benchmark?.model_mae_spread_pct);
      const atrMae = Number(benchmark?.atr_only_mae_spread_pct);
      const skill = Number(benchmark?.skill_vs_atr);
      const floorSkill = Number(benchmark?.floor_skill_vs_atr);
      const ceilingSkill = Number(benchmark?.ceiling_skill_vs_atr);
      const periods = Number(stability?.periods);
      const wins = Number(stability?.periods_won_vs_atr);
      const recent = Number(stability?.recent_period_skill_vs_atr);
      const worst = Number(stability?.worst_period_skill_vs_atr);
      const modelLabel = Number.isFinite(modelMae) ? fmtRatioPct(modelMae) : '—';
      const atrLabel = Number.isFinite(atrMae) ? fmtRatioPct(atrMae) : '—';
      const skillLabel = Number.isFinite(skill) ? fmtRatioPct(skill) : '—';
      const boundaryText = Number.isFinite(floorSkill) && Number.isFinite(ceilingSkill)
        ? `Piso ${fmtRatioPct(floorSkill)} · Techo ${fmtRatioPct(ceilingSkill)}`
        : 'Fronteras no disponibles';
      const stabilityText = Number.isFinite(periods) && periods > 0
        ? `${Number.isFinite(wins) ? wins : 0}/${periods} periodos · reciente ${Number.isFinite(recent) ? fmtRatioPct(recent) : '—'} · peor ${Number.isFinite(worst) ? fmtRatioPct(worst) : '—'}`
        : 'Estabilidad temporal no disponible';
      const verdict = Number.isFinite(skill) && skill > 0
        ? badge('OK', 'Model > ATR')
        : Number.isFinite(skill)
          ? badge('WARN', 'ATR-only sigue siendo superior')
          : badge('UNKNOWN', 'Sin evidencia');
      return `<tr>
        <td><strong>${escapeHTML(horizonLabel(horizon))}</strong><div class="small">${escapeHTML(horizon.toUpperCase())}</div></td>
        <td>${escapeHTML(modelLabel)}<div class="small">${escapeHTML(detail.model_name || '—')}</div></td>
        <td>${escapeHTML(atrLabel)}</td>
        <td><strong class="${Number.isFinite(skill) && skill < 0 ? 'negative' : 'positive'}">${escapeHTML(skillLabel)}</strong><div style="margin-top:6px">${verdict}</div></td>
        <td>${escapeHTML(boundaryText)}</td>
        <td>${escapeHTML(stabilityText)}</td>
      </tr>`;
    }).join('');
  }

  const timeline = document.getElementById('timeline');
  if (timeline) timeline.innerHTML = (models.timeline || []).map((x) => `<tr><td>${escapeHTML(x.as_of || '—')}</td><td>${escapeHTML(x.model_name || '—')}</td><td>${escapeHTML(x.action || '—')}</td><td>${badge(x.drift_level || 'UNKNOWN')}</td></tr>`).join('') || emptyRow('Sin eventos de modelos.', 4);
}

async function drift() {
  const result = await loadJSONState('data/drift.json', { drift_level: 'UNKNOWN', decision: 'PENDING', thresholds: [] });
  const d = result.data || {};
  const light = document.getElementById('driftLight');
  if (light) light.innerHTML = badge(result.ok ? (d.drift_level || 'UNKNOWN') : 'UNKNOWN');
  const decision = document.getElementById('decision');
  if (decision) decision.textContent = result.ok ? (d.decision || 'PENDING') : 'No disponible';
  const thresholds = document.getElementById('thresholds');
  if (thresholds) thresholds.innerHTML = (d.thresholds || []).map((t) => `<tr><td>${escapeHTML(t.name || '—')}</td><td>${escapeHTML(t.observed ?? '—')}</td><td>${escapeHTML(t.threshold ?? '—')}</td><td>${badge(t.severity || 'UNKNOWN')}</td></tr>`).join('') || emptyRow('Sin umbrales reportados.', 4);
}

async function incidents() {
  const result = await loadJSONState('data/incidents.json', { status: 'UNKNOWN', severity: 'UNKNOWN', summary: {}, impact: {} });
  const i = result.data || {};
  const status = document.getElementById('status');
  if (status) status.innerHTML = result.ok ? `${badge(i.status || 'UNKNOWN')} ${badge(i.severity || 'UNKNOWN')}` : badge('UNKNOWN', 'Estado no disponible');
  const symptom = document.getElementById('symptom');
  if (symptom) symptom.textContent = result.ok ? (i.summary?.symptom || 'Sin síntoma reportado') : 'No fue posible cargar el reporte de incidentes.';
  const impact = document.getElementById('impact');
  if (impact) impact.innerHTML = Object.entries(i.impact || {}).map(([key, value]) => `<tr><td>${escapeHTML(key)}</td><td>${escapeHTML(value)}</td></tr>`).join('') || emptyRow('Sin impacto reportado.', 2);
}

async function system() {
  const [dashboardR, driftR, incidentsR, modelsR, auditR] = await Promise.all([
    loadJSONState('data/dashboard.json', {}),
    loadJSONState('data/drift.json', {}),
    loadJSONState('data/incidents.json', {}),
    loadJSONState('data/models.json', {}),
    loadJSONState('data/audit.json', {}),
  ]);
  const dashboard = dashboardR.data || {};
  const driftData = driftR.data || {};
  const incidentData = incidentsR.data || {};
  const modelsData = modelsR.data || {};
  const audit = auditR.data || {};
  const pub = publicationState(audit);

  const overview = document.getElementById('systemOverview');
  if (overview) overview.innerHTML = [
    metricCard('Estado general', dashboardR.ok ? String(dashboard.system_health || 'UNKNOWN') : 'UNKNOWN', dashboardR.ok ? 'Pipeline / dashboard' : 'No se pudo cargar dashboard.json', stateTone(dashboard.system_health)),
    metricCard('Publicación', pub.label, freshnessText(audit), pub.tone),
    metricCard('Modelos', String(modelsData.suite_status || 'UNKNOWN'), String(modelsData.suite_recommendation || 'Sin recomendación'), stateTone(modelsData.suite_status)),
    metricCard('Incidentes', incidentsR.ok ? String(incidentData.status || 'UNKNOWN') : 'UNKNOWN', incidentsR.ok ? String(incidentData.severity || '') : 'Reporte no disponible', stateTone(incidentData.status)),
  ].join('');

  const components = document.getElementById('systemComponents');
  if (components) components.innerHTML = [
    healthRow('Datos y publicación', pub.status, auditR.ok ? freshnessText(audit) : 'audit.json no disponible'),
    healthRow('Modelos', modelsR.ok ? (modelsData.suite_status || 'UNKNOWN') : 'UNKNOWN', modelsData.suite_recommendation || ''),
    healthRow('Drift', driftR.ok ? (driftData.drift_level || 'UNKNOWN') : 'UNKNOWN', driftData.decision || ''),
    healthRow('Incidentes', incidentsR.ok ? (incidentData.status || 'UNKNOWN') : 'UNKNOWN', incidentData.severity || ''),
    healthRow('Pipeline', dashboardR.ok ? (dashboard.system_health || 'UNKNOWN') : 'UNKNOWN'),
  ].join('');

  const models = document.getElementById('systemModels');
  if (models) models.innerHTML = modelCards(modelsData) || emptyState('Sin detalles de modelos');

  const drift = document.getElementById('systemDrift');
  if (drift) {
    drift.innerHTML = `<div class="system-panel-head">${badge(driftR.ok ? (driftData.drift_level || 'UNKNOWN') : 'UNKNOWN')}<strong>${escapeHTML(driftData.decision || 'Sin decisión disponible')}</strong></div>
      <div class="compact-list">${(driftData.thresholds || []).slice(0, 8).map((t) => `<div><span>${escapeHTML(t.name || 'Umbral')}</span><strong>${escapeHTML(t.observed ?? '—')} / ${escapeHTML(t.threshold ?? '—')}</strong>${badge(t.severity || 'UNKNOWN')}</div>`).join('') || '<span class="muted">Sin umbrales disparados.</span>'}</div>`;
  }

  const incidents = document.getElementById('systemIncidents');
  if (incidents) {
    incidents.innerHTML = `<div class="system-panel-head">${incidentsR.ok ? `${badge(incidentData.status || 'UNKNOWN')} ${badge(incidentData.severity || 'UNKNOWN')}` : badge('UNKNOWN')}</div>
      <p>${escapeHTML(incidentsR.ok ? (incidentData.summary?.symptom || 'Sin síntoma reportado.') : 'Reporte de incidentes no disponible.')}</p>`;
  }

  const auditRoot = document.getElementById('systemAudit');
  if (auditRoot) {
    const blockers = Array.isArray(audit.blockers) ? audit.blockers : [];
    const warnings = Array.isArray(audit.warnings) ? audit.warnings : [];
    auditRoot.innerHTML = `<div class="system-panel-head">${badge(pub.status, pub.label)}<span>${escapeHTML(freshnessText(audit))}</span></div>
      <dl class="audit-grid">
        <div><dt>Batch</dt><dd>${escapeHTML(audit?.batch?.as_of || '—')}</dd></div>
        <div><dt>Filas</dt><dd>${escapeHTML(audit?.batch?.observed_rows ?? '—')} / ${escapeHTML(audit?.batch?.expected_rows ?? '—')}</dd></div>
        <div><dt>Commit</dt><dd><code>${escapeHTML(audit.source_commit || '—')}</code></dd></div>
        <div><dt>Forecasts</dt><dd>${audit.publishable_forecasts ? 'Publicables' : 'Suprimidos'}</dd></div>
      </dl>
      ${blockers.length ? `<div class="callout bad"><strong>Bloqueos</strong><ul>${blockers.map((x) => `<li>${escapeHTML(x)}</li>`).join('')}</ul></div>` : ''}
      ${warnings.length ? `<div class="callout warn"><strong>Advertencias</strong><ul>${warnings.map((x) => `<li>${escapeHTML(x)}</li>`).join('')}</ul></div>` : ''}`;
  }
}

const page = document.body.dataset.page;
setNav(page);
initNavigation();
const pageHandler = ({ home, forecasts, tickers, strategies, models, drift, incidents, system }[page] || (() => {}));
pageHandler();
if (page === 'home') setInterval(home, 300_000);
if (page === 'strategies') setInterval(strategies, 300_000);
