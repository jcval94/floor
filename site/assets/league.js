import { escapeHTML, fmt, fmtPct, loadJSONState } from './utils.js';
import { lineSvg } from './charts.js';

const LABELS = {
  weekly_opportunity_ridge: 'Weekly Opportunity',
  breakout_protected_by_floor: 'Momentum + Floor',
  benchmark_spy: 'SPY',
  benchmark_equal_weight: 'Equal Weight',
};

function pct(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? fmtPct(numeric * 100) : '—';
}

function number(value, digits = 2) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : '—';
}

function money(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(numeric)
    : '—';
}

function statusCard(data) {
  const running = data?.status === 'RUNNING';
  const detail = running
    ? `Desde ${data.start_session || '—'} · ${data.sessions || 0} sesiones prospectivas`
    : data?.detail || 'La liga aún no ha iniciado.';
  return `<div class="trust-strip ${running ? 'ok' : 'warn'}">
    <div><strong>${escapeHTML(running ? 'Strategy League activa' : String(data?.status || 'PENDIENTE'))}</strong></div>
    <span class="trust-detail">${escapeHTML(detail)}</span>
  </div>`;
}

function tableRows(rows) {
  if (!rows.length) {
    return '<tr><td colspan="7"><div class="empty-state"><strong>Sin historial prospectivo todavía.</strong><p>La liga comenzará cuando exista el modelo semanal congelado y un batch completo.</p></div></td></tr>';
  }
  return rows.map((row) => `
    <tr>
      <td><strong>${escapeHTML(LABELS[row.strategy] || row.strategy || '—')}</strong></td>
      <td>${money(row.nav)}</td>
      <td class="${Number(row.return) >= 0 ? 'positive' : 'negative'}">${pct(row.return)}</td>
      <td class="${Number(row.vs_spy) >= 0 ? 'positive' : 'negative'}">${pct(row.vs_spy)}</td>
      <td>${number(row.sharpe)}</td>
      <td class="negative">${pct(row.max_drawdown)}</td>
      <td>${escapeHTML(String(row.trades ?? '—'))}</td>
    </tr>`).join('');
}

function curves(rows) {
  return rows
    .map((row) => {
      const points = Array.isArray(row.equity_curve) ? row.equity_curve : [];
      if (!points.length) return '';
      const title = LABELS[row.strategy] || row.strategy || 'Strategy';
      return `<article class="panel"><span class="eyebrow">${escapeHTML(title)}</span>${lineSvg(points.map((point) => ({ value: point.nav })), { title })}</article>`;
    })
    .filter(Boolean)
    .join('');
}

async function renderLeague() {
  const statusRoot = document.getElementById('leagueStatus');
  const table = document.getElementById('leagueTable');
  const curveRoot = document.getElementById('leagueCurves');
  if (!statusRoot && !table && !curveRoot) return;

  const result = await loadJSONState('data/strategy_league.json', { status: 'UNKNOWN', rows: [] });
  const data = result.data || { status: 'UNKNOWN', rows: [] };
  const rows = Array.isArray(data.rows) ? data.rows : [];
  if (statusRoot) statusRoot.innerHTML = statusCard(data);
  if (table) table.innerHTML = tableRows(rows);
  if (curveRoot) curveRoot.innerHTML = curves(rows);

  const note = document.getElementById('leagueNote');
  if (note) {
    note.textContent = data.status === 'RUNNING'
      ? `Capital inicial: ${money(data.initial_nav_usd)} por cartera. Shadow-paper únicamente; no hay ejecución LIVE ni promoción automática.`
      : 'Shadow-paper únicamente. El historial no comienza hasta que todas las carteras puedan arrancar el mismo día.';
  }
}

renderLeague();
