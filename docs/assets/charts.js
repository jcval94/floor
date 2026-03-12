import { fmt } from './utils.js';

export function rangeSvg(floor, current, ceiling) {
  if (![floor, current, ceiling].every((x) => Number.isFinite(Number(x)))) return '<div class="small">Sin datos</div>';
  const lo = Math.min(floor, current, ceiling), hi = Math.max(floor, current, ceiling);
  const scale = (x) => ((x - lo) / Math.max(hi - lo, 1e-9)) * 80 + 10;
  const xf = scale(Number(floor)), xc = scale(Number(current)), xce = scale(Number(ceiling));
  return `<svg viewBox="0 0 100 20" style="width:100%;height:52px">
    <line x1="10" y1="10" x2="90" y2="10" stroke="var(--line)" stroke-width="3"/>
    <line x1="${xf}" y1="10" x2="${xce}" y2="10" stroke="var(--accent)" stroke-width="3"/>
    <circle cx="${xf}" cy="10" r="2" fill="var(--ok)"/><circle cx="${xc}" cy="10" r="2.4" fill="var(--warn)"/><circle cx="${xce}" cy="10" r="2" fill="var(--bad)"/>
    <text x="0" y="18" font-size="4">F ${fmt(floor)}</text><text x="35" y="18" font-size="4">C ${fmt(current)}</text><text x="72" y="18" font-size="4">Ce ${fmt(ceiling)}</text>
  </svg>`;
}

export function lineSvg(points = []) {
  if (!points.length) return '<div class="small">Sin datos</div>';
  const ys = points.map((p) => Number(p.value ?? p.equity ?? p.drawdown ?? 0));
  const min = Math.min(...ys), max = Math.max(...ys);
  const coords = points.map((p, i) => {
    const x = (i / Math.max(points.length - 1, 1)) * 100;
    const y = 100 - ((ys[i] - min) / Math.max(max - min, 1e-9)) * 100;
    return `${x},${y}`;
  }).join(' ');
  return `<svg viewBox="0 0 100 100" style="width:100%;height:160px"><polyline fill="none" stroke="var(--accent)" stroke-width="2" points="${coords}"/></svg>`;
}

export function m3WeekBarsSvg(top3 = []) {
  if (!Array.isArray(top3) || !top3.length) return '<div class="small">Sin top-3 m3</div>';
  const probs = top3.map((x) => Number(x.probability ?? x.prob ?? 0));
  const maxP = Math.max(...probs, 1e-9);
  const bars = top3.slice(0, 3).map((x, i) => {
    const week = Number(x.week ?? x.floor_week_m3 ?? 0);
    const p = Number(x.probability ?? x.prob ?? 0);
    const w = (p / maxP) * 80;
    const y = 10 + i * 22;
    return `
      <rect x="10" y="${y}" width="${w}" height="12" fill="var(--accent)" opacity="0.75"></rect>
      <text x="12" y="${y + 9}" font-size="5" fill="white">W${String(week).padStart(2, '0')}</text>
      <text x="${12 + w}" y="${y + 9}" font-size="5">${(p * 100).toFixed(1)}%</text>
    `;
  }).join('');
  return `<svg viewBox="0 0 100 80" style="width:100%;height:110px">${bars}</svg>`;
}
