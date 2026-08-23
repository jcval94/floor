async function loadAudit() {
  try {
    const response = await fetch('data/audit.json', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } catch (error) {
    return {
      status: 'BLOCKED',
      publishable_forecasts: false,
      blockers: [`audit.json no disponible: ${String(error)}`],
      warnings: [],
      generated_at: null,
      batch: {},
      models: {},
    };
  }
}

function addStyles() {
  const style = document.createElement('style');
  style.textContent = `
    .publication-audit { margin: 0 0 16px; padding: 12px 14px; border: 1px solid #777; border-radius: 8px; font-size: 14px; line-height: 1.45; }
    .publication-audit.ok { border-color: #1f7a3f; background: rgba(31,122,63,.08); }
    .publication-audit.degraded { border-color: #a16b00; background: rgba(161,107,0,.10); }
    .publication-audit.blocked { border-color: #a12626; background: rgba(161,38,38,.10); }
    .publication-audit strong { display: inline-block; margin-right: 8px; }
    .publication-audit code { overflow-wrap: anywhere; }
    .publication-suppressed { padding: 14px; border: 1px solid #a12626; border-radius: 8px; background: rgba(161,38,38,.08); }
  `;
  document.head.appendChild(style);
}

function textList(items) {
  return Array.isArray(items) && items.length ? items.join(' · ') : '-';
}

function renderBanner(audit) {
  const status = String(audit.status || 'BLOCKED').toUpperCase();
  const tone = status === 'OK' ? 'ok' : status === 'DEGRADED' ? 'degraded' : 'blocked';
  const banner = document.createElement('section');
  banner.className = `publication-audit ${tone}`;

  const title = document.createElement('div');
  const strong = document.createElement('strong');
  strong.textContent = `Publication audit: ${status}`;
  title.appendChild(strong);
  const publish = document.createElement('span');
  publish.textContent = audit.publishable_forecasts ? 'Forecasts publicables' : 'Forecasts suprimidos';
  title.appendChild(publish);
  banner.appendChild(title);

  const meta = document.createElement('div');
  meta.className = 'small';
  const batch = audit.batch || {};
  meta.textContent = `Batch: ${batch.as_of || '-'} · filas ${batch.observed_rows ?? '-'} / ${batch.expected_rows ?? '-'} · commit ${audit.source_commit || '-'}`;
  banner.appendChild(meta);

  if (Array.isArray(audit.blockers) && audit.blockers.length) {
    const block = document.createElement('div');
    block.className = 'small';
    block.textContent = `Bloqueos: ${textList(audit.blockers)}`;
    banner.appendChild(block);
  }
  if (Array.isArray(audit.warnings) && audit.warnings.length) {
    const warning = document.createElement('div');
    warning.className = 'small';
    warning.textContent = `Advertencias: ${textList(audit.warnings)}`;
    banner.appendChild(warning);
  }

  const main = document.querySelector('main');
  if (main) main.prepend(banner);
  else document.body.prepend(banner);
}

function suppressActionablePanels(audit) {
  if (audit.publishable_forecasts) return;
  const message = `Forecasts ocultos por auditoría de publicación: ${textList(audit.blockers)}`;

  const blockContainers = ['forecastCards', 'tickerDetail'];
  blockContainers.forEach((id) => {
    const node = document.getElementById(id);
    if (!node) return;
    node.innerHTML = '';
    const div = document.createElement('div');
    div.className = 'publication-suppressed';
    div.textContent = message;
    node.appendChild(div);
  });

  const tableBodies = ['opps', 'm3TopWeeks', 'tickersTable'];
  tableBodies.forEach((id) => {
    const node = document.getElementById(id);
    if (!node) return;
    node.innerHTML = `<tr><td colspan="20" class="publication-suppressed"></td></tr>`;
    const cell = node.querySelector('td');
    if (cell) cell.textContent = message;
  });
}

async function runAuditOverlay() {
  addStyles();
  const audit = await loadAudit();
  renderBanner(audit);
  suppressActionablePanels(audit);
  if (!audit.publishable_forecasts) {
    const observer = new MutationObserver(() => suppressActionablePanels(audit));
    ['forecastCards', 'tickerDetail', 'opps', 'm3TopWeeks', 'tickersTable'].forEach((id) => {
      const node = document.getElementById(id);
      if (node) observer.observe(node, { childList: true, subtree: true });
    });
    setTimeout(() => observer.disconnect(), 2500);
  }
}

window.addEventListener('load', () => {
  runAuditOverlay();
});
