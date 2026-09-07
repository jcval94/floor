import { escapeHTML, fmtPct, loadJSONState } from './utils.js';
import { multiLineSvg } from './charts.js';

const LABELS = {
  capital_allocation_challenger: 'Capital Allocation Challenger',
  weekly_opportunity_ridge: 'Weekly Opportunity',
  breakout_protected_by_floor: 'Momentum + Floor',
  mean_reversion_floor_w1: 'Mean Reversion + Floor',
  cross_horizon_asymmetry: 'Cross-Horizon Asymmetry',
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

function pct(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? fmtPct(numeric * 100) : '—';
}

function signedPct(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  const sign = numeric > 0 ? '+' : numeric < 0 ? '−' : '';
  return `${sign}${fmtPct(Math.abs(numeric) * 100)}`;
}

function money(value, digits = 2) {
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

function oosChart(rows) {
  const series = [...rows]
    .filter((row) => Array.isArray(row.equity_curve) && row.equity_curve.length)
    .sort((a, b) => (SERIES_RANK.get(a.strategy) ?? 99) - (SERIES_RANK.get(b.strategy) ?? 99))
    .map((row) => ({
      id: row.strategy,
      label: label(row.strategy),
      points: row.equity_curve.map((point) => ({ session: point.session, value: point.nav })),
    }));
  return multiLineSvg(series, { title: 'Walk-forward histórico con modelos entrenados antes de cada fold' });
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
  statusRoot.innerHTML = `<div class="trust-strip ${running ? 'ok' : 'warn'}"><div><strong>${escapeHTML(data.status || 'WAITING')}</strong></div><span class="trust-detail">${running ? `${escapeHTML(data.start_session)} → ${escapeHTML(data.end_session)} · ${data.sessions} sesiones · ${data.folds} folds` : 'El walk-forward todavía no ha sido publicado.'}</span></div>`;

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
  if (chart) chart.innerHTML = rows.length ? oosChart(rows) : '<div class="empty-state"><strong>Sin curva OOS todavía.</strong></div>';

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
      <td>${money(row.costs_paid_per_10k_fold_sum)}</td>
    </tr>`).join('') : '<tr><td colspan="10"><div class="empty-state"><strong>Sin resultados walk-forward.</strong></div></td></tr>';
  }
  const note = document.getElementById('oosNote');
  if (note) note.textContent = running
    ? 'Model-OOS: cada fold entrena únicamente con observaciones y targets maduros anteriores al inicio del fold. La configuración de estrategia fue seleccionada retrospectivamente, así que esto no sustituye la Strategy League prospectiva.'
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
