import { escapeHTML, fmt } from './utils.js';

export function rangeSvg(floor, current, ceiling, label = 'Rango pronosticado') {
  const values = [floor, current, ceiling].map(Number);
  if (!values.every(Number.isFinite)) {
    return '<div class="empty-inline">Rango no disponible</div>';
  }
  const [f, c, ce] = values;
  const lo = Math.min(f, c, ce);
  const hi = Math.max(f, c, ce);
  const scale = (x) => ((x - lo) / Math.max(hi - lo, 1e-9)) * 76 + 12;
  const xf = scale(f);
  const xc = scale(c);
  const xce = scale(ce);
  return `<svg class="range-chart" viewBox="0 0 100 28" role="img" aria-label="${escapeHTML(label)}: piso ${fmt(f)}, referencia ${fmt(c)}, techo ${fmt(ce)}">
    <line class="range-track" x1="12" y1="10" x2="88" y2="10" />
    <line class="range-downside" x1="${Math.min(xf, xc)}" y1="10" x2="${Math.max(xf, xc)}" y2="10" />
    <line class="range-upside" x1="${Math.min(xc, xce)}" y1="10" x2="${Math.max(xc, xce)}" y2="10" />
    <circle class="range-floor" cx="${xf}" cy="10" r="2.1" />
    <circle class="range-current" cx="${xc}" cy="10" r="2.7" />
    <circle class="range-ceiling" cx="${xce}" cy="10" r="2.1" />
    <text x="4" y="25" class="chart-label">P ${fmt(f)}</text>
    <text x="39" y="25" class="chart-label">Ref ${fmt(c)}</text>
    <text x="74" y="25" class="chart-label">T ${fmt(ce)}</text>
  </svg>`;
}

export function lineSvg(points = [], options = {}) {
  const clean = points
    .map((p, idx) => ({ idx, value: Number(p?.value ?? p?.equity ?? p?.drawdown) }))
    .filter((p) => Number.isFinite(p.value));
  if (!clean.length) return '<div class="empty-chart">Sin datos suficientes para graficar.</div>';

  const ys = clean.map((p) => p.value);
  const min = Math.min(...ys);
  const max = Math.max(...ys);
  const pad = Math.max((max - min) * 0.08, 1e-9);
  const yMin = min - pad;
  const yMax = max + pad;
  const coords = clean.map((p, i) => {
    const x = 5 + (i / Math.max(clean.length - 1, 1)) * 90;
    const y = 88 - ((p.value - yMin) / Math.max(yMax - yMin, 1e-9)) * 72;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' ');
  const title = escapeHTML(options.title || 'Serie temporal');
  const start = clean[0].value;
  const end = clean[clean.length - 1].value;

  return `<svg class="line-chart" viewBox="0 0 100 100" role="img" aria-label="${title}. Inicio ${fmt(start)}, final ${fmt(end)}">
    <line class="chart-grid" x1="5" y1="16" x2="95" y2="16" />
    <line class="chart-grid" x1="5" y1="52" x2="95" y2="52" />
    <line class="chart-grid" x1="5" y1="88" x2="95" y2="88" />
    <polyline class="chart-series" points="${coords}" />
    <text x="5" y="12" class="chart-label">${fmt(max)}</text>
    <text x="5" y="98" class="chart-label">${fmt(min)}</text>
  </svg>`;
}

export function multiLineSvg(series = [], options = {}) {
  const cleanSeries = series
    .map((entry, seriesIndex) => ({
      id: String(entry?.id || `series-${seriesIndex}`),
      label: String(entry?.label || entry?.id || `Serie ${seriesIndex + 1}`),
      seriesIndex,
      points: (Array.isArray(entry?.points) ? entry.points : [])
        .map((point, pointIndex) => ({
          idx: pointIndex,
          value: Number(point?.value ?? point?.nav ?? point?.equity),
          label: String(point?.label ?? point?.session ?? ''),
        }))
        .filter((point) => Number.isFinite(point.value)),
    }))
    .filter((entry) => entry.points.length > 0);

  if (!cleanSeries.length) {
    return '<div class="empty-chart">La liga aún no tiene sesiones suficientes para comparar curvas.</div>';
  }

  const allValues = cleanSeries.flatMap((entry) => entry.points.map((point) => point.value));
  const min = Math.min(...allValues);
  const max = Math.max(...allValues);
  const pad = Math.max((max - min) * 0.10, Math.max(Math.abs(max), 1) * 0.0025);
  const yMin = min - pad;
  const yMax = max + pad;
  const maxPoints = Math.max(...cleanSeries.map((entry) => entry.points.length));

  const polylines = cleanSeries.map((entry) => {
    const coords = entry.points.map((point, index) => {
      const x = 7 + (index / Math.max(maxPoints - 1, 1)) * 88;
      const y = 86 - ((point.value - yMin) / Math.max(yMax - yMin, 1e-9)) * 68;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(' ');
    return `<polyline class="league-series league-series-${entry.seriesIndex % 7}" points="${coords}" data-series="${escapeHTML(entry.id)}" />`;
  }).join('');

  const legend = cleanSeries.map((entry) => {
    const last = entry.points[entry.points.length - 1]?.value;
    return `<span class="league-legend-item"><i class="league-legend-swatch league-series-${entry.seriesIndex % 7}"></i><strong>${escapeHTML(entry.label)}</strong><span>${fmt(last)}</span></span>`;
  }).join('');

  const title = escapeHTML(options.title || 'Comparación de NAV');
  const firstLabel = cleanSeries[0]?.points[0]?.label || '';
  const lastLabel = cleanSeries[0]?.points[cleanSeries[0].points.length - 1]?.label || '';

  return `<div class="league-chart-shell">
    <svg class="league-chart" viewBox="0 0 100 100" role="img" aria-label="${title}. Mínimo ${fmt(min)}, máximo ${fmt(max)}">
      <line class="chart-grid" x1="7" y1="18" x2="95" y2="18" />
      <line class="chart-grid" x1="7" y1="52" x2="95" y2="52" />
      <line class="chart-grid" x1="7" y1="86" x2="95" y2="86" />
      ${polylines}
      <text x="7" y="13" class="chart-label">${fmt(max)}</text>
      <text x="7" y="96" class="chart-label">${fmt(min)}</text>
      <text x="7" y="91" class="chart-label">${escapeHTML(firstLabel)}</text>
      <text x="95" y="91" text-anchor="end" class="chart-label">${escapeHTML(lastLabel)}</text>
    </svg>
    <div class="league-legend" aria-label="Leyenda de estrategias">${legend}</div>
  </div>`;
}

export function m3WeekBarsSvg(top3 = []) {
  if (!Array.isArray(top3) || !top3.length) {
    return '<div class="empty-inline">Timing no disponible</div>';
  }
  const safe = top3.slice(0, 3).map((x) => ({
    week: Number(x?.week ?? x?.floor_week_m3),
    probability: Number(x?.probability ?? x?.prob),
  })).filter((x) => Number.isFinite(x.week) && Number.isFinite(x.probability));
  if (!safe.length) return '<div class="empty-inline">Timing no disponible</div>';
  const maxP = Math.max(...safe.map((x) => x.probability), 1e-9);
  const bars = safe.map((x, i) => {
    const width = Math.max(4, (x.probability / maxP) * 68);
    const y = 8 + i * 21;
    return `<g>
      <text x="2" y="${y + 9}" class="chart-label">S${String(x.week).padStart(2, '0')}</text>
      <rect class="m3-bar-track" x="18" y="${y}" width="70" height="11" rx="5.5"></rect>
      <rect class="m3-bar" x="18" y="${y}" width="${width}" height="11" rx="5.5"></rect>
      <text x="91" y="${y + 9}" text-anchor="end" class="chart-label">${(x.probability * 100).toFixed(1)}%</text>
    </g>`;
  }).join('');
  return `<svg class="m3-bars" viewBox="0 0 100 72" role="img" aria-label="Tres semanas más probables del horizonte de 3 meses">${bars}</svg>`;
}
