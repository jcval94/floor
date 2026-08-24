export async function loadJSONState(path, fallback = null) {
  try {
    const res = await fetch(path, { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return { ok: true, data: await res.json(), error: null };
  } catch (error) {
    return { ok: false, data: fallback, error: String(error) };
  }
}

export async function loadJSON(path, fallback = null) {
  const result = await loadJSONState(path, fallback);
  return result.data;
}

export function escapeHTML(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

export function bySymbol(rows = []) {
  const out = {};
  rows.forEach((r) => {
    const symbol = String(r?.symbol || '').trim();
    if (!symbol) return;
    out[symbol] = out[symbol] || [];
    out[symbol].push(r);
  });
  return out;
}

export function normalizeState(value) {
  return String(value || 'UNKNOWN').trim().toUpperCase();
}

export function stateTone(value) {
  const state = normalizeState(value);
  if ([
    'RED', 'ALERT', 'ESCALATE', 'CRITICAL', 'HIGH', 'FAIL', 'FAILED', 'BAD',
    'ERROR', 'BLOCKED', 'RETRAIN_REQUIRED', 'SEV1', 'SEV2',
  ].includes(state)) return 'bad';
  if ([
    'YELLOW', 'WARN', 'WARNING', 'MEDIUM', 'PENDING', 'REVIEW', 'UNKNOWN',
    'STALE', 'DEGRADED', 'SEV3', 'REBUILD_SITE_DATA',
  ].includes(state)) return 'warn';
  if ([
    'GREEN', 'OK', 'HEALTHY', 'SUCCESS', 'READY', 'CURRENT', 'SEV4',
  ].includes(state)) return 'ok';
  return 'neutral';
}

export function badge(value, label = null) {
  const state = normalizeState(value);
  const tone = stateTone(state);
  return `<span class="status-badge ${tone}"><span class="status-dot" aria-hidden="true"></span>${escapeHTML(label ?? state)}</span>`;
}

export function fmt(value, digits = 2) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : '—';
}

export function fmtPct(value, digits = 1) {
  const n = Number(value);
  return Number.isFinite(n) ? `${n.toFixed(digits)}%` : '—';
}

export function fmtDateTime(value) {
  if (!value) return 'No disponible';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat('es-MX', {
    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
    timeZoneName: 'short',
  }).format(date);
}

export function horizonLabel(horizon) {
  const key = String(horizon || '').toLowerCase();
  return {
    d1: '1 sesión',
    w1: '5 sesiones',
    q1: '10 sesiones',
    m3: '3 meses',
  }[key] || key.toUpperCase() || '—';
}

export function horizonCode(horizon) {
  const key = String(horizon || '').toLowerCase();
  return key || '—';
}

export function m3WeekHumanLabel(week) {
  const w = Number(week);
  if (!Number.isFinite(w) || w < 1 || w > 13) return 'Timing no disponible';
  return `Semana ${String(w).padStart(2, '0')}`;
}

export function m3ProximityLabel(week) {
  const w = Number(week);
  if (!Number.isFinite(w) || w <= 0) return 'desconocida';
  if (w <= 2) return 'cerca';
  if (w <= 6) return 'media';
  return 'lejana';
}

export function relativeDelta(value, reference) {
  const val = Number(value);
  const ref = Number(reference);
  if (!Number.isFinite(val) || !Number.isFinite(ref) || Math.abs(ref) < 1e-9) return null;
  return ((val - ref) / ref) * 100;
}

export function confidenceLabel(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return { label: 'No disponible', tone: 'neutral' };
  if (n >= 0.8) return { label: 'Alta', tone: 'ok' };
  if (n >= 0.6) return { label: 'Media', tone: 'warn' };
  return { label: 'Baja', tone: 'bad' };
}
