"""Worker child entry point. Inputs resolved by run ID, never shell/code prompts."""

import html
import os
import platform
import sys
import time
from importlib.metadata import version
from pathlib import Path
from ai_lab.contracts import Plan
from ai_lab.store import Store, canonical, digest


def execute(root, run_id):
    store = Store(Path(root))
    run = store.get(run_id)
    plan = Plan.model_validate(run["plan"])
    rows, manifest = store.dataset(plan.dataset_id)
    from ai_lab.recipes import evaluate

    start = time.monotonic()
    out = store.root / "runs" / run_id
    out.mkdir(parents=True, exist_ok=True)
    if plan.recipe == "strategy_comparison":
        from ai_lab.strategy_replay import replay_strategy

        result = replay_strategy(rows, manifest, plan, out / "backtest_ledger")
    else:
        result = evaluate(rows, manifest, plan)
    predictions = result.pop("predictions")
    (out / "predictions.json").write_text(canonical(predictions))
    result["duration_seconds"] = time.monotonic() - start
    result["duration_scope"] = (
        "evaluation_only; full job wall time is recorded by control-plane started/finished"
    )
    result["provenance"] = {
        "as_of": manifest.get("as_of"),
        "source": manifest.get("source"),
        "source_run_id": manifest.get("source_run_id"),
        "snapshot_hash": manifest.get("archive_sha256"),
        "data_status": "EXPERIMENTAL",
        "currency": "USD",
        "model_version": "frozen-FLOOR-heads"
        if plan.recipe in ("frozen_model_comparison", "strategy_comparison")
        else "registered-estimators-v1",
        "model_assets": result.get("model_assets", []),
    }
    result["cloud_cost_usd"] = 0
    result["cost_scope"] = (
        "local measured runtime; cloud not provisioned; electricity excluded"
    )
    import subprocess

    code_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    dirty = bool(
        subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
    )
    # Export exact source/dependency fingerprints; refuse to call a dirty tree a clean release.
    source_hashes = {str(p): digest(p.read_bytes()) for p in Path("src").rglob("*.py")}
    provenance = {
        "git_commit": code_sha,
        "dirty_tree": dirty,
        "source_hashes": source_hashes,
        "acceptance_provenance": run.get("acceptance_provenance"),
        "python": platform.python_version(),
        "dependencies": {
            n: version(n) for n in ["numpy", "scikit-learn", "mcp", "mlflow-skinny"]
        },
        "dataset_manifest": manifest,
        "plan": plan.model_dump(),
        "parent_run_id": None,
        "experiment_id": run["experiment_id"],
        "run_id": run_id,
        "requester": run["actor"],
    }
    (out / "provenance.json").write_text(canonical(provenance))
    # No daemon or legacy file store: SQLite-backed MLflow metadata, local artifacts.
    os.environ["MLFLOW_ENABLE_ASYNC_LOGGING"] = "false"
    os.environ["MLFLOW_DISABLE_TELEMETRY"] = "true"
    import mlflow

    mlflow.set_tracking_uri("sqlite:///" + str(store.root / "mlflow.sqlite"))
    if mlflow.get_experiment_by_name("floor-ai-lab") is None:
        mlflow.create_experiment(
            "floor-ai-lab", artifact_location=(store.root / "mlflow_artifacts").as_uri()
        )
    mlflow.set_experiment("floor-ai-lab")
    with mlflow.start_run(run_name=run_id) as tracked:
        mlflow.set_tags(
            {
                "floor.run_id": run_id,
                "floor.experiment_id": run["experiment_id"],
                "floor.dataset_id": plan.dataset_id,
                "git_commit": code_sha,
            }
        )
        mlflow.log_params(
            {
                "recipe": plan.recipe,
                "seed": plan.seed,
                "folds": plan.folds,
                "target": "portfolio_net_return"
                if plan.recipe == "strategy_comparison"
                else "floor_w1",
            }
        )
        for m in result["metrics"]:
            candidate = m.get("model", m.get("strategy"))
            for key, value in m.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    mlflow.log_metric(f"{m['variant']}.{candidate}.{key}", value)
        result["mlflow_run_id"] = tracked.info.run_id
        (out / "result.json").write_text(canonical(result))
        (out / "report.html").write_text(report(plan, manifest, result))
        mlflow.log_artifacts(str(out))
    artifact_manifest = {
        str(p.relative_to(out)): digest(p.read_bytes())
        for p in out.rglob("*")
        if p.is_file() and p.name != "artifacts.json"
    }
    (out / "artifacts.json").write_text(canonical(artifact_manifest))
    return {
        "metrics": result["metrics"],
        "ranking_leader": result["ranking_leader"],
        "comparability": result["comparability"],
        "dataset_id": plan.dataset_id,
        "mlflow_run_id": result["mlflow_run_id"],
        "duration_seconds": result["duration_seconds"],
        "cloud_cost_usd": 0,
        "provenance": result["provenance"],
        "report": "runs/" + run_id + "/report.html",
        "conclusion": result["conclusion"],
    }


def report(plan, manifest, result):
    esc = html.escape
    if plan.recipe == "strategy_comparison":
        return strategy_report(plan, manifest, result)
    headings = [
        ("Pregunta original", plan.question),
        ("Hipótesis", "Comparación exploratoria preregistrada en el plan."),
        ("Diseño y metodología", canonical(plan.model_dump())),
        ("Dataset", canonical(manifest)),
        ("Comparabilidad", canonical(result["comparability"])),
    ]
    body = "".join(f"<h2>{esc(k)}</h2><pre>{esc(v)}</pre>" for k, v in headings)
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{esc(str(m[k]))}</td>"
            for k in (
                "model",
                "variant",
                "mae_floor_pct",
                "rmse_floor_pct",
                "fold_mae_std",
            )
        )
        + "</tr>"
        for m in result["metrics"]
    )
    extended = ""
    if plan.recipe == "frozen_model_comparison":
        extended = (
            "<h2>Geometría y cobertura congeladas</h2><pre>"
            + esc(canonical(result["metrics"]))
            + "</pre>"
        )
    bars = "".join(
        f'<p>{esc(m["model"] + " / " + m["variant"])} <meter min="0" max="{max(x["mae_floor_pct"] for x in result["metrics"]) * 1.1}" value="{m["mae_floor_pct"]}"></meter> {m["mae_floor_pct"]:.6f}</p>'
        for m in result["metrics"]
    )
    return (
        '<!doctype html><html lang="es"><meta charset="utf-8"><title>FLOOR AI Lab</title>'
        "<style>body{font:16px system-ui;max-width:1050px;margin:40px auto;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}td,th{border:1px solid #ccc;padding:8px}meter{width:220px}</style>"
        "<h1>FLOOR AI Lab — research experimental</h1>"
        + body
        + "<h2>Resultados medidos por código</h2>"
        "<table><tr><th>Modelo</th><th>Variante</th><th>MAE relativo</th><th>RMSE</th><th>Desv. folds</th></tr>"
        + rows
        + "</table>"
        + extended
        + bars
        + "<h2>Auditoría de features</h2><pre>"
        + esc(canonical(result["feature_audit"]))
        + "</pre><h2>Evidencia estadística y limitaciones</h2><pre>"
        + esc(canonical(result["limitations"]))
        + "</pre>"
        "<h2>Costos</h2><p>Infraestructura cloud: $0 en este perfil local. Electricidad excluida. Duración medida de evaluación: "
        + str(result["duration_seconds"])
        + " s.</p>"
        "<h2>Conclusiones e interpretación</h2><p>" + esc(result["conclusion"]) + "</p>"
        "<h2>Recomendación</h2><p>Validar estabilidad en un holdout futuro antes de seleccionar o promover. No operar capital real.</p>"
        '<h2>Referencias y artefactos</h2><p><a href="result.json">JSON</a> · <a href="predictions.json">Predicciones de evaluación</a> · <a href="provenance.json">Código y fuentes</a></p>'
        "<p>No contiene explicación generada por IA ni hipótesis validada causalmente.</p></html>"
    )


def equity_svg(curves):
    """Exact static NAV chart, generated solely from measured engine points."""
    esc = html.escape
    values = [float(p["nav"]) for points in curves.values() for p in points]
    if not values:
        return ""
    low, high = min(values), max(values)
    padding = max((high - low) * 0.05, 1.0)
    low, high = low - padding, high + padding
    colors = ("#1d4ed8", "#b45309", "#15803d", "#7e22ce")
    body = '<svg role="img" aria-label="NAV medido en USD por sesión" viewBox="0 0 1000 420" style="width:100%;height:auto">'
    body += '<line x1="60" x2="940" y1="330" y2="330" stroke="#555"/>'
    body += f'<text x="5" y="35">{high:.2f}</text><text x="5" y="330">{low:.2f}</text>'
    for i, (name, points) in enumerate(curves.items()):
        color = colors[i % len(colors)]
        coords = " ".join(
            f"{60 + 880 * j / max(len(points) - 1, 1):.3f},{330 - 300 * (float(p['nav']) - low) / (high - low):.3f}"
            for j, p in enumerate(points)
        )
        body += f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{coords}"/>'
        body += f'<text x="{60 + (i % 2) * 440}" y="{365 + (i // 2) * 25}" fill="{color}">{esc(name)}</text>'
    return body + "</svg>"


def strategy_report(plan, manifest, result):
    esc = html.escape
    columns = (
        "strategy",
        "net_return",
        "max_drawdown",
        "turnover",
        "trades",
        "costs_paid",
        "vs_spy",
    )
    rows = "".join(
        "<tr>"
        + "".join("<td>" + esc(str(m.get(k))) + "</td>" for k in columns)
        + "</tr>"
        for m in result["metrics"]
    )
    sections = [
        ("Pregunta", plan.question),
        ("Diseño", canonical(plan.model_dump())),
        ("Dataset", canonical(manifest)),
        ("Comparabilidad", canonical(result["comparability"])),
        ("Costos autoritativos", canonical(result["cost_contract"])),
        ("Curvas NAV medidas", canonical(result["equity_curves"])),
        ("Motivos de decisión", canonical(result.get("decision_reason_counts", {}))),
        ("Limitaciones", canonical(result["limitations"])),
        ("Conclusión", result["conclusion"]),
    ]
    body = "".join(
        "<h2>" + esc(k) + "</h2><pre>" + esc(v) + "</pre>" for k, v in sections
    )
    return (
        '<!doctype html><html lang="es"><meta charset="utf-8"><title>FLOOR Strategy Research</title>'
        "<style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}td,th{border:1px solid #ccc;padding:8px}</style>"
        "<h1>Comparación retrospectiva de estrategias — simulación aislada</h1>"
        "<p>Retornos y drawdown son fracciones; costos monetarios en USD. OOS no verificado. Sin broker PAPER/LIVE.</p>"
        "<table><tr>"
        + "".join("<th>" + esc(k) + "</th>" for k in columns)
        + "</tr>"
        + rows
        + "</table><h2>NAV medido (USD)</h2>"
        + equity_svg(result["equity_curves"])
        + body
        + '<h2>Evidencia</h2><a href="result.json">JSON completo</a> · <a href="provenance.json">Provenance</a> · <a href="backtest_ledger/history.jsonl">Ledger hash-chain</a> · <a href="backtest_ledger/decisions.json">Decisiones</a></html>'
    )


if __name__ == "__main__":
    try:
        result = execute(sys.argv[1], sys.argv[2])
        path = Path(sys.argv[1]) / "runs" / sys.argv[2] / "completion.json"
        path.write_text(canonical(result))
    except Exception as exc:
        # Full exceptions may contain provider secrets; keep a safe structured code.
        path = Path(sys.argv[1]) / "runs" / sys.argv[2] / "failure.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            canonical(
                {
                    "code": type(exc).__name__,
                    "stage": "evaluation_or_tracking",
                    "recoverable": False,
                }
            )
        )
        raise SystemExit(1)
