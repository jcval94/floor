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
