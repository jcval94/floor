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

const SESSION_WINDOW_SIZES = {
  '5d': 5,
  '10d': 10,
  '20d': 20,
  '2w': 10,
  '1m': 21,
  '3m': 63,
  '6m': 126,
  all: Number.POSITIVE_INFINITY,
};

export function windowSessionCount(windowKey = 'all') {
  return SESSION_WINDOW_SIZES[windowKey] ?? Number.POSITIVE_INFINITY;
}

export function filterPointsByWindow(points = [], windowKey = 'all') {
  const safe = Array.isArray(points) ? points : [];
  const limit = windowSessionCount(windowKey);
  if (!Number.isFinite(limit) || safe.length <= limit) return [...safe];
  return safe.slice(-limit);
}

function temporalValue(label) {
  const raw = String(label || '').trim();
  if (!raw) return null;
  const dateMatch = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (dateMatch) {
    const [, year, month, day] = dateMatch;
    return Date.UTC(Number(year), Number(month) - 1, Number(day));
  }
  const timeMatch = raw.match(/^(\d{1,2}):(\d{2})$/);
  if (timeMatch) return Number(timeMatch[1]) * 60 + Number(timeMatch[2]);
  return null;
}

function shortTemporalLabel(label) {
  const raw = String(label || '').trim();
  const dateMatch = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!dateMatch) return raw;
  const [, year, month, day] = dateMatch;
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  return new Intl.DateTimeFormat('es-MX', {
    day: 'numeric',
    month: 'short',
    year: '2-digit',
    timeZone: 'UTC',
  }).format(date);
}

function numericValue(value) {
  if (value === null || value === undefined || value === '') return NaN;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : NaN;
}

function chartValue(value, options = {}) {
  const numeric = numericValue(value);
  if (!Number.isFinite(numeric)) return '—';
  if (options.valueFormat === 'percent') {
    return `${(numeric * 100).toFixed(options.valueDigits ?? 1)}%`;
  }
  if (options.valueFormat === 'money') {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      maximumFractionDigits: options.valueDigits ?? 0,
    }).format(numeric);
  }
  return fmt(numeric, options.valueDigits ?? 2);
}

function xCoordinate(label, index, labels, left = 7, right = 95) {
  const temporal = labels.map(temporalValue);
  const canUseTemporal = temporal.every((value) => Number.isFinite(value));
  if (canUseTemporal) {
    const min = Math.min(...temporal);
    const max = Math.max(...temporal);
    if (max > min) {
      const current = temporalValue(label);
      return left + ((current - min) / (max - min)) * (right - left);
    }
  }
  return left + (Math.max(0, index) / Math.max(labels.length - 1, 1)) * (right - left);
}

function referenceLines(yMin, yMax, options = {}) {
  const scaleY = (value) => 86 - ((value - yMin) / Math.max(yMax - yMin, 1e-9)) * 68;
  const lines = [];
  if (Number.isFinite(numericValue(options.baseline))) {
    const value = numericValue(options.baseline);
    if (value >= yMin && value <= yMax) {
      const y = scaleY(value);
      lines.push(`<line class="chart-baseline" x1="7" y1="${y.toFixed(2)}" x2="95" y2="${y.toFixed(2)}" />`);
      if (options.baselineLabel) {
        lines.push(`<text x="94" y="${Math.max(10, y - 2).toFixed(2)}" text-anchor="end" class="chart-reference-label">${escapeHTML(options.baselineLabel)}</text>`);
      }
    }
  }
  (Array.isArray(options.thresholds) ? options.thresholds : []).forEach((threshold) => {
    const value = numericValue(threshold?.value);
    if (!Number.isFinite(value) || value < yMin || value > yMax) return;
    const y = scaleY(value);
    lines.push(`<line class="chart-threshold" x1="7" y1="${y.toFixed(2)}" x2="95" y2="${y.toFixed(2)}" />`);
    if (threshold?.label) {
      lines.push(`<text x="94" y="${Math.max(10, y - 2).toFixed(2)}" text-anchor="end" class="chart-reference-label">${escapeHTML(String(threshold.label))}</text>`);
    }
  });
  return lines.join('');
}

export function lineSvg(points = [], options = {}) {
  const clean = (Array.isArray(points) ? points : [])
    .map((p, idx) => ({
      idx,
      value: numericValue(p?.value ?? p?.equity ?? p?.drawdown),
      label: String(p?.label ?? p?.session ?? ''),
    }))
    .filter((p) => Number.isFinite(p.value));
  if (!clean.length) return '<div class="empty-chart">Sin datos suficientes para graficar.</div>';

  const labels = clean.map((point, index) => point.label || String(index + 1));
  const referenceValues = [
    ...(Number.isFinite(numericValue(options.baseline)) ? [numericValue(options.baseline)] : []),
    ...(Array.isArray(options.thresholds)
      ? options.thresholds.map((item) => Number(item?.value)).filter(Number.isFinite)
      : []),
  ];
  const ys = [...clean.map((p) => p.value), ...referenceValues];
  const min = Math.min(...ys);
  const max = Math.max(...ys);
  const pad = Math.max((max - min) * 0.10, Math.max(Math.abs(max), 1) * 0.0025);
  const yMin = min - pad;
  const yMax = max + pad;
  const scaleY = (value) => 86 - ((value - yMin) / Math.max(yMax - yMin, 1e-9)) * 68;
  const coords = clean.map((p, i) => {
    const x = xCoordinate(labels[i], i, labels, 7, 95);
    const y = scaleY(p.value);
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' ');
  const title = escapeHTML(options.title || 'Serie temporal');
  const start = clean[0];
  const end = clean[clean.length - 1];
  const minPoint = clean.reduce((best, point) => point.value < best.value ? point : best, clean[0]);
  const middle = clean[Math.floor((clean.length - 1) / 2)];
  const minX = xCoordinate(minPoint.label || labels[minPoint.idx], minPoint.idx, labels);
  const endX = xCoordinate(end.label || labels[clean.length - 1], clean.length - 1, labels);
  const annotations = options.annotateExtrema ? `
      <circle class="chart-key-point" cx="${minX.toFixed(2)}" cy="${scaleY(minPoint.value).toFixed(2)}" r="1.7" />
      <text class="chart-end-label" x="${minX.toFixed(2)}" y="${Math.min(98, scaleY(minPoint.value) + 6).toFixed(2)}" text-anchor="middle">${escapeHTML(options.minLabel || `Mín ${chartValue(minPoint.value, options)}`)}</text>` : '';
  const endAnnotation = options.annotateEnd !== false ? `
      <circle class="chart-end-point" cx="${endX.toFixed(2)}" cy="${scaleY(end.value).toFixed(2)}" r="1.9" />
      <text class="chart-end-label" x="94" y="${Math.max(11, scaleY(end.value) - 3).toFixed(2)}" text-anchor="end">${escapeHTML(options.endLabel || chartValue(end.value, options))}</text>` : '';

  return `<svg class="line-chart enhanced-chart" viewBox="0 0 100 100" role="img" aria-label="${title}. Inicio ${chartValue(start.value, options)}, final ${chartValue(end.value, options)}">
    <line class="chart-grid" x1="7" y1="18" x2="95" y2="18" />
    <line class="chart-grid" x1="7" y1="52" x2="95" y2="52" />
    <line class="chart-grid" x1="7" y1="86" x2="95" y2="86" />
    ${referenceLines(yMin, yMax, options)}
    <polyline class="chart-series" points="${coords}" />
    ${annotations}
    ${endAnnotation}
    <text x="7" y="13" class="chart-label">${escapeHTML(chartValue(max, options))}</text>
    <text x="7" y="96" class="chart-label">${escapeHTML(chartValue(min, options))}</text>
    <text x="7" y="91" class="chart-label">${escapeHTML(shortTemporalLabel(start.label))}</text>
    <text x="51" y="91" text-anchor="middle" class="chart-label">${escapeHTML(shortTemporalLabel(middle.label))}</text>
    <text x="95" y="91" text-anchor="end" class="chart-label">${escapeHTML(shortTemporalLabel(end.label))}</text>
  </svg>`;
}

export function multiLineSvg(series = [], options = {}) {
  const cleanSeries = series
    .map((entry, seriesIndex) => ({
      id: String(entry?.id || `series-${seriesIndex}`),
      label: String(entry?.label || entry?.id || `Serie ${seriesIndex + 1}`),
      shortLabel: String(entry?.shortLabel || entry?.label || entry?.id || `Serie ${seriesIndex + 1}`),
      seriesIndex,
      points: (Array.isArray(entry?.points) ? entry.points : [])
        .map((point, pointIndex) => ({
          idx: pointIndex,
          value: numericValue(point?.value ?? point?.nav ?? point?.equity),
          label: String(point?.label ?? point?.session ?? ''),
        }))
        .filter((point) => Number.isFinite(point.value)),
    }))
    .filter((entry) => entry.points.length > 0);

  if (!cleanSeries.length) {
    return '<div class="empty-chart">La liga aún no tiene sesiones suficientes para comparar curvas.</div>';
  }

  const labels = [];
  cleanSeries.forEach((entry) => entry.points.forEach((point, index) => {
    const label = point.label || `#${index + 1}`;
    if (!labels.includes(label)) labels.push(label);
  }));
  labels.sort((left, right) => {
    const l = temporalValue(left);
    const r = temporalValue(right);
    if (Number.isFinite(l) && Number.isFinite(r)) return l - r;
    return String(left).localeCompare(String(right));
  });
  const labelIndex = new Map(labels.map((label, index) => [label, index]));

  const geometry = {
    width: 220,
    height: 72,
    left: 10,
    right: 214,
    top: 12,
    bottom: 57,
    axisY: 68,
  };

  const allValues = cleanSeries.flatMap((entry) => entry.points.map((point) => point.value));
  const baseline = numericValue(options.baseline);
  if (Number.isFinite(baseline)) allValues.push(baseline);
  const thresholds = Array.isArray(options.thresholds) ? options.thresholds : [];
  thresholds.forEach((threshold) => {
    const value = numericValue(threshold?.value);
    if (Number.isFinite(value)) allValues.push(value);
  });
  const min = Math.min(...allValues);
  const max = Math.max(...allValues);
  const pad = Math.max((max - min) * 0.10, Math.max(Math.abs(max), 1) * 0.0025);
  const yMin = min - pad;
  const yMax = max + pad;
  const scaleY = (value) => geometry.bottom
    - ((value - yMin) / Math.max(yMax - yMin, 1e-9)) * (geometry.bottom - geometry.top);

  const referenceMarkup = [];
  if (Number.isFinite(baseline) && baseline >= yMin && baseline <= yMax) {
    const y = scaleY(baseline);
    referenceMarkup.push(
      `<line class="chart-baseline" x1="${geometry.left}" y1="${y.toFixed(2)}" x2="${geometry.right}" y2="${y.toFixed(2)}" />`,
    );
    if (options.baselineLabel) {
      referenceMarkup.push(
        `<text x="${geometry.left + 1.5}" y="${Math.max(geometry.top + 2, y - 1.5).toFixed(2)}" text-anchor="start" class="chart-reference-label">${escapeHTML(options.baselineLabel)}</text>`,
      );
    }
  }
  thresholds.forEach((threshold) => {
    const value = numericValue(threshold?.value);
    if (!Number.isFinite(value) || value < yMin || value > yMax) return;
    const y = scaleY(value);
    referenceMarkup.push(
      `<line class="chart-threshold" x1="${geometry.left}" y1="${y.toFixed(2)}" x2="${geometry.right}" y2="${y.toFixed(2)}" />`,
    );
    if (threshold?.label) {
      referenceMarkup.push(
        `<text x="${geometry.left + 1.5}" y="${Math.max(geometry.top + 2, y - 1.5).toFixed(2)}" text-anchor="start" class="chart-reference-label">${escapeHTML(String(threshold.label))}</text>`,
      );
    }
  });

  const polylines = cleanSeries.map((entry) => {
    const coords = entry.points.map((point, pointIndex) => {
      const label = point.label || `#${pointIndex + 1}`;
      const index = labelIndex.get(label) ?? pointIndex;
      const x = xCoordinate(label, index, labels, geometry.left, geometry.right);
      const y = scaleY(point.value);
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(' ');
    return `<polyline class="league-series league-series-${entry.seriesIndex % 7}" points="${coords}" data-series="${escapeHTML(entry.id)}" />`;
  }).join('');

  const markerLines = (Array.isArray(options.markers) ? options.markers : []).map((marker) => {
    const label = String(marker?.session ?? marker?.label ?? '');
    const index = labelIndex.get(label);
    if (index == null) return '';
    const x = xCoordinate(label, index, labels, geometry.left, geometry.right);
    return `<g class="chart-marker-group"><line class="chart-marker" x1="${x.toFixed(2)}" y1="${geometry.top}" x2="${x.toFixed(2)}" y2="${geometry.bottom}" />${marker?.text ? `<text class="chart-marker-label" x="${Math.min(geometry.right - 2, x + 1.3).toFixed(2)}" y="${geometry.top + 3}">${escapeHTML(String(marker.text))}</text>` : ''}</g>`;
  }).join('');

  const endIds = new Set(Array.isArray(options.endLabelIds) ? options.endLabelIds.map(String) : []);
  const rawEndLabels = cleanSeries
    .filter((entry) => endIds.has(entry.id))
    .map((entry) => {
      const last = entry.points[entry.points.length - 1];
      const label = last.label || labels[labels.length - 1];
      const index = labelIndex.get(label) ?? labels.length - 1;
      const x = xCoordinate(label, index, labels, geometry.left, geometry.right);
      const y = scaleY(last.value);
      return { entry, last, x, y, labelY: y - 1.4 };
    })
    .sort((a, b) => a.y - b.y);

  const labelGap = 4.2;
  let nextLabelY = geometry.top + 1.5;
  rawEndLabels.forEach((item) => {
    item.labelY = Math.max(item.labelY, nextLabelY);
    nextLabelY = item.labelY + labelGap;
  });
  if (rawEndLabels.length) {
    const lastLabel = rawEndLabels[rawEndLabels.length - 1];
    const overflow = Math.max(0, lastLabel.labelY - (geometry.bottom - 1));
    if (overflow > 0) rawEndLabels.forEach((item) => { item.labelY -= overflow; });
    const firstLabel = rawEndLabels[0];
    const underflow = Math.max(0, (geometry.top + 1.5) - firstLabel.labelY);
    if (underflow > 0) rawEndLabels.forEach((item) => { item.labelY += underflow; });
  }

  const endLabels = rawEndLabels.map(({ entry, last, x, y, labelY }) => {
    const nearRight = x > geometry.right - 20;
    const labelX = nearRight ? geometry.right - 1 : Math.min(geometry.right - 1, x + 2);
    const guide = Math.abs(labelY - y) > 2
      ? `<line class="chart-label-guide" x1="${x.toFixed(2)}" y1="${y.toFixed(2)}" x2="${labelX.toFixed(2)}" y2="${labelY.toFixed(2)}" />`
      : '';
    return `<g>${guide}<circle class="chart-end-point league-series-${entry.seriesIndex % 7}" cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="1.15" /><text class="chart-end-label" x="${labelX.toFixed(2)}" y="${labelY.toFixed(2)}" text-anchor="${nearRight ? 'end' : 'start'}">${escapeHTML(entry.shortLabel)} ${escapeHTML(chartValue(last.value, options))}</text></g>`;
  }).join('');

  const legend = cleanSeries.map((entry) => {
    const last = entry.points[entry.points.length - 1]?.value;
    return `<span class="league-legend-item"><i class="league-legend-swatch league-series-${entry.seriesIndex % 7}"></i><strong>${escapeHTML(entry.label)}</strong><span>${escapeHTML(chartValue(last, options))}</span></span>`;
  }).join('');

  const title = escapeHTML(options.title || 'Comparación de NAV');
  const firstLabel = labels[0] || '';
  const middleLabel = labels[Math.floor((labels.length - 1) / 2)] || '';
  const lastLabel = labels[labels.length - 1] || '';
  const middleY = (geometry.top + geometry.bottom) / 2;

  return `<div class="league-chart-shell">
    <svg class="league-chart enhanced-chart" viewBox="0 0 ${geometry.width} ${geometry.height}" role="img" aria-label="${title}. Mínimo ${chartValue(min, options)}, máximo ${chartValue(max, options)}">
      <line class="chart-grid" x1="${geometry.left}" y1="${geometry.top}" x2="${geometry.right}" y2="${geometry.top}" />
      <line class="chart-grid" x1="${geometry.left}" y1="${middleY.toFixed(2)}" x2="${geometry.right}" y2="${middleY.toFixed(2)}" />
      <line class="chart-grid" x1="${geometry.left}" y1="${geometry.bottom}" x2="${geometry.right}" y2="${geometry.bottom}" />
      ${referenceMarkup.join('')}
      ${markerLines}
      ${polylines}
      ${endLabels}
      <text x="${geometry.left}" y="8" class="chart-label">${escapeHTML(chartValue(max, options))}</text>
      <text x="${geometry.left}" y="63" class="chart-label">${escapeHTML(chartValue(min, options))}</text>
      <text x="${geometry.left}" y="${geometry.axisY}" class="chart-label">${escapeHTML(shortTemporalLabel(firstLabel))}</text>
      <text x="${(geometry.left + geometry.right) / 2}" y="${geometry.axisY}" text-anchor="middle" class="chart-label">${escapeHTML(shortTemporalLabel(middleLabel))}</text>
      <text x="${geometry.right}" y="${geometry.axisY}" text-anchor="end" class="chart-label">${escapeHTML(shortTemporalLabel(lastLabel))}</text>
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
