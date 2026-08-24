const dataUrl = "data/experiment_observation.json";

const el = (id) => document.getElementById(id);

const fmtNumber = (value, digits = 2) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("es-MX", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
};

const fmtPercent = (value, digits = 1) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(digits)}%`;
};

const fmtPercentAlready = (value, digits = 2) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `${Number(value).toFixed(digits)}%`;
};

const evidenceLabel = (row) => {
  if (row.evidence_status === "RESOLVED") return "Con outcomes";
  return `Esperando ${row.required_market_sessions ?? "—"} sesiones`;
};

function renderModels(payload) {
  const status = el("experimentModelStatus");
  const table = el("prospectiveModelTable");
  const note = el("prospectiveModelNote");
  const weekly = el("weeklyChallengerMetrics");
  if (!status && !table && !note && !weekly) return;

  const models = payload.models || {};
  const horizons = Array.isArray(models.horizons) ? models.horizons : [];
  const evidence = payload.evidence || {};

  if (status) {
    const start = payload.start_session || "sin génesis";
    const last = payload.last_session || "—";
    const sessions = payload.sessions ?? 0;
    status.innerHTML = `<span class="status-pill">${payload.status || "WAITING"}</span> <span class="small">Inicio ${start} · última sesión ${last} · ${sessions} sesiones</span>`;
  }

  if (table) {
    table.innerHTML = horizons.length
      ? horizons.map((row) => {
          const metrics = row.metrics || {};
          const versions = Array.isArray(row.versions) && row.versions.length
            ? row.versions.map((item) => item.model_version || "unknown").join(", ")
            : "—";
          return `<tr>
            <td><strong>${String(row.horizon || "").toUpperCase()}</strong><div class="small">${evidenceLabel(row)}</div></td>
            <td>${versions}</td>
            <td>${metrics.resolved_predictions ?? 0}</td>
            <td>${row.pending_predictions ?? 0}</td>
            <td>${fmtPercentAlready(metrics.mean_abs_error_floor_pct)}</td>
            <td>${fmtPercentAlready(metrics.mean_abs_error_ceiling_pct)}</td>
            <td>${fmtPercent(metrics.realized_range_coverage_rate)}</td>
            <td>${fmtPercent(metrics.m3_week_hit_rate)}</td>
          </tr>`;
        }).join("")
      : `<tr><td colspan="8">La evidencia prospectiva comenzará después de la génesis.</td></tr>`;
  }

  if (note) {
    note.textContent = `${evidence.prediction_count_since_genesis ?? 0} predicciones desde génesis · ${evidence.reconciled_count_since_genesis ?? 0} reconciliadas. ${evidence.note || ""}`;
  }

  if (weekly) {
    const challenger = models.weekly_opportunity_challenger || {};
    const metrics = challenger.validation_metrics || {};
    weekly.innerHTML = `
      <div class="metric-card"><span>Estado</span><strong>${challenger.status || "WAITING"}</strong></div>
      <div class="metric-card"><span>Versión</span><strong>${challenger.version || "—"}</strong></div>
      <div class="metric-card"><span>Rank corr.</span><strong>${fmtNumber(metrics.spearman_rank_correlation, 3)}</strong></div>
      <div class="metric-card"><span>Top quintile lift</span><strong>${fmtPercent(metrics.top_quintile_return_lift, 2)}</strong></div>
    `;
  }
}

async function main() {
  try {
    const response = await fetch(dataUrl, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    renderModels(payload);
  } catch (error) {
    const status = el("experimentModelStatus");
    if (status) status.textContent = `Evidencia prospectiva no disponible: ${error.message}`;
  }
}

main();
