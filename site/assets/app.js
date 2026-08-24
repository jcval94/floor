import {
  badge,
  bySymbol,
  confidenceLabel,
  escapeHTML,
  fmt,
  fmtDateTime,
  fmtPct,
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
import { lineSvg, m3WeekBarsSvg, rangeSvg } from './charts.js';
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

function confidenceFromForecast(row) {
  const breach = Number(row?.breach_probability ?? row?.breach_prob);
  if (Number.isFinite(breach) && breach >= 0 && breach <= 1) return 1 - breach;
  const explicit = Number(row?.confidence_score ?? row?.confidence);
  if (Number.isFinite(explicit) && explicit >= 0 && explicit <= 1) return explicit;
  return null;
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
  const intraday = Number(data?.latest_intraday?.[symbol]?.price);
  if (Number.isFinite(intraday)) return { value: intraday, source: 'Intraday' };
  const close = Number(data?.latest_close?.[symbol]?.close);
  if (Number.isFinite(close)) return { value: close, source: 'Último close' };
  const floor = Number(row?.floor_value);
  const ceiling = Number(row?.ceiling_value);
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
  const confidence = confidenceFromForecast(row);
  const m3 = extractM3(rowsForSymbol);
  const horizon = String(row?.horizon || '').toLowerCase();
  const timingFloor = row?.floor_time_bucket || '—';
  const timingCeiling = row?.ceiling_time_bucket || '—';
  return `<article class="forecast-card">
    <div class="card-head">
      <div>
        <a class="ticker-link" href="tickers.html?ticker=${encodeURIComponent(symbol)}">${escapeHTML(symbol)}</a>
        <div class="eyebrow">${escapeHTML(horizonLabel(horizon))} <span class="code-label">${escapeHTML(horizonCode(horizon))}</span></div>
      </div>
      ${confidenceChip(confidence)}
    </div>
    <div class="forecast-price-row">
      <div><span class="metric-label">Referencia</span><strong>${fmt(ref.value)}</strong><small>${escapeHTML(ref.source)}</small></div>
      <div><span class="metric-label">Piso</span><strong>${fmt(range.floor)}</strong><small class="negative">${range.downside == null ? '—' : fmtPct(range.downside)}</small></div>
      <div><span class="metric-label">Techo</span><strong>${fmt(range.ceiling)}</strong><small class="positive">${range.upside == null ? '—' : fmtPct(range.upside)}</small></div>
    </div>
    ${rangeSvg(range.floor, ref.value, range.ceiling, `${symbol} ${horizonLabel(horizon)}`)}
    <div class="forecast-meta-grid">
      <div><span>Piso esperado</span><strong>${escapeHTML(String(timingFloor))}</strong></div>
      <div><span>Techo esperado</span><strong>${escapeHTML(String(timingCeiling))}</strong></div>
      <div><span>3M downside</span><strong>${fmt(m3.floor)}</strong></div>
      <div><span>Timing 3M</span><strong>${escapeHTML(m3TimingText(m3))}</strong></div>
    </div>
    <div class="card-actions"><a href="tickers.html?ticker=${encodeURIComponent(symbol)}">Ver detalle</a></div>
  </article>`;
}

async function home() {
  const [dashboardR, driftR, incidentsR, forecastsR, auditR, modelsR] = await Promise.all([
    loadJSONState('data/dashboard.json', {}),
    loadJSONState('data/drift.json', {}),
    loadJSONState('data/incidents.json', {}),
    loadJSONState('data/forecasts.json', { rows: [] }),
    loadJSONState('data/audit.json', {}),
    loadJSONState('data/models.json', {}),
  ]);
  const dashboard = dashboardR.data || {};
  const drift = driftR.data || {};
  const incidents = incidentsR.data || {};
  const forecasts = forecastsR.data || { rows: [] };
  const audit = auditR.data || {};
  const models = modelsR.data || {};
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
      const confidence = confidenceFromForecast(row);
      return `<tr>
        <td><a class="ticker-link compact" href="tickers.html?ticker=${encodeURIComponent(symbol)}">${escapeHTML(symbol)}</a></td>
        <td>${fmt(ref.value)}</td>
        <td>${escapeHTML(horizonLabel(row.horizon))}</td>
        <td><span class="range-text">${fmt(range.floor)} <span aria-hidden="true">→</span> ${fmt(range.ceiling)}</span></td>
        <td class="negative">${range.downside == null ? '—' : fmtPct(range.downside)}</td>
        <td class="positive">${range.upside == null ? '—' : fmtPct(range.upside)}</td>
        <td>${confidenceChip(confidence)}</td>
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
  const root = document.getElementById('forecastCards');
  const summary = document.getElementById('forecastSummary');
  const tableRoot = document.getElementById('forecastTable');
  const m3Root = document.getElementById('m3WatchTable');

  function render() {
    const query = String(search?.value || '').trim().toUpperCase();
    const selectedHorizon = String(horizon?.value || 'w1').toLowerCase();
    const minConfidence = Number(confidence?.value || 0);
    const items = Object.entries(grouped).map(([symbol, rows]) => {
      const row = selectForecast(rows, selectedHorizon);
      if (!row) return null;
      const conf = confidenceFromForecast(row);
      if (query && !symbol.toUpperCase().includes(query)) return null;
      if (minConfidence > 0 && (!Number.isFinite(conf) || conf < minConfidence)) return null;
      return { symbol, rows, row, conf };
    }).filter(Boolean).sort((a, b) => {
      const ca = Number.isFinite(a.conf) ? a.conf : -1;
      const cb = Number.isFinite(b.conf) ? b.conf : -1;
      return cb - ca || a.symbol.localeCompare(b.symbol);
    });

    if (summary) {
      summary.innerHTML = `<strong>${items.length}</strong> activos · ${escapeHTML(horizonLabel(selectedHorizon))} · ordenados por confianza del intervalo`;
    }
    if (root) {
      root.innerHTML = items.length
        ? items.slice(0, 12).map(({ symbol, row, rows }) => renderForecastCard(symbol, row, data, rows)).join('')
        : emptyState(dataResult.ok ? 'Sin resultados' : 'No se pudieron cargar los forecasts', dataResult.ok ? 'Prueba con otros filtros.' : dataResult.error);
    }
    if (tableRoot) {
      tableRoot.innerHTML = items.map(({ symbol, row, conf }) => {
        const ref = referencePrice(data, symbol, row);
        const range = forecastRange(row, ref.value);
        return `<tr>
          <td><a class="ticker-link compact" href="tickers.html?ticker=${encodeURIComponent(symbol)}">${escapeHTML(symbol)}</a></td>
          <td>${fmt(ref.value)}</td>
          <td>${fmt(range.floor)}</td>
          <td class="negative">${range.downside == null ? '—' : fmtPct(range.downside)}</td>
          <td>${fmt(range.ceiling)}</td>
          <td class="positive">${range.upside == null ? '—' : fmtPct(range.upside)}</td>
          <td>${confidenceChip(conf)}</td>
        </tr>`;
      }).join('') || emptyRow('No hay datos que coincidan con los filtros.', 7);
    }
  }

  [search, horizon, confidence].forEach((control) => {
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
      const conf = confidenceFromForecast(row);
      if (query && !String(symbol).toUpperCase().includes(query)) return null;
      if (minConfidence > 0 && (!Number.isFinite(conf) || conf < minConfidence)) return null;
      return { symbol, row, rows, ref, range, confidence: conf };
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
      return `<article class="detail-horizon">
        <div class="detail-title"><strong>${escapeHTML(horizonLabel(key))}</strong><span class="code-label">${escapeHTML(key)}</span>${confidenceChip(confidenceFromForecast(row))}</div>
        ${rangeSvg(range.floor, ref.value, range.ceiling, `${symbol} ${horizonLabel(key)}`)}
        <div class="detail-metrics"><span>Piso <strong>${fmt(range.floor)}</strong></span><span>Techo <strong>${fmt(range.ceiling)}</strong></span><span>Timing piso <strong>${escapeHTML(row.floor_time_bucket || '—')}</strong></span><span>Timing techo <strong>${escapeHTML(row.ceiling_time_bucket || '—')}</strong></span></div>
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
        <td><span class="range-text">${fmt(item.range.floor)} → ${fmt(item.range.ceiling)}</span></td>
        <td class="negative">${item.range.downside == null ? '—' : fmtPct(item.range.downside)}</td>
        <td class="positive">${item.range.upside == null ? '—' : fmtPct(item.range.upside)}</td>
        <td>${confidenceChip(item.confidence)}</td>
      </tr>`).join('') || emptyRow(forecastsR.ok ? 'No hay activos que coincidan con los filtros.' : 'No se pudieron cargar los forecasts.', 6);
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
  const result = await loadJSONState('data/strategy.json', { status: 'UNKNOWN', equity_curve: [] });
  const strategy = result.data || {};
  const status = document.getElementById('strategyStatus');
  const metrics = document.getElementById('strategyMetrics');
  const curve = Array.isArray(strategy.equity_curve) ? strategy.equity_curve : [];
  if (status) status.innerHTML = result.ok ? badge(strategy.status || 'UNKNOWN') : badge('UNKNOWN', 'Reporte no disponible');

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
  if (equity) equity.innerHTML = lineSvg(curve.map((x) => ({ value: x.equity ?? x.value })), { title: 'Curva de equity' });
  if (drawdown) drawdown.innerHTML = lineSvg(curve.map((x) => ({ value: x.drawdown })), { title: 'Drawdown' });
  const hint = document.getElementById('strategyHint');
  if (hint) hint.textContent = curve.length ? 'Resultados históricos del reporte de estrategia. No representan rendimiento futuro.' : 'Aún no hay una curva de backtest publicable.';
}

function modelCards(models) {
  return Object.values(models?.details || {}).map((detail) => {
    const current = detail?.metrics?.current || {};
    const metrics = Object.entries(current).slice(0, 4);
    return `<article class="model-card">
      <div class="card-head"><div><span class="eyebrow">${escapeHTML(detail.model_key || 'Modelo')}</span><h3>${escapeHTML(detail.model_name || 'Sin nombre')}</h3></div>${badge(detail.status || 'UNKNOWN')}</div>
      <div class="model-version">Versión ${escapeHTML(detail.current_version || '—')}</div>
      <div class="model-metrics">${metrics.length ? metrics.map(([key, value]) => `<div><span>${escapeHTML(key)}</span><strong>${fmt(value, 3)}</strong></div>`).join('') : '<span class="muted">Sin métricas públicas actuales.</span>'}</div>
      <div class="model-footer"><span>Drift ${badge(detail.drift_level || 'UNKNOWN')}</span><span>${escapeHTML(detail.recommendation || 'Sin recomendación')}</span></div>
      <details class="advanced-details"><summary>Detalles técnicos</summary><pre>${safeJSON(detail.artifact?.params || {})}</pre><p>${escapeHTML(detail.reason || '')}</p></details>
    </article>`;
  }).join('');
}

async function models() {
  const models = await loadJSON('data/models.json', { details: {}, timeline: [], suite_status: 'UNKNOWN', suite_recommendation: 'PENDING' });
  const champion = document.getElementById('champion');
  if (champion) champion.textContent = models.champion || 'No disponible';
  const suite = document.getElementById('suiteStatus');
  if (suite) suite.innerHTML = `${badge(models.suite_status || 'UNKNOWN')} ${badge(models.suite_recommendation || 'PENDING')}`;
  const cards = document.getElementById('modelCards');
  if (cards) cards.innerHTML = modelCards(models) || emptyState('Sin modelos publicables');
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
({ home, forecasts, tickers, strategies, models, drift, incidents, system }[page] || (() => {}))();
