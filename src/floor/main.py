from __future__ import annotations

import argparse
import logging

from floor.calendar import nearest_event_type
from floor.config import RuntimeConfig
from floor.pipeline.intraday_cycle import run_intraday_cycle
from floor.prediction_reconciliation import reconcile_predictions
from floor.reporting.generate_site_data import build_dashboard_snapshot
from floor.training.review import run_training_review
from floor.universe import parse_universe_yaml

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    parser = argparse.ArgumentParser(prog="floor")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_cycle = sub.add_parser("run-cycle")
    run_cycle.add_argument("--event", default=None)
    run_cycle.add_argument("--symbols", default=None)

    sub.add_parser("review-training")
    sub.add_parser("reconcile-predictions")
    sub.add_parser("build-site")

    args = parser.parse_args()
    cfg = RuntimeConfig.from_env()

    try:
        if args.cmd == "run-cycle":
            event = args.event or nearest_event_type()
            if event is None:
                print("No market session today; skipping run-cycle")
                return
            if args.symbols:
                symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            else:
                symbols = parse_universe_yaml(cfg.root_dir / "config" / "universe.yaml")
            logger.info("[main] running run-cycle event=%s symbols=%s", event, len(symbols))
            run_intraday_cycle(event_type=event, symbols=symbols, cfg=cfg)
            build_dashboard_snapshot(cfg.data_dir, output_path=cfg.data_dir / "reports" / "dashboard.json")
            logger.info("[main] refreshed dashboard snapshot after run-cycle")
        elif args.cmd == "review-training":
            logger.info("[main] running review-training")
            run_training_review(
                data_dir=cfg.data_dir,
                output_path=cfg.data_dir / "training" / "reviews.jsonl",
                summary_path=cfg.data_dir / "training" / "review_summary_latest.json",
                config_path=cfg.root_dir / "config" / "retraining.yaml",
            )
        elif args.cmd == "reconcile-predictions":
            logger.info("[main] running reconcile-predictions")
            reconcile_predictions(cfg.data_dir)
        elif args.cmd == "build-site":
            logger.info("[main] running build-site")
            build_dashboard_snapshot(cfg.data_dir, output_path=cfg.data_dir / "reports" / "dashboard.json")
    except Exception as exc:
        logger.exception("[main] command failed cmd=%s error=%s", args.cmd, exc)
        raise


if __name__ == "__main__":
    main()
