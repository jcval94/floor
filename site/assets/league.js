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

const LIVE_MIN_POINTS = 3;
const LEAGUE_MIN_SESSIONS = 5;

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
  if (value === null || value === undefined || value === '') return '—';
  const numeric = Number(value);
  return Number.isFinite(numeric) ? fmtPct(numeric * 100) : '—';
}

function signedPct(value) {
  if (value === null || value === undefined || value === '') return '—';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  const rendered = fmtPct(Math.abs(numeric) * 100);
  return `${numeric > 0 ? '+' : numeric < 0 ? '−' : ''}${rendered}`;
}

function number(value, digits = 2) {
  if (value === null || value === undefined || value === '') return '—';
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : '—';
}

function money(value, digits = 0) {
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

function labelFor(strategy) {
  return LABELS[strategy] || strategy || '—';
}

function shortLabelFor(strategy) {
  return SHORT_LABELS[strategy] || labelFor(strategy);
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


function liveStatusCard(data) {
  const rows = Array.isArray(data?.rows) ? data.rows : [];
  const active = data?.status === 'LIVE' && rows.length > 0;
  const degraded = data?.status === 'DEGRADED' && rows.length > 0;
  const generated = marketTime(data?.generated_at, true);
  const detail = rows.length
    ? `Lectura ${generated} ET · cobertura fresca ${pct(data?.quote_source?.fresh_coverage)} · base EOD ${data?.last_eod_session || '—'}`
    : data?.detail || 'Aún no hay una lectura intradía publicable.';
  const label = active
    ? 'Monitor intradía activo'
    : degraded
      ? 'Monitor intradía parcial'
      : String(data?.status || 'PENDIENTE');
  return `<div class="trust-strip ${active ? 'ok' : 'warn'}">
    <div><strong>${escapeHTML(label)}</strong></div>
    <span class="trust-detail">${escapeHTML(detail)}</span>
  </div>`;
}

function retrospectiveStatusCard(data) {
  const ready = data?.status === 'RETROSPECTIVE_OK' && Array.isArray(data?.rows) && data.rows.length > 0;
  const requestedSessions = Number(data?.requested_sessions);
  const effectiveSessions = Number(data?.sessions);
  const excludedSessions = Number.isFinite(requestedSessions) && Number.isFinite(effectiveSessions)
    ? Math.max(0, requestedSessions - effectiveSessions)
    : 0;
  const coverageDetail = excludedSessions > 0
    ? ` · ${excludedSessions} sesión${excludedSessions === 1 ? '' : 'es'} fuera de la ventana efectiva por cobertura`
    : '';
  const detail = ready
    ? `${data.start_session || '—'} → ${data.end_session || '—'} · ${data.sessions || 0} sesiones · ${money(data.initial_nav_usd)} iniciales por cartera${coverageDetail}`
    : 'El torneo retrospectivo aún no ha sido publicado.';
  return `<div class="trust-strip ${ready ? 'ok' : 'warn'}">
    <div><strong>${escapeHTML(ready ? 'Replay retrospectivo disponible' : String(data?.status || 'PENDIENTE'))}</strong></div>
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
    leader_status: 'INSUFFICIENT_EVIDENCE',
    overall_leader: null,
    overall_leader_return: null,
    strategy_leader: null,
    strategy_leader_return: null,
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
      label: 'Líder provisional',
      value: leaderId ? labelFor(leaderId) : 'Sin evidencia suficiente',
      detail: leaderId ? `${pct(summary.strategy_leader_return)} retorno acumulado` : `Mínimo ${summary.min_sessions_for_leader || 5} sesiones y un retorno distinto`,
      tone: leaderId === 'capital_allocation_challenger' ? 'ok' : '',
    },
    {
      label: 'Challenger · posición',
      value: challengerRank ? `#${challengerRank}` : '—',
      detail: `${pct(challengerReturn)} retorno`,
      tone: challengerRank === 1 && leaderId === 'capital_allocation_challenger' ? 'ok' : '',
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


function liveSummaryCards(data, rows) {
  if (!rows.length) {
    return '<div class="empty-state league-empty"><strong>Sin lectura intradía disponible.</strong><p>El monitor se poblará durante una sesión de mercado cuando exista una base EOD oficial.</p></div>';
  }
  const summary = data?.summary && typeof data.summary === 'object' ? data.summary : {};
  const leaderId = summary.live_strategy_leader;
  const challenger = rows.find((row) => row.strategy === 'capital_allocation_challenger');
  const coverage = Number(data?.quote_source?.fresh_coverage);
  const generated = marketTime(data?.generated_at);
  const cards = [
    {
      label: 'Mayor retorno ahora',
      value: leaderId ? labelFor(leaderId) : '—',
      detail: `${pct(summary.live_strategy_leader_return)} retorno marcado`,
      tone: leaderId === 'capital_allocation_challenger' ? 'ok' : '',
    },
    {
      label: 'Challenger · desde EOD',
      value: signedPct(challenger?.change_since_eod),
      detail: challenger?.rank ? `posición intradía #${challenger.rank}` : 'sin posición',
      tone: Number(challenger?.change_since_eod) > 0 ? 'ok' : Number(challenger?.change_since_eod) < 0 ? 'bad' : '',
    },
    {
      label: 'Challenger vs SPY',
      value: signedPct(challenger?.vs_spy),
      detail: 'diferencia sobre retorno acumulado marcado',
      tone: Number(challenger?.vs_spy) > 0 ? 'ok' : Number(challenger?.vs_spy) < 0 ? 'bad' : '',
    },
    {
      label: 'Cobertura de quotes',
      value: Number.isFinite(coverage) ? pct(coverage) : '—',
      detail: `última publicación ${generated} ET`,
      tone: coverage >= 0.95 ? 'ok' : 'bad',
    },
  ];
  return cards.map((card) => `<article class="metric-card league-metric ${card.tone}">
    <span class="metric-label">${escapeHTML(card.label)}</span>
    <strong class="metric-value">${escapeHTML(card.value)}</strong>
    <span class="metric-detail">${escapeHTML(card.detail)}</span>
  </article>`).join('');
}

function retrospectiveSummaryCards(data, rows) {
  if (!rows.length) {
    return '<div class="empty-state league-empty"><strong>Sin replay publicado todavía.</strong><p>Cuando termine el torneo retrospectivo aparecerán aquí el líder y las comparaciones.</p></div>';
  }
  const summary = data?.summary && typeof data.summary === 'object' ? data.summary : fallbackSummary(rows);
  const cards = [
    {
      label: 'Ganador retrospectivo',
      value: labelFor(summary.overall_leader),
      detail: `${pct(summary.overall_leader_return)} retorno`,
      tone: summary.overall_leader === 'capital_allocation_challenger' ? 'ok' : '',
    },
    {
      label: 'Challenger · posición',
      value: summary.challenger_rank ? `#${summary.challenger_rank}` : '—',
      detail: `${pct(summary.challenger_return)} retorno`,
      tone: summary.challenger_rank === 1 ? 'ok' : '',
    },
    {
      label: 'Challenger vs SPY',
      value: signedPct(summary.challenger_vs_spy),
      detail: 'exceso retrospectivo',
      tone: Number(summary.challenger_vs_spy) > 0 ? 'ok' : Number(summary.challenger_vs_spy) < 0 ? 'bad' : '',
    },
    {
      label: 'Ventana',
      value: `${data.sessions || 0} sesiones`,
      detail: `${data.start_session || '—'} → ${data.end_session || '—'}`,
      tone: '',
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
  if (row?.evidence_role === 'diagnostic_only') {
    return '<span class="status-badge neutral"><span class="status-dot"></span>Diagnóstico</span>';
  }
  if (row?.turnover_warning === true) {
    return '<span class="status-badge warn"><span class="status-dot"></span>Turnover alto</span>';
  }
  if (row?.promotion_review_eligible === true) {
    return '<span class="status-badge ok"><span class="status-dot"></span>Elegible</span>';
  }
  return '<span class="status-badge neutral"><span class="status-dot"></span>Tracking</span>';
}

function tableRows(rows) {
  if (!rows.length) {
    return '<tr><td colspan="13"><div class="empty-state"><strong>Sin historial prospectivo todavía.</strong><p>El primer cierre válido de la nueva liga poblará esta clasificación.</p></div></td></tr>';
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
      <td>${number(row.turnover)}×</td>
      <td>${number(row.cost_drag_bps_nav, 0)} bps</td>
      <td>${money(row.costs_paid, 2)}</td>
      <td>${promotionBadge(row)}</td>
    </tr>`;
  }).join('');
}


function liveTableRows(rows) {
  if (!rows.length) {
    return '<tr><td colspan="11"><div class="empty-state"><strong>Sin mark-to-market intradía.</strong><p>La vista se activa en horario de mercado y requiere una base oficial del último EOD.</p></div></td></tr>';
  }
  return rows.map((row, index) => {
    const rank = row.rank ?? index + 1;
    const isChallenger = row.strategy === 'capital_allocation_challenger';
    const coverage = Number(row.quote_coverage);
    const coverageTone = coverage >= 0.95 ? 'ok' : 'warn';
    return `
    <tr class="${isChallenger ? 'league-challenger-row' : ''}">
      <td><strong class="league-rank">#${escapeHTML(String(rank))}</strong></td>
      <td><strong>${escapeHTML(labelFor(row.strategy))}</strong>${isChallenger ? '<span class="league-chip">Challenger</span>' : ''}</td>
      <td>${isBenchmark(row) ? 'Benchmark' : 'Estrategia'}</td>
      <td>${money(row.nav, 2)}</td>
      <td class="${Number(row.change_since_eod) >= 0 ? 'positive' : 'negative'}">${signedPct(row.change_since_eod)}</td>
      <td class="${Number(row.return) >= 0 ? 'positive' : 'negative'}">${pct(row.return)}</td>
      <td class="${Number(row.vs_spy) >= 0 ? 'positive' : 'negative'}">${signedPct(row.vs_spy)}</td>
      <td>${pct(row.gross_exposure_pct)}</td>
      <td>${pct(row.cash_pct)}</td>
      <td>${escapeHTML(String(row.position_count ?? '—'))}</td>
      <td><span class="status-badge ${coverageTone}"><span class="status-dot"></span>${pct(row.quote_coverage)}</span></td>
    </tr>`;
  }).join('');
}

function retrospectiveTableRows(rows) {
  if (!rows.length) {
    return '<tr><td colspan="11"><div class="empty-state"><strong>Sin torneo retrospectivo todavía.</strong><p>El workflow publicará esta tabla al terminar el replay.</p></div></td></tr>';
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
      <td><span class="status-badge neutral"><span class="status-dot"></span>Diagnóstico</span></td>
    </tr>`;
  }).join('');
}


function curvePoints(row, curveField, windowKey = 'all') {
  return filterPointsByWindow(
    Array.isArray(row?.[curveField]) ? row[curveField] : [],
    windowKey,
  );
}

function curveDepth(rows, curveField, windowKey = 'all') {
  return Math.max(
    0,
    ...(Array.isArray(rows) ? rows : []).map((row) => curvePoints(row, curveField, windowKey).length),
  );
}

function warmupChartState(title, detail, current, target) {
  const safeCurrent = Math.max(0, Number(current) || 0);
  const safeTarget = Math.max(1, Number(target) || 1);
  const progress = Math.min(100, (safeCurrent / safeTarget) * 100);
  return `<div class="chart-warmup" role="status">
    <div class="chart-warmup-copy">
      <strong>${escapeHTML(title)}</strong>
      <span>${escapeHTML(detail)}</span>
    </div>
    <div class="chart-warmup-progress" aria-label="${escapeHTML(`${safeCurrent} de ${safeTarget}`)}">
      <span style="width:${progress.toFixed(1)}%"></span>
    </div>
  </div>`;
}

function curveStats(row, curveField, windowKey = 'all') {
  const points = curvePoints(row, curveField, windowKey);
  if (!points.length) return { points, start: null, end: null, return: null, maxDrawdown: null };
  const start = Number(points[0]?.nav ?? points[0]?.equity ?? points[0]?.value);
  const end = Number(points[points.length - 1]?.nav ?? points[points.length - 1]?.equity ?? points[points.length - 1]?.value);
  const ret = Number.isFinite(start) && Number.isFinite(end) && Math.abs(start) > 1e-9
    ? end / start - 1.0
    : null;
  let peak = Number.NEGATIVE_INFINITY;
  let maxDrawdown = 0;
  points.forEach((point) => {
    const nav = Number(point?.nav ?? point?.equity ?? point?.value);
    if (!Number.isFinite(nav)) return;
    peak = Math.max(peak, nav);
    if (Number.isFinite(peak) && peak > 0) maxDrawdown = Math.min(maxDrawdown, nav / peak - 1.0);
  });
  return { points, start, end, return: ret, maxDrawdown };
}

function windowLeader(rows, curveField, windowKey) {
  const candidates = rows
    .filter((row) => !isBenchmark(row))
    .map((row) => ({ row, stats: curveStats(row, curveField, windowKey) }))
    .filter((item) => Number.isFinite(item.stats.return))
    .sort((a, b) => Number(b.stats.return) - Number(a.stats.return));
  return candidates[0]?.row?.strategy || null;
}

function chartKpi(label, value) {
  return `<span class="chart-kpi"><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong></span>`;
}

function chartWindowMetrics(
  rows,
  curveField,
  windowKey,
  { coverage = null, countLabel = 'Sesiones', sessionSuffix = '' } = {},
) {
  const challenger = rows.find((row) => row.strategy === 'capital_allocation_challenger');
  const spy = rows.find((row) => row.strategy === 'benchmark_spy');
  const challengerStats = curveStats(challenger, curveField, windowKey);
  const spyStats = curveStats(spy, curveField, windowKey);
  const reference = challengerStats.points.length
    ? challengerStats.points
    : curvePoints(rows[0], curveField, windowKey);
  const firstSession = reference[0]?.session || '—';
  const lastSession = reference[reference.length - 1]?.session || '—';
  const vsSpy = Number.isFinite(challengerStats.return) && Number.isFinite(spyStats.return)
    ? challengerStats.return - spyStats.return
    : null;
  const period = reference.length
    ? `${firstSession}${sessionSuffix} → ${lastSession}${sessionSuffix}`
    : '—';
  const items = [
    chartKpi('Periodo', period),
    chartKpi('Challenger', signedPct(challengerStats.return)),
    chartKpi('vs SPY', signedPct(vsSpy)),
    chartKpi('Máx. DD', challengerStats.maxDrawdown == null ? '—' : pct(challengerStats.maxDrawdown)),
    chartKpi(countLabel, String(reference.length || 0)),
  ];
  if (coverage != null) items.push(chartKpi('Cobertura', pct(coverage)));
  return items.join('');
}

function curveChart(rows, curveField, title, options = {}) {
  const windowKey = options.windowKey || 'all';
  const withCurves = rows
    .filter((row) => Array.isArray(row[curveField]) && row[curveField].length > 0)
    .sort((a, b) => {
      const aRank = SERIES_RANK.get(String(a.strategy)) ?? SERIES_ORDER.length;
      const bRank = SERIES_RANK.get(String(b.strategy)) ?? SERIES_ORDER.length;
      return aRank - bRank || String(a.strategy).localeCompare(String(b.strategy));
    });
  const series = withCurves.map((row) => ({
    id: row.strategy,
    label: labelFor(row.strategy),
    shortLabel: shortLabelFor(row.strategy),
    points: curvePoints(row, curveField, windowKey).map((point) => ({
      session: point.session,
      value: point.nav ?? point.equity ?? point.value,
    })),
  })).filter((entry) => entry.points.length);
  return multiLineSvg(series, {
    title,
    valueFormat: 'money',
    baseline: options.baseline,
    baselineLabel: options.baselineLabel,
    endLabelIds: options.endLabelIds || [],
    markers: options.markers || [],
  });
}

function competitionChart(rows, title = 'Carrera prospectiva de NAV de Strategy League', options = {}) {
  return curveChart(rows, 'equity_curve', title, options);
}


async function renderLive() {
  const statusRoot = document.getElementById('liveStatus');
  const summaryRoot = document.getElementById('liveSummary');
  const table = document.getElementById('liveTable');
  const chartRoot = document.getElementById('liveCompetitionChart');
  const chartMetrics = document.getElementById('liveChartMetrics');
  if (!statusRoot && !summaryRoot && !table && !chartRoot) return;

  const result = await loadJSONState('data/strategy_live.json', { status: 'UNKNOWN', rows: [] });
  const data = result.data || { status: 'UNKNOWN', rows: [] };
  const rows = Array.isArray(data.rows) ? data.rows : [];
  if (statusRoot) statusRoot.innerHTML = liveStatusCard(data);
  if (summaryRoot) summaryRoot.innerHTML = liveSummaryCards(data, rows);
  if (table) table.innerHTML = liveTableRows(rows);

  const leader = windowLeader(rows, 'intraday_curve', 'all') || data?.summary?.live_strategy_leader;
  const challenger = rows.find((row) => row.strategy === 'capital_allocation_challenger');
  if (chartMetrics) {
    const coverage = Number(data?.quote_source?.fresh_coverage);
    chartMetrics.innerHTML = chartWindowMetrics(rows, 'intraday_curve', 'all', {
      coverage: Number.isFinite(coverage) ? coverage : null,
      countLabel: 'Snapshots',
      sessionSuffix: ' ET',
    });
  }
  if (chartRoot) {
    const livePoints = curveDepth(rows, 'intraday_curve', 'all');
    chartRoot.innerHTML = livePoints < LIVE_MIN_POINTS
      ? warmupChartState(
        'Esperando más observaciones intradía',
        `${livePoints}/${LIVE_MIN_POINTS} snapshots de mercado. La curva aparece cuando ya existe una forma útil; las horas se muestran en ET.`,
        livePoints,
        LIVE_MIN_POINTS,
      )
      : curveChart(rows, 'intraday_curve', 'NAV intradía · mark-to-market', {
        baseline: Number.isFinite(Number(challenger?.eod_nav)) ? Number(challenger.eod_nav) : undefined,
        baselineLabel: 'Challenger EOD',
        endLabelIds: ['capital_allocation_challenger', 'benchmark_spy', leader].filter(Boolean),
      });
  }

  const note = document.getElementById('liveNote');
  if (note) {
    if (rows.length) {
      const source = data?.quote_source?.provider || 'fuente intradía';
      const interval = data?.quote_source?.interval || '—';
      const generated = marketTime(data.generated_at, true);
      note.textContent = `Lectura observacional publicada ${generated} ET. Fuente: ${source}, intervalo ${interval}. Los precios faltantes degradan la cobertura y pueden usar caché de la sesión o el último EOD; este snapshot no cuenta para promoción y no genera órdenes.`;
    } else {
      note.textContent = data?.detail || 'El monitor intradía todavía no tiene datos publicables.';
    }
  }
}


async function renderRetrospective() {
  const statusRoot = document.getElementById('replayStatus');
  const summaryRoot = document.getElementById('replaySummary');
  const table = document.getElementById('replayTable');
  const chartRoot = document.getElementById('replayCompetitionChart');
  const chartMetrics = document.getElementById('replayChartMetrics');
  const windowControl = document.getElementById('replayWindow');
  if (!statusRoot && !summaryRoot && !table && !chartRoot) return;

  const result = await loadJSONState('data/strategy.json', { status: 'UNKNOWN', rows: [] });
  const data = result.data || { status: 'UNKNOWN', rows: [] };
  const rows = Array.isArray(data.rows) ? data.rows : [];
  if (statusRoot) statusRoot.innerHTML = retrospectiveStatusCard(data);
  if (summaryRoot) summaryRoot.innerHTML = retrospectiveSummaryCards(data, rows);
  if (table) table.innerHTML = retrospectiveTableRows(rows);

  function renderWindow() {
    const windowKey = String(windowControl?.value || '3m');
    const leader = windowLeader(rows, 'equity_curve', windowKey);
    if (chartMetrics) chartMetrics.innerHTML = chartWindowMetrics(rows, 'equity_curve', windowKey);
    if (chartRoot) {
      chartRoot.innerHTML = competitionChart(rows, 'Torneo retrospectivo de NAV · ventana filtrada', {
        windowKey,
        baseline: Number(data.initial_nav_usd),
        baselineLabel: `${money(data.initial_nav_usd)} inicial`,
        endLabelIds: ['capital_allocation_challenger', 'benchmark_spy', leader].filter(Boolean),
      });
    }
  }
  windowControl?.addEventListener('change', renderWindow);
  renderWindow();

const note = document.getElementById('replayNote');
  if (note) {
    const selection = data?.session_selection && typeof data.session_selection === 'object'
      ? data.session_selection
      : {};
    const incomplete = Array.isArray(selection.incomplete_sessions)
      ? selection.incomplete_sessions
      : [];
    const coverageNote = incomplete.length
      ? ` Cobertura de mercado incompleta en ${incomplete.length} sesión${incomplete.length === 1 ? '' : 'es'}; se usa el bloque contiguo completo más largo y queda auditado en el reporte.`
      : '';
    note.textContent = rows.length
      ? `${data.methodology_note || 'Replay retrospectivo diagnóstico.'} El filtro cambia sólo la lectura visual; la tabla conserva el resultado de la ventana completa.${coverageNote} Ningún resultado de esta sección cuenta como promoción ni evidencia prospectiva.`
      : 'Aún no hay un reporte retrospectivo publicado.';
  }
}

async function renderLeague() {
  const statusRoot = document.getElementById('leagueStatus');
  const summaryRoot = document.getElementById('leagueSummary');
  const table = document.getElementById('leagueTable');
  const chartRoot = document.getElementById('leagueCompetitionChart');
  const chartMetrics = document.getElementById('leagueChartMetrics');
  const windowControl = document.getElementById('leagueWindow');
  if (!statusRoot && !summaryRoot && !table && !chartRoot) return;

  const result = await loadJSONState('data/strategy_league.json', { status: 'UNKNOWN', rows: [] });
  const data = result.data || { status: 'UNKNOWN', rows: [] };
  const rows = Array.isArray(data.rows) ? data.rows : [];
  if (statusRoot) statusRoot.innerHTML = statusCard(data);
  if (summaryRoot) summaryRoot.innerHTML = summaryCards(data, rows);
  if (table) table.innerHTML = tableRows(rows);

  function renderWindow() {
    const windowKey = String(windowControl?.value || 'all');
    const leader = windowLeader(rows, 'equity_curve', windowKey);
    if (chartMetrics) chartMetrics.innerHTML = chartWindowMetrics(rows, 'equity_curve', windowKey);
    if (chartRoot) {
      const visibleSessions = curveDepth(rows, 'equity_curve', windowKey);
      chartRoot.innerHTML = visibleSessions < LEAGUE_MIN_SESSIONS
        ? warmupChartState(
          'Calentamiento de Strategy League',
          `${visibleSessions}/${LEAGUE_MIN_SESSIONS} sesiones visibles. La carrera completa se habilita al reunir suficiente trayectoria para que las líneas sean interpretables.`,
          visibleSessions,
          LEAGUE_MIN_SESSIONS,
        )
        : competitionChart(rows, 'Carrera prospectiva de NAV de Strategy League', {
          windowKey,
          baseline: Number(data.initial_nav_usd),
          baselineLabel: 'Génesis',
          endLabelIds: ['capital_allocation_challenger', 'benchmark_spy', leader].filter(Boolean),
        });
    }
  }
  windowControl?.addEventListener('change', renderWindow);
  renderWindow();

  const note = document.getElementById('leagueNote');
  if (note) {
    const published = data.published_at ? ` Publicado ${new Date(data.published_at).toLocaleString('es-MX')}.` : '';
    const modelWarning = data.weekly_model?.validation_warning ? ' El modelo semanal congelado mostró validación débil; sus resultados siguen en evaluación.' : '';
    note.textContent = data.status === 'RUNNING'
      ? `Capital inicial: ${money(data.initial_nav_usd)} por cartera. Datos prospectivos shadow-paper; cada EOD actualiza la liga y Pages automáticamente. El selector recorta sólo la visualización, nunca reescribe el ranking oficial. ${data.summary?.leader_status === 'INSUFFICIENT_EVIDENCE' ? 'Todavía no hay líder: faltan sesiones o hay un empate.' : 'Líder provisional; la promoción exige controles adicionales.'}${modelWarning}${published}`
      : `Shadow-paper únicamente. El historial empieza cuando todas las carteras pueden arrancar en igualdad de condiciones.${modelWarning}${published}`;
  }
}

renderLive();
renderRetrospective();
renderLeague();
setInterval(renderLive, 60_000);
