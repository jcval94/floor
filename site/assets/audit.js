async function loadAudit() {
  try {
    const response = await fetch('data/audit.json', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } catch (error) {
    return {
      status: 'BLOCKED',
      publishable_forecasts: false,
      blockers: [`No se pudo validar audit.json: ${String(error)}`],
      warnings: [],
      generated_at: null,
      batch: {},
      models: {},
    };
  }
}

function textList(items) {
  return Array.isArray(items) && items.length ? items.join(' · ') : 'Sin detalle adicional';
}

function tone(status) {
  const normalized = String(status || 'BLOCKED').toUpperCase();
  if (normalized === 'OK') return 'ok';
  if (normalized === 'DEGRADED') return 'warn';
  return 'bad';
}

function makeStatusBadge(label, status) {
  const span = document.createElement('span');
  span.className = `status-badge ${tone(status)}`;
  const dot = document.createElement('span');
  dot.className = 'status-dot';
  dot.setAttribute('aria-hidden', 'true');
  span.appendChild(dot);
  span.appendChild(document.createTextNode(label));
  return span;
}

function renderBanner(audit) {
  const status = String(audit.status || 'BLOCKED').toUpperCase();
  const page = document.body.dataset.page;
  if (page === 'home' && status === 'OK' && audit.publishable_forecasts) return;

  const banner = document.createElement('section');
  banner.className = `publication-audit trust-strip ${tone(status)}`;
  banner.setAttribute('role', status === 'BLOCKED' ? 'alert' : 'status');

  const left = document.createElement('div');
  const label = !audit.publishable_forecasts
    ? 'Pronósticos no disponibles'
    : status === 'DEGRADED'
      ? 'Publicación con advertencias'
      : 'Datos verificados';
  left.appendChild(makeStatusBadge(label, status));

  const meta = document.createElement('span');
  meta.className = 'trust-time';
  const batch = audit.batch || {};
  meta.textContent = `Batch ${batch.as_of || 'sin fecha'} · ${batch.observed_rows ?? '—'}/${batch.expected_rows ?? '—'} filas`;
  left.appendChild(meta);
  banner.appendChild(left);

  const detail = document.createElement('span');
  detail.className = 'trust-detail';
  if (!audit.publishable_forecasts) detail.textContent = 'Los valores accionables fueron ocultados por controles de integridad.';
  else if (Array.isArray(audit.warnings) && audit.warnings.length) detail.textContent = textList(audit.warnings.slice(0, 2));
  else detail.textContent = 'Contrato de publicación validado.';
  banner.appendChild(detail);

  const main = document.querySelector('main');
  if (main) main.prepend(banner);
}

function suppressedCard(message) {
  const div = document.createElement('div');
  div.className = 'empty-state publication-suppressed';
  const strong = document.createElement('strong');
  strong.textContent = 'Datos temporalmente no disponibles';
  const p = document.createElement('p');
  p.textContent = message;
  div.append(strong, p);
  return div;
}

function suppressActionablePanels(audit) {
  if (audit.publishable_forecasts) return;
  const message = 'El batch más reciente no superó los controles de integridad. No se muestran forecasts hasta contar con una publicación verificable.';

  ['forecastCards', 'tickerDetail'].forEach((id) => {
    const node = document.getElementById(id);
    if (!node) return;
    node.replaceChildren(suppressedCard(message));
  });

  ['opps', 'm3TopWeeks', 'tickersTable', 'forecastTable', 'm3WatchTable', 'forecastSnapshot'].forEach((id) => {
    const node = document.getElementById(id);
    if (!node) return;
    node.innerHTML = '';
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 20;
    td.appendChild(suppressedCard(message));
    tr.appendChild(td);
    node.appendChild(tr);
  });
}

async function runAuditOverlay() {
  const audit = await loadAudit();
  renderBanner(audit);
  suppressActionablePanels(audit);
  if (!audit.publishable_forecasts) {
    const observer = new MutationObserver(() => suppressActionablePanels(audit));
    ['forecastCards', 'tickerDetail', 'tickersTable', 'forecastTable', 'm3WatchTable', 'forecastSnapshot'].forEach((id) => {
      const node = document.getElementById(id);
      if (node) observer.observe(node, { childList: true, subtree: true });
    });
    setTimeout(() => observer.disconnect(), 2500);
  }
}

window.addEventListener('load', runAuditOverlay);
