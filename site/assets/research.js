import { escapeHTML, fmtPct, loadJSONState } from './utils.js';
import { filterPointsByWindow, multiLineSvg } from './charts.js';

const LABELS = {
  capital_allocation_challenger: 'Capital Allocation Challenger',
  weekly_opportunity_ridge: 'Weekly Opportunity',
  breakout_protected_by_floor: 'Momentum + Floor',
  mean_reversion_floor_w1: 'Mean Reversion + Floor',
  cross_horizon_asymmetry: 'Cross-Horizon Asymmetry',
  benchmark_spy: 'SPY',
  benchmark_equal_weight: 'Equal Weight',
};

const SHORT_LABELS = {
  capital_allocation_challenger: 'Challenger',
  weekly_opportunity_ridge: 'Weekly',
  breakout_protected_by_floor: 'Momentum',
  mean_reversion_floor_w1: 'Mean Reversion',
  cross_horizon_asymmetry: 'Cross-Horizon',
  benchmark_spy: 'SPY',
  benchmark_equal_weight: 'Equal Weight',
};

const SERIES_ORDER = [
  'capital_allocation_challenger',
  'weekly_opportunity_ridge',
  'breakout_protected_by_floor',
  'mean_reversion_floor_w1',
  'cross_horizon_asymmetry',
  'benchmark_spy',
  'benchmark_equal_weight',
];
const SERIES_RANK = new Map(SERIES_ORDER.map((id, index) => [id, index]));

function label(id) {
  return LABELS[id] || id || '—';
}

function shortLabel(id) {
  return SHORT_LABELS[id] || label(id);
}

function pct(value) {
  if (value === null || value === undefined || value === '') return '—';
  const numeric = Number(value);
  return Number.isFinite(numeric) ? fmtPct(numeric * 100) : '—';
}

function signedPct(value) {
  if (value === null || value === undefined || value === '') return '—';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  const sign = numeric > 0 ? '+' : numeric < 0 ? '−' : '';
  return `${sign}${fmtPct(Math.abs(numeric) * 100)}`;
}

function money(value, digits = 2) {
  if (value === null || value === undefined || value === '') return '—';
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(numeric)
    : '—';
}

function number(value, digits = 2) {
  if (value === null || value === undefined || value === '') return '—';
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : '—';
}

function metric(labelText, value, detail, tone = '') {
  return `<article class="metric-card league-metric ${tone}">
    <span class="metric-label">${escapeHTML(labelText)}</span>
    <strong class="metric-value">${escapeHTML(value)}</strong>
    <span class="metric-detail">${escapeHTML(detail)}</span>
  </article>`;
}

function oosStats(row, windowKey) {
  const points = filterPointsByWindow(
    Array.isArray(row?.equity_curve) ? row.equity_curve : [],
    windowKey,
  );
  if (!points.length) return { points, return: null, maxDrawdown: null, end: null };
  const start = Number(points[0]?.nav);
  const end = Number(points[points.length - 1]?.nav);
  const ret = Number.isFinite(start) && Number.isFinite(end) && Math.abs(start) > 1e-9
    ? end / start - 1.0
    : null;
  let peak = Number.NEGATIVE_INFINITY;
  let maxDrawdown = 0;
  points.forEach((point) => {
    const nav = Number(point?.nav);
    if (!Number.isFinite(nav)) return;
    peak = Math.max(peak, nav);
    if (peak > 0) maxDrawdown = Math.min(maxDrawdown, nav / peak - 1.0);
  });
  return { points, return: ret, maxDrawdown, end };
}

function oosKpi(labelText, value) {
  return `<span class="chart-kpi"><span>${escapeHTML(labelText)}</span><strong>${escapeHTML(value)}</strong></span>`;
}

function oosChart(rows, data, windowKey) {
  const ranked = [...rows]
    .filter((row) => !String(row.strategy || '').startsWith('benchmark_'))
    .map((row) => ({ row, stats: oosStats(row, windowKey) }))
    .filter((item) => Number.isFinite(item.stats.return))
    .sort((a, b) => Number(b.stats.return) - Number(a.stats.return));
  const leader = ranked[0]?.row?.strategy || null;
  const series = [...rows]
    .filter((row) => Array.isArray(row.equity_curve) && row.equity_curve.length)
    .sort((a, b) => (SERIES_RANK.get(a.strategy) ?? 99) - (SERIES_RANK.get(b.strategy) ?? 99))
    .map((row) => ({
      id: row.strategy,
      label: label(row.strategy),
      shortLabel: shortLabel(row.strategy),
      points: filterPointsByWindow(row.equity_curve, windowKey)
        .map((point) => ({ session: point.session, value: point.nav })),
    }))
    .filter((entry) => entry.points.length);
  const visibleSessions = new Set(series.flatMap((entry) => entry.points.map((point) => String(point.session))));
  const markers = (Array.isArray(data?.fold_reports) ? data.fold_reports : [])
    .filter((fold) => visibleSessions.has(String(fold?.start_session || '')))
    .map((fold) => ({
      session: String(fold.start_session),
      text: `F${fold.fold}`,
    }));
  return multiLineSvg(series, {
    title: 'Walk-forward histórico con modelos entrenados antes de cada fold',
    valueFormat: 'money',
    baseline: Number(data?.initial_nav_usd),
    baselineLabel: 'Capital inicial',
    markers,
    endLabelIds: ['capital_allocation_challenger', 'benchmark_spy', leader].filter(Boolean),
  });
}

function oosWindowMetrics(rows, data, windowKey) {
  const challenger = rows.find((row) => row.strategy === 'capital_allocation_challenger');
  const spy = rows.find((row) => row.strategy === 'benchmark_spy');
  const challengerStats = oosStats(challenger, windowKey);
  const spyStats = oosStats(spy, windowKey);
  const points = challengerStats.points;
  const first = points[0]?.session || '—';
  const last = points[points.length - 1]?.session || '—';
  const vsSpy = Number.isFinite(challengerStats.return) && Number.isFinite(spyStats.return)
    ? challengerStats.return - spyStats.return
    : null;
  const visible = new Set(points.map((point) => String(point.session)));
  const visibleFolds = (Array.isArray(data?.fold_reports) ? data.fold_reports : [])
    .filter((fold) => visible.has(String(fold?.start_session || ''))).length;
  return [
    oosKpi('Periodo', points.length ? `${first} → ${last}` : '—'),
    oosKpi('Challenger', signedPct(challengerStats.return)),
    oosKpi('vs SPY', signedPct(vsSpy)),
    oosKpi('Máx. DD', challengerStats.maxDrawdown == null ? '—' : pct(challengerStats.maxDrawdown)),
    oosKpi('Folds visibles', String(visibleFolds)),
  ].join('');
}

async function renderOOS() {
  const statusRoot = document.getElementById('oosStatus');
  if (!statusRoot) return;
  const result = await loadJSONState('data/walk_forward_oos.json', { status: 'WAITING', rows: [] });
  const data = result.data || { status: 'WAITING', rows: [] };
  const rows = Array.isArray(data.rows) ? data.rows : [];
  const challenger = rows.find((row) => row.strategy === 'capital_allocation_challenger');
  const spy = rows.find((row) => row.strategy === 'benchmark_spy');
  const running = data.status === 'MODEL_OOS_OK';
  const recalculating = data.status === 'WAITING_FOR_CONTINUOUS_RECALCULATION';
  const statusDetail = running
    ? `${escapeHTML(data.start_session)} → ${escapeHTML(data.end_session)} · ${data.sessions} sesiones · ${data.folds} folds · cartera continua`
    : recalculating
      ? 'La evidencia OOS anterior fue retirada porque reiniciaba la cartera entre folds; se espera el recálculo v2 continuo.'
      : 'El walk-forward todavía no ha sido publicado.';
  statusRoot.innerHTML = `<div class="trust-strip ${running ? 'ok' : 'warn'}"><div><strong>${escapeHTML(data.status || 'WAITING')}</strong></div><span class="trust-detail">${statusDetail}</span></div>`;

  const summary = document.getElementById('oosSummary');
  if (summary) {
    summary.innerHTML = rows.length ? [
      metric('Challenger · posición', challenger?.rank ? `#${challenger.rank}` : '—', `${pct(challenger?.return)} retorno encadenado`, challenger?.rank === 1 ? 'ok' : ''),
      metric('Challenger vs SPY', signedPct(challenger && spy ? Number(challenger.return) - Number(spy.return) : null), 'exceso de retorno model-OOS'),
      metric('Folds positivos', challenger ? `${challenger.positive_folds}/${challenger.folds}` : '—', 'consistencia entre ventanas'),
      metric('Evidencia', data.historical_model_out_of_sample === true ? 'MODEL-OOS' : '—', 'entrenamiento y madurez anteriores a cada fold', 'ok'),
    ].join('') : '<div class="empty-state"><strong>Walk-forward pendiente.</strong><p>Se publicará cuando termine el workflow histórico con retraining por fold.</p></div>';
  }

  const chart = document.getElementById('oosCompetitionChart');
  const chartMetrics = document.getElementById('oosChartMetrics');
  const windowControl = document.getElementById('oosWindow');
  function renderWindow() {
    const windowKey = String(windowControl?.value || '6m');
    if (chartMetrics) chartMetrics.innerHTML = rows.length ? oosWindowMetrics(rows, data, windowKey) : '';
    if (chart) chart.innerHTML = rows.length ? oosChart(rows, data, windowKey) : '<div class="empty-state"><strong>Sin curva OOS todavía.</strong></div>';
  }
  windowControl?.addEventListener('change', renderWindow);
  renderWindow();

  const table = document.getElementById('oosTable');
  if (table) {
    table.innerHTML = rows.length ? rows.map((row) => `<tr class="${row.strategy === 'capital_allocation_challenger' ? 'league-challenger-row' : ''}">
      <td><strong>#${escapeHTML(String(row.rank ?? '—'))}</strong></td>
      <td><strong>${escapeHTML(label(row.strategy))}</strong></td>
      <td>${money(row.nav)}</td>
      <td class="${Number(row.return) >= 0 ? 'positive' : 'negative'}">${pct(row.return)}</td>
      <td class="${Number(row.vs_spy) >= 0 ? 'positive' : 'negative'}">${pct(row.vs_spy)}</td>
      <td>${number(row.sharpe)}</td>
      <td class="negative">${pct(row.max_drawdown)}</td>
      <td>${escapeHTML(String(row.trades ?? '—'))}</td>
      <td>${escapeHTML(`${row.positive_folds ?? 0}/${row.folds ?? data.folds ?? 0}`)}</td>
      <td>${money(row.costs_paid_continuous ?? row.costs_paid_per_10k_fold_sum)}</td>
    </tr>`).join('') : '<tr><td colspan="10"><div class="empty-state"><strong>Sin resultados walk-forward.</strong></div></td></tr>';
  }
  const note = document.getElementById('oosNote');
  if (note) note.textContent = running
    ? 'Model-OOS: cada fold entrena únicamente con pasado, pero cash, posiciones y costos continúan en una sola cartera entre folds; cambiar el modelo no liquida ni reinicia la cuenta. La configuración de estrategia fue seleccionada retrospectivamente, así que esto no sustituye la Strategy League prospectiva. El filtro temporal cambia sólo la visualización.'
    : recalculating
      ? 'El resultado OOS legacy no se muestra porque incluía liquidaciones/recompras artificiales en cada fold. Sólo volverán a aparecer cifras cuando exista un artifact v2 con cartera continua.'
      : 'Pendiente de ejecución.';
}

function sourceTable(data) {
  const rows = Array.isArray(data?.source_attribution) ? data.source_attribution : [];
  if (!rows.length) return '<div class="empty-state"><strong>Sin P&L realizado atribuible todavía.</strong></div>';
  return `<div class="table-wrap"><table><thead><tr><th>Fuente</th><th>P&L neto atribuido</th><th>Participaciones</th></tr></thead><tbody>${rows.map((row) => `<tr><td><strong>${escapeHTML(label(row.source_strategy))}</strong></td><td class="${Number(row.net_pnl_equal_split) >= 0 ? 'positive' : 'negative'}">${money(row.net_pnl_equal_split)}</td><td>${escapeHTML(String(row.realized_trade_participations ?? 0))}</td></tr>`).join('')}</tbody></table></div>`;
}

function attributionPanel(data, title, evidenceLabel) {
  if (!data || data.status !== 'OK') {
    return `<article class="panel"><span class="eyebrow">${escapeHTML(evidenceLabel)}</span><h3>${escapeHTML(title)}</h3><div class="empty-state"><strong>Esperando trades realizados.</strong><p>La atribución aparecerá cuando exista historial ejecutado suficiente.</p></div></article>`;
  }
  const summary = data.summary || {};
  const exposure = data.exposure || {};
  return `<article class="panel">
    <span class="eyebrow">${escapeHTML(evidenceLabel)}</span><h3>${escapeHTML(title)}</h3>
    <div class="league-summary-grid">
      ${metric('P&L neto realizado', money(summary.realized_net_pnl), `${summary.realized_round_trips ?? 0} round trips`, Number(summary.realized_net_pnl) >= 0 ? 'ok' : 'bad')}
      ${metric('Win rate', summary.win_rate == null ? '—' : pct(summary.win_rate), `${summary.wins ?? 0} wins · ${summary.losses ?? 0} losses`)}
      ${metric('Gross exposure', pct(exposure.gross_exposure_pct_nav), `${money(exposure.gross_exposure_usd)} expuesto`)}
      ${metric('Portfolio heat', pct(exposure.stop_heat_pct_nav), 'riesgo abierto aproximado hasta stops')}
      ${metric('Cash', pct(exposure.cash_pct_nav), money(exposure.cash))}
      ${metric('Mayor posición', pct(exposure.max_position_pct_nav), `${exposure.open_positions ?? 0} posiciones abiertas`)}
    </div>
    <h4>Atribución por estrategia fuente</h4>${sourceTable(data)}
  </article>`;
}

async function renderAttribution() {
  const root = document.getElementById('attributionPanels');
  if (!root) return;
  const [retroR, prospectiveR] = await Promise.all([
    loadJSONState('data/strategy_attribution.json', { status: 'WAITING' }),
    loadJSONState('data/strategy_league_attribution.json', { status: 'WAITING' }),
  ]);
  root.innerHTML = [
    attributionPanel(retroR.data, 'Por qué ganó el replay', 'Retrospective attribution'),
    attributionPanel(prospectiveR.data, 'Qué está generando P&L ahora', 'Prospective attribution'),
  ].join('');
}

renderOOS();
renderAttribution();
