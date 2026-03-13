PYTHON ?= python

.PHONY: test run-cycle review-training build-site lint retrain-models init-dbs yahoo-ingest build-training-from-db

test:
	PYTHONPATH=src $(PYTHON) -m pytest

run-cycle:
	PYTHONPATH=src $(PYTHON) -m floor.main run-cycle $${SYMBOLS:+--symbols $${SYMBOLS}} --event $${EVENT:-OPEN}

review-training:
	PYTHONPATH=src $(PYTHON) -m floor.main review-training

build-site:
	PYTHONPATH=src $(PYTHON) -m floor.main build-site

lint:
	@echo "ruff no configurado en entorno actual"


retrain-models:
	bash scripts/retrain_models.sh $${DATASET:-data/training/modelable_dataset.json} data/training $${VERSION:-local} $${TASKS:-value,timing}


yahoo-ingest:
	PYTHONPATH=src $(PYTHON) -m storage.yahoo_ingest --db data/market/market_data.sqlite --range 2y --interval 1d --sleep-seconds 0.4

build-training-from-db:
	PYTHONPATH=src $(PYTHON) -m features.build_training_from_db --db data/market/market_data.sqlite --output data/training/yahoo_market_rows.jsonl
	PYTHONPATH=src $(PYTHON) -m features.run_features --input data/training/yahoo_market_rows.jsonl --output data/training/modelable_dataset.json


init-dbs:
	PYTHONPATH=src $(PYTHON) -c "from pathlib import Path; from storage.market_db import init_market_db; from floor.persistence_db import init_persistence_db; init_market_db(Path('data/market/market_data.sqlite')); init_persistence_db(Path('data/persistence/app.sqlite'))"
