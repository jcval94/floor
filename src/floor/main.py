from __future__ import annotations

import argparse

from floor.calendar import nearest_event_type
from floor.config import RuntimeConfig
from floor.pipeline.intraday_cycle import run_intraday_cycle
from floor.reporting.generate_site_data import build_dashboard_snapshot
from floor.training.review import run_training_review
from floor.universe import parse_universe_yaml


def main() -> None:
    parser = argparse.ArgumentParser(prog="floor")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_cycle = sub.add_parser("run-cycle")
    run_cycle.add_argument("--event", default=None)
    run_cycle.add_argument("--symbols", default=None)

    sub.add_parser("review-training")
    sub.add_parser("build-site")

    args = parser.parse_args()
    cfg = RuntimeConfig.from_env()

    if args.cmd == "run-cycle":
        event = args.event or nearest_event_type()
        if event is None:
            print("No market session today; skipping run-cycle")
            return
        if args.symbols:
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        else:
            symbols = parse_universe_yaml(cfg.root_dir / "config" / "universe.yaml")
        run_intraday_cycle(event_type=event, symbols=symbols, cfg=cfg)
    elif args.cmd == "review-training":
        run_training_review(
            metrics_path=cfg.data_dir / "metrics" / "model_metrics.jsonl",
            output_path=cfg.data_dir / "training" / "reviews.jsonl",
        )
    elif args.cmd == "build-site":
        build_dashboard_snapshot(cfg.data_dir, output_path=cfg.data_dir / "reports" / "dashboard.json")


if __name__ == "__main__":
    main()
