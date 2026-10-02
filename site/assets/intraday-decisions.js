import { escapeHTML, loadJSONState } from './utils.js';

const LABELS = {
  weekly_opportunity_ridge: 'Weekly Opportunity',
  breakout_protected_by_floor: 'Momentum + Floor',
  mean_reversion_floor_w1: 'Mean Reversion + Floor',
  cross_horizon_asymmetry: 'Cross-Horizon Asymmetry',
};

function labelFor(strategy) {
  return LABELS[strategy] || strategy || '—';
}

function number(value, digits = 4) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : '—';
}

function timingLabel(row) {
  const timing = row && row.decision_trace && row.decision_trace.intraday_timing
    ? row.decision_trace.intraday_timing
    : {};
  const status = String(timing.status || 'unavailable');
  const labels = {
    confirmed: 'Confirma',
    contradicted: 'Contradice',
    mixed: 'Mixto',
    unavailable: 'Sin 15m/1h',
    not_applicable: 'No aplica',
  };
  const r15 = Number(timing.return_15m);
  const r1h = Number(timing.return_1h);
  const detail = Number.isFinite(r15) && Number.isFinite(r1h)
    ? ' · 15m ' + (r15 * 100).toFixed(2) + '% · 1h ' + (r1h * 100).toFixed(2) + '%'
    : '';
  return (labels[status] || status) + detail;
}

function marketTime(value, includeDate = false) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('es-MX', {
    timeZone: 'America/New_York',
    ...(includeDate ? { day: '2-digit', month: 'short' } : {}),
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
}

function decisionSide(trace = {}) {
  const inputs = trace.inputs || {};
  if (Number.isFinite(Number(inputs.model_implied_signed_return))) return Number(inputs.model_implied_signed_return) >= 0 ? 'long' : 'short';
  if (Number.isFinite(Number(inputs.reversal_signal_pct))) return Number(inputs.reversal_signal_pct) >= 0 ? 'long' : 'short';
  if (Number.isFinite(Number(inputs.trend))) return Number(inputs.trend) >= 0 ? 'long' : 'short';
  const longRatio = Number(inputs.long_asymmetry_ratio);
  const shortRatio = Number(inputs.short_asymmetry_ratio);
  if (Number.isFinite(longRatio) || Number.isFinite(shortRatio)) return (longRatio || 0) >= (shortRatio || 0) ? 'long' : 'short';
  return 'long';
}

function dominantHoldGate(decisions) {
  const counts = new Map();
  (Array.isArray(decisions) ? decisions : []).filter((row) => row && row.action === 'HOLD').forEach((row) => {
    const trace = row.decision_trace || {};
    const gates = trace.gates || {};
    const side = decisionSide(trace);
    const relevant = Object.entries(gates).filter(([name, passed]) => passed === false && (name === 'liquidity' || name.startsWith(side + '_')));
    const failed = relevant.length ? relevant[0][0] : null;
    if (failed) counts.set(failed, (counts.get(failed) || 0) + 1);
  });
  const ranked = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  if (!ranked.length) return '—';
  return ranked[0][0].replaceAll('_', ' ') + ' · ' + ranked[0][1];
}

function statusCard(data) {
  const ready = data && data.status === 'READY';
  const summary = (data && data.summary) || {};
  const detail = ready
    ? String(data.event || '—') + ' · ' + marketTime(data.as_of, true) + ' ET · ' + String(summary.decisions_evaluated || 0) + ' decisiones · ' + String(summary.actionable_decisions || 0) + ' accionables'
    : (data && data.detail) || 'Esperando el primer checkpoint de decisiones.';
  return '<div class="trust-strip ' + (ready ? 'ok' : 'warn') + '"><div><strong>' + escapeHTML(ready ? 'Motor de decisiones intradía activo' : String((data && data.status) || 'PENDIENTE')) + '</strong></div><span class="trust-detail">' + escapeHTML(detail) + '</span></div>';
}

function summaryCards(data) {
  const summary = (data && data.summary) || {};
  const challenger = (data && data.capital_allocation_challenger) || {};
  const cards = [
    ['Checkpoint', String((data && data.event) || '—'), String((data && data.session_day) || '—') + ' · ' + marketTime(data && data.as_of) + ' ET', data && data.status === 'READY' ? 'ok' : ''],
    ['Decisiones', String(summary.decisions_evaluated || 0), String(summary.strategies_evaluated || 0) + ' estrategias × ' + String(summary.symbols_evaluated || 0) + ' tickers', Number(summary.decisions_evaluated) > 0 ? 'ok' : ''],
    ['Accionables', String(summary.actionable_decisions || 0), 'BUY/SELL que superaron gates y sizing', Number(summary.actionable_decisions) > 0 ? 'ok' : ''],
    ['Timing confirmado', String((((data || {}).intraday_timing_adapter || {}).status_counts || {}).confirmed || 0), '15m/1h sólo reordena señales válidas; nunca crea acciones', ''],
    ['Challenger targets', String(challenger.target_count || 0), challenger.action === 'ALLOCATE' ? 'asignación shadow disponible' : 'permanece en HOLD', Number(challenger.target_count) > 0 ? 'ok' : ''],
  ];
  return cards.map((card) => '<article class="metric-card league-metric ' + card[3] + '"><span class="metric-label">' + escapeHTML(card[0]) + '</span><strong class="metric-value">' + escapeHTML(card[1]) + '</strong><span class="metric-detail">' + escapeHTML(card[2]) + '</span></article>').join('');
}

function strategyRows(data) {
  const strategies = data && data.strategies && typeof data.strategies === 'object' ? data.strategies : {};
  const entries = Object.entries(strategies);
  if (!entries.length) return '<tr><td colspan="6"><div class="empty-state"><strong>Sin decisiones publicadas.</strong><p>El primer checkpoint aceptado poblará esta tabla.</p></div></td></tr>';
  return entries.map(([strategyId, payload]) => {
    const counts = (payload && payload.action_counts) || {};
    const decisions = payload && Array.isArray(payload.decisions) ? payload.decisions : [];
    return '<tr><td><strong>' + escapeHTML(labelFor(strategyId)) + '</strong></td><td class="' + (Number(counts.BUY) > 0 ? 'positive' : '') + '">' + escapeHTML(String(counts.BUY || 0)) + '</td><td class="' + (Number(counts.SELL) > 0 ? 'negative' : '') + '">' + escapeHTML(String(counts.SELL || 0)) + '</td><td>' + escapeHTML(String(counts.HOLD || 0)) + '</td><td>' + escapeHTML(String((payload && payload.actionable_count) || 0)) + '</td><td>' + escapeHTML(dominantHoldGate(decisions)) + '</td></tr>';
  }).join('');
}

function candidateRows(data) {
  const strategies = data && data.strategies && typeof data.strategies === 'object' ? data.strategies : {};
  const candidates = Object.entries(strategies).flatMap(([strategyId, payload]) => {
    const rows = payload && Array.isArray(payload.top_actionable) ? payload.top_actionable : [];
    return rows.map((row) => ({ ...row, strategy_id: strategyId }));
  }).sort((a, b) => Number(b.score || 0) - Number(a.score || 0)).slice(0, 15);
  if (!candidates.length) return '<tr><td colspan="7"><div class="empty-state"><strong>Este checkpoint no produjo señales accionables.</strong><p>La tabla superior muestra el gate dominante que mantuvo a cada estrategia en HOLD.</p></div></td></tr>';
  return candidates.map((row) => {
    const action = String(row.action || 'HOLD');
    const tone = action === 'BUY' ? 'positive' : action === 'SELL' ? 'negative' : '';
    const baseScore = Number.isFinite(Number(row.base_score)) ? number(row.base_score) : number(row.score);
    const intraScore = Number.isFinite(Number(row.intraday_rank_score)) ? number(row.intraday_rank_score) : number(row.score);
    return '<tr><td>' + escapeHTML(labelFor(row.strategy_id)) + '</td><td><strong>' + escapeHTML(String(row.symbol || '—')) + '</strong></td><td class="' + tone + '"><strong>' + escapeHTML(action) + '</strong></td><td><strong>' + baseScore + ' → ' + intraScore + '</strong></td><td>' + escapeHTML(timingLabel(row)) + '</td><td>' + escapeHTML(String(row.horizon || '—').toUpperCase()) + '</td><td>' + escapeHTML(String(row.reason || '—')) + '</td></tr>';
  }).join('');
}

async function renderIntradayDecisions() {
  const statusRoot = document.getElementById('decisionStatus');
  const summaryRoot = document.getElementById('decisionSummary');
  const tableRoot = document.getElementById('decisionTable');
  const candidateRoot = document.getElementById('decisionCandidates');
  const noteRoot = document.getElementById('decisionNote');
  if (!statusRoot && !summaryRoot && !tableRoot && !candidateRoot) return;
  const result = await loadJSONState('data/strategy_decisions_intraday.json', { status: 'WAITING_FOR_INTRADAY_DECISIONS', strategies: {}, summary: {}, capital_allocation_challenger: { target_count: 0, targets: {} } });
  const data = result.data || {};
  if (statusRoot) statusRoot.innerHTML = statusCard(data);
  if (summaryRoot) summaryRoot.innerHTML = summaryCards(data);
  if (tableRoot) tableRoot.innerHTML = strategyRows(data);
  if (candidateRoot) candidateRoot.innerHTML = candidateRows(data);
  if (noteRoot) {
    const targets = Object.keys((data.capital_allocation_challenger && data.capital_allocation_challenger.targets) || {});
    const targetText = targets.length ? ' Targets shadow del Capital Challenger: ' + targets.join(', ') + '.' : ' El Capital Challenger no encontró targets elegibles en este checkpoint.';
    noteRoot.textContent = data.status === 'READY'
      ? 'La geometría central decide la oportunidad; el envelope de riesgo calibrado se reserva para stops y sizing. Los retornos 15m/1h sólo ajustan el ranking intradía entre señales que ya eran válidas; no cambian BUY/SELL/HOLD ni quantity.' + targetText + ' Ninguna decisión de esta sección ejecuta órdenes.'
      : data.detail || 'Esperando decisiones del checkpoint.';
  }
}

renderIntradayDecisions();
setInterval(renderIntradayDecisions, 60_000);
