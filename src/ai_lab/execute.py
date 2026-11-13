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
    result = evaluate(rows, manifest, plan)
    out = store.root / "runs" / run_id
    out.mkdir(parents=True, exist_ok=True)
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
        if plan.recipe == "frozen_model_comparison"
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
                "target": "floor_w1",
            }
        )
        for m in result["metrics"]:
            for key in ("mae_floor_pct", "rmse_floor_pct", "fold_mae_std"):
                mlflow.log_metric(f"{m['variant']}.{m['model']}.{key}", m[key])
        result["mlflow_run_id"] = tracked.info.run_id
        (out / "result.json").write_text(canonical(result))
        (out / "report.html").write_text(report(plan, manifest, result))
        mlflow.log_artifacts(str(out))
    artifact_manifest = {
        p.name: digest(p.read_bytes()) for p in out.iterdir() if p.is_file()
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
        '<h2>Referencias y artefactos</h2><p><a href="result.json">JSON</a> · <a href="predictions.json">Predicciones OOS</a> · <a href="provenance.json">Código y fuentes</a></p>'
        "<p>No contiene explicación generada por IA ni hipótesis validada causalmente.</p></html>"
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
