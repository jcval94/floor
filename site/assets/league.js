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

const BENCHMARKS = new Set(['benchmark_spy', 'benchmark_equal_weight']);
const SERIES_ORDER = [
  'capital_allocation_challenger',
  'weekly_opportunity_ridge',
  'breakout_protected_by_floor',
  'mean_reversion_floor_w1',
  'cross_horizon_asymmetry',
  'benchmark_spy',
  'benchmark_equal_weight',
];
const SERIES_RANK = new Map(SERIES_ORDER.map((strategy, index) => [strategy, index]));

function pct(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? fmtPct(numeric * 100) : '—';
}

function signedPct(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  const rendered = fmtPct(Math.abs(numeric) * 100);
  return `${numeric > 0 ? '+' : numeric < 0 ? '−' : ''}${rendered}`;
}

function number(value, digits = 2) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : '—';
}

function money(value, digits = 0) {
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

function labelFor(strategy) {
  return LABELS[strategy] || strategy || '—';
}

function isBenchmark(row) {
  return row?.member_type === 'benchmark' || BENCHMARKS.has(String(row?.strategy || ''));
}

function statusCard(data) {
  const running = data?.status === 'RUNNING';
  const leagueId = data?.league_id || 'strategy_league';
  const detail = running
    ? `${data.start_session || '—'} → ${data.last_session || '—'} · ${data.sessions || 0} sesiones prospectivas · ${leagueId}`
    : data?.detail || 'La liga aún no ha iniciado.';
  return `<div class="trust-strip ${running ? 'ok' : 'warn'}">
    <div><strong>${escapeHTML(running ? 'Strategy League activa' : String(data?.status || 'PENDIENTE'))}</strong></div>
    <span class="trust-detail">${escapeHTML(detail)}</span>
  </div>`;
}

function fallbackSummary(rows) {
  const sorted = [...rows].sort((a, b) => Number(b?.return ?? -Infinity) - Number(a?.return ?? -Infinity));
  const strategies = sorted.filter((row) => !isBenchmark(row));
  const challenger = sorted.find((row) => row.strategy === 'capital_allocation_challenger');
  const spy = sorted.find((row) => row.strategy === 'benchmark_spy');
  const bestBase = strategies.find((row) => row.strategy !== 'capital_allocation_challenger');
  const challengerReturn = Number(challenger?.return);
  const spyReturn = Number(spy?.return);
  const bestBaseReturn = Number(bestBase?.return);
  return {
    overall_leader: sorted[0]?.strategy || null,
    strategy_leader: strategies[0]?.strategy || null,
    strategy_leader_return: Number(strategies[0]?.return),
    challenger_rank: challenger?.rank || (challenger ? sorted.indexOf(challenger) + 1 : null),
    challenger_return: challengerReturn,
    challenger_vs_spy: Number.isFinite(challengerReturn) && Number.isFinite(spyReturn) ? challengerReturn - spyReturn : null,
    best_base_strategy: bestBase?.strategy || null,
    challenger_vs_best_base: Number.isFinite(challengerReturn) && Number.isFinite(bestBaseReturn) ? challengerReturn - bestBaseReturn : null,
    members: rows.length,
    strategies: strategies.length,
    benchmarks: rows.length - strategies.length,
  };
}

function summaryCards(data, rows) {
  if (!rows.length) {
    const scheduled = Array.isArray(data?.scheduled_members)
      ? data.scheduled_members.map((strategy) => labelFor(String(strategy)))
      : [];
    const roster = scheduled.length
      ? `${scheduled.length} carteras registradas: ${scheduled.join(' · ')}`
      : 'El primer EOD completo de la nueva liga generará el ranking y las curvas automáticamente.';
    return `<div class="empty-state league-empty"><strong>La carrera está lista y espera su primera sesión.</strong><p>${escapeHTML(roster)}</p></div>`;
  }
  const summary = data?.summary && typeof data.summary === 'object' ? data.summary : fallbackSummary(rows);
  const leaderId = summary.strategy_leader;
  const challengerRank = summary.challenger_rank;
  const challengerReturn = summary.challenger_return;
  const vsSpy = summary.challenger_vs_spy;
  const vsBase = summary.challenger_vs_best_base;
  const bestBase = summary.best_base_strategy;

  const cards = [
    {
      label: 'Líder estrategia',
      value: labelFor(leaderId),
      detail: `${pct(summary.strategy_leader_return)} retorno acumulado`,
      tone: leaderId === 'capital_allocation_challenger' ? 'ok' : '',
    },
    {
      label: 'Challenger · posición',
      value: challengerRank ? `#${challengerRank}` : '—',
      detail: `${pct(challengerReturn)} retorno`,
      tone: challengerRank === 1 ? 'ok' : '',
    },
    {
      label: 'Challenger vs SPY',
      value: signedPct(vsSpy),
      detail: 'exceso de retorno prospectivo',
      tone: Number(vsSpy) > 0 ? 'ok' : Number(vsSpy) < 0 ? 'bad' : '',
    },
    {
      label: 'Challenger vs mejor base',
      value: signedPct(vsBase),
      detail: bestBase ? `contra ${labelFor(bestBase)}` : 'sin base comparable',
      tone: Number(vsBase) > 0 ? 'ok' : Number(vsBase) < 0 ? 'bad' : '',
    },
  ];

  return cards.map((card) => `<article class="metric-card league-metric ${card.tone}">
    <span class="metric-label">${escapeHTML(card.label)}</span>
    <strong class="metric-value">${escapeHTML(card.value)}</strong>
    <span class="metric-detail">${escapeHTML(card.detail)}</span>
  </article>`).join('');
}

function promotionBadge(row) {
  if (isBenchmark(row)) return '<span class="status-badge neutral"><span class="status-dot"></span>Benchmark</span>';
  if (row?.promotion_review_eligible === true) {
    return '<span class="status-badge ok"><span class="status-dot"></span>Elegible</span>';
  }
  return '<span class="status-badge neutral"><span class="status-dot"></span>Tracking</span>';
}

function tableRows(rows) {
  if (!rows.length) {
    return '<tr><td colspan="11"><div class="empty-state"><strong>Sin historial prospectivo todavía.</strong><p>El primer cierre válido de la nueva liga poblará esta clasificación.</p></div></td></tr>';
  }
  return rows.map((row, index) => {
    const rank = row.rank ?? index + 1;
    const isChallenger = row.strategy === 'capital_allocation_challenger';
    return `
    <tr class="${isChallenger ? 'league-challenger-row' : ''}">
      <td><strong class="league-rank">#${escapeHTML(String(rank))}</strong></td>
      <td><strong>${escapeHTML(labelFor(row.strategy))}</strong>${isChallenger ? '<span class="league-chip">Challenger</span>' : ''}</td>
      <td>${isBenchmark(row) ? 'Benchmark' : 'Estrategia'}</td>
      <td>${money(row.nav, 2)}</td>
      <td class="${Number(row.return) >= 0 ? 'positive' : 'negative'}">${pct(row.return)}</td>
      <td class="${Number(row.vs_spy) >= 0 ? 'positive' : 'negative'}">${pct(row.vs_spy)}</td>
      <td>${number(row.sharpe)}</td>
      <td class="negative">${pct(row.max_drawdown)}</td>
      <td>${escapeHTML(String(row.trades ?? '—'))}</td>
      <td>${money(row.costs_paid, 2)}</td>
      <td>${promotionBadge(row)}</td>
    </tr>`;
  }).join('');
}

function competitionChart(rows) {
  const withCurves = rows
    .filter((row) => Array.isArray(row.equity_curve) && row.equity_curve.length > 0)
    .sort((a, b) => {
      const aRank = SERIES_RANK.get(String(a.strategy)) ?? SERIES_ORDER.length;
      const bRank = SERIES_RANK.get(String(b.strategy)) ?? SERIES_ORDER.length;
      return aRank - bRank || String(a.strategy).localeCompare(String(b.strategy));
    });
  const series = withCurves.map((row) => ({
    id: row.strategy,
    label: labelFor(row.strategy),
    points: row.equity_curve.map((point) => ({
      session: point.session,
      value: point.nav,
    })),
  }));
  return multiLineSvg(series, { title: 'Carrera prospectiva de NAV de Strategy League' });
}

async function renderLeague() {
  const statusRoot = document.getElementById('leagueStatus');
  const summaryRoot = document.getElementById('leagueSummary');
  const table = document.getElementById('leagueTable');
  const chartRoot = document.getElementById('leagueCompetitionChart');
  if (!statusRoot && !summaryRoot && !table && !chartRoot) return;

  const result = await loadJSONState('data/strategy_league.json', { status: 'UNKNOWN', rows: [] });
  const data = result.data || { status: 'UNKNOWN', rows: [] };
  const rows = Array.isArray(data.rows) ? data.rows : [];
  if (statusRoot) statusRoot.innerHTML = statusCard(data);
  if (summaryRoot) summaryRoot.innerHTML = summaryCards(data, rows);
  if (table) table.innerHTML = tableRows(rows);
  if (chartRoot) chartRoot.innerHTML = competitionChart(rows);

  const note = document.getElementById('leagueNote');
  if (note) {
    const published = data.published_at ? ` Publicado ${new Date(data.published_at).toLocaleString('es-MX')}.` : '';
    note.textContent = data.status === 'RUNNING'
      ? `Capital inicial: ${money(data.initial_nav_usd)} por cartera. Datos prospectivos shadow-paper; cada EOD actualiza la liga y Pages automáticamente.${published}`
      : `Shadow-paper únicamente. El historial empieza cuando todas las carteras pueden arrancar en igualdad de condiciones.${published}`;
  }
}

renderLeague();
