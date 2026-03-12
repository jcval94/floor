export async function loadJSON(path, fallback = null) {
  try {
    const res = await fetch(path);
    if (!res.ok) throw new Error(res.statusText);
    return await res.json();
  } catch {
    return fallback;
  }
}

export function bySymbol(rows = []) {
  const out = {};
  rows.forEach((r) => {
    const s = r.symbol;
    out[s] = out[s] || [];
    out[s].push(r);
  });
  return out;
}

export function badge(value) {
  return `<span class="badge ${value}">${value}</span>`;
}

export function fmt(n) {
  const x = Number(n);
  return Number.isFinite(x) ? x.toFixed(2) : "-";
}

export function m3WeekHumanLabel(week) {
  const w = Number(week);
  if (!Number.isFinite(w) || w < 1 || w > 13) return 'Semana -';
  return `Semana ${String(w).padStart(2, '0')} (horizonte m3: 1..13 semanas bursátiles relativas)`;
}

export function m3ProximityLabel(week) {
  const w = Number(week);
  if (!Number.isFinite(w) || w <= 0) return 'desconocida';
  if (w <= 2) return 'cerca';
  if (w <= 6) return 'media';
  return 'lejos';
}
