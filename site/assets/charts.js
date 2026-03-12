import { fmt } from './utils.js';

export function rangeSvg(floor, current, ceiling) {
  const min = Math.min(floor, current, ceiling);
  const max = Math.max(floor, current, ceiling);
  const scale = (x) => ((x - min) / Math.max(max - min, 1e-6)) * 100;
  const xf = scale(floor), xc = scale(current), xce = scale(ceiling);
  return `<svg class="range" viewBox="0 0 100 20" preserveAspectRatio="none">
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
