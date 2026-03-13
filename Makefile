PYTHON ?= python

.PHONY: test run-cycle review-training build-site lint retrain-models

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
	bash scripts/retrain_models.sh $${DATASET:-data/training/modelable_dataset.json} data/training $${VERSION:-local}
