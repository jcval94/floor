PYTHON ?= python

.PHONY: test run-cycle review-training build-site lint

test:
	PYTHONPATH=src $(PYTHON) -m pytest

run-cycle:
	PYTHONPATH=src $(PYTHON) -m floor.main run-cycle --symbols $${SYMBOLS:-AAPL,MSFT,SPY} --event $${EVENT:-OPEN}

review-training:
	PYTHONPATH=src $(PYTHON) -m floor.main review-training

build-site:
	PYTHONPATH=src $(PYTHON) -m floor.main build-site

lint:
	@echo "ruff no configurado en entorno actual"
