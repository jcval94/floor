from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from features.run_features import build_modelable_dataset
from floor.universe import parse_universe_yaml
from forecasting.run_forecast import run_forecast_pipeline
from models.run_training import run_training as run_m3_training
from models.sync_models_file import sync_champions
from models.train_classic_horizons import run as run_classic_training
from replay.yahoo_source import fetch_rows_with_retries
from utils.model_artifact_guard import validate_registry


def _session_date(value: object) -> date:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _daily_source_rows(
    symbols: list[str],
    *,
    cutoff: date,
    benchmark_symbol: str = "SPY",
    sleep_seconds: float = 0.10,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    benchmark_symbol = benchmark_symbol.upper()
    requested = sorted(set([*(symbol.upper() for symbol in symbols), benchmark_symbol]))
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for symbol in requested:
        rows = fetch_rows_with_retries(symbol, range_="2y", interval="1d")
        filtered = [row for row in rows if _session_date(row["timestamp"]) <= cutoff]
        if not filtered:
            raise RuntimeError(f"no daily data before cutoff symbol={symbol}")
        if max(_session_date(row["timestamp"]) for row in filtered) > cutoff:
            raise RuntimeError(f"cutoff violation symbol={symbol}")
        by_symbol[symbol] = filtered
        time.sleep(sleep_seconds)

    benchmark_close_by_day = {
        _session_date(row["timestamp"]).isoformat(): float(row["close"])
        for row in by_symbol[benchmark_symbol]
    }
    raw: list[dict[str, Any]] = []
    for symbol in symbols:
        symbol = symbol.upper()
        for row in by_symbol[symbol]:
            day = _session_date(row["timestamp"]).isoformat()
            raw.append(
                {
                    "timestamp": row["timestamp"],
                    "symbol": symbol,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0.0) or 0.0),
                    "benchmark_close": benchmark_close_by_day.get(day),
                    "ai_conviction": None,
                    "ai_floor_d1": None,
                    "ai_ceiling_d1": None,
                    "ai_floor_w1": None,
                    "ai_ceiling_w1": None,
                    "ai_floor_q1": None,
                    "ai_ceiling_q1": None,
                    "ai_floor_m3": None,
                    "ai_conviction_long": None,
                    "ai_recency_long": None,
                    "ai_consensus_score": None,
                }
            )
    raw.sort(key=lambda row: (str(row["timestamp"]), str(row["symbol"])))
    if any(_session_date(row["timestamp"]) > cutoff for row in raw):
        raise RuntimeError("physical training cutoff was not enforced")
    return raw, {
        "source": "Yahoo chart range=2y interval=1d",
        "symbols": len(requested),
        "asset_rows": len(raw),
        "benchmark_rows": len(by_symbol[benchmark_symbol]),
        "max_source_session": max(_session_date(row["timestamp"]) for row in raw).isoformat(),
    }


def _latest_feature_rows(dataset: dict[str, Any], symbols: list[str]) -> list[dict[str, Any]]:
    rows = dataset.get("rows")
    if not isinstance(rows, list):
        raise RuntimeError("modelable dataset rows missing")
    latest: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or "").upper()
        if symbol in symbols:
            latest[symbol] = {str(key): value for key, value in raw.items()}
    missing = sorted(set(symbols) - set(latest))
    if missing:
        raise RuntimeError(f"missing smoke feature rows: {missing}")
    return [latest[symbol] for symbol in symbols]


def _artifact_metrics(models_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for task in ("d1", "w1", "q1", "value", "timing"):
        payload = json.loads(
            (models_dir / f"{task}_champion.json").read_text(encoding="utf-8")
        )
        result[task] = {
            "model_name": payload.get("model_name"),
            "version": payload.get("version"),
            "train_rows": payload.get("train_rows")
            or payload.get("metrics", {}).get("train_rows"),
            "validation_rows": payload.get("test_rows")
            or payload.get("metrics", {}).get("validation_rows"),
            "metrics": payload.get("metrics", {}),
        }
    return result


def build_schema2_champions(
    *,
    cutoff: date,
    output_root: Path,
    universe_path: Path,
    version: str,
) -> dict[str, Any]:
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    symbols = parse_universe_yaml(universe_path)
    raw_rows, source_summary = _daily_source_rows(symbols, cutoff=cutoff)

    raw_path = output_root / "cutoff_market_rows.jsonl"
    with raw_path.open("w", encoding="utf-8") as handle:
        for row in raw_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    dataset = build_modelable_dataset([dict(row) for row in raw_rows])
    dataset_path = output_root / "modelable_dataset_cutoff.json"
    dataset_path.write_text(
        json.dumps(dataset, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    dataset_rows = dataset.get("rows")
    if not isinstance(dataset_rows, list) or not dataset_rows:
        raise RuntimeError("cutoff training produced no modelable rows")
    if any(
        _session_date(row.get("timestamp")) > cutoff
        for row in dataset_rows
        if isinstance(row, dict)
    ):
        raise RuntimeError("modelable dataset crossed the physical cutoff")

    models_dir = output_root / "models"
    run_classic_training(
        dataset_path,
        models_dir,
        version,
        tasks=("d1", "w1", "q1"),
        training_mode="manual",
    )
    run_m3_training(
        dataset_path,
        output_root,
        version=version,
        tasks=("value", "timing"),
        training_mode="manual",
        persistence_db_path=output_root / "repair_audit.sqlite",
    )
    sync_champions(
        models_dir,
        output_root / "models_file",
        ["d1", "w1", "q1"],
    )

    registry_summary = validate_registry(models_dir, run_smoke=True)
    smoke_rows = _latest_feature_rows(dataset, [symbol.upper() for symbol in symbols])
    generated = run_forecast_pipeline(
        market_rows=smoke_rows,
        ai_by_symbol={},
        session="CLOSE",
        as_of=datetime.combine(cutoff, datetime.max.time(), tzinfo=timezone.utc),
        model_registry_dir=models_dir,
    )
    forecasts = list(generated.get("dataset_forecasts", []))
    blocked = list(generated.get("blocked_list", []))
    if blocked or len(forecasts) != len(symbols):
        raise RuntimeError(
            f"real serving smoke incomplete forecasts={len(forecasts)} "
            f"expected={len(symbols)} blocked={blocked[:5]}"
        )

    artifact_hashes = {
        path.name: _sha256(path)
        for path in sorted(models_dir.glob("*_champion.json"))
    }
    model_file_hashes = {
        path.name: _sha256(path)
        for path in sorted((output_root / "models_file").glob("*_champion.pkl"))
    }
    split_counts = dataset.get("split_counts", {})
    horizon_coverage = dataset.get("horizon_coverage", {})
    manifest = {
        "schema_version": 1,
        "purpose": "serving schema-v2 compatibility repair",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "training_cutoff": cutoff.isoformat(),
        "replay_window_start": "2026-08-24",
        "future_replay_rows_used_for_training": False,
        "version": version,
        "universe_size": len(symbols),
        "source": source_summary,
        "dataset_rows": len(dataset_rows),
        "split_counts": split_counts,
        "horizon_coverage": horizon_coverage,
        "registry_validation": registry_summary,
        "full_universe_serving_smoke": {
            "forecasts": len(forecasts),
            "blocked": len(blocked),
            "event": "CLOSE",
        },
        "artifact_sha256": artifact_hashes,
        "model_file_sha256": model_file_hashes,
        "artifact_metrics": _artifact_metrics(models_dir),
    }
    manifest_path = output_root / "schema2_repair_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build serving-compatible champions from a physically cutoff dataset"
    )
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--universe", default="config/universe.yaml")
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    manifest = build_schema2_champions(
        cutoff=date.fromisoformat(args.cutoff),
        output_root=Path(args.output_root),
        universe_path=Path(args.universe),
        version=args.version,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
