PYTHON ?= python3
export PYTHONPATH := src

.PHONY: help sanity fast fmt lint types test install clean

help:
	@echo "make sanity   - full sanity-check suite (PRD section 9)"
	@echo "make fast     - formatting, lint and unit tests only"
	@echo "make fmt      - apply ruff formatting"
	@echo "make lint     - ruff lint with autofix"
	@echo "make types    - mypy (if installed)"
	@echo "make test     - unittest discovery across tests/"
	@echo "make install  - editable install with dev extras"
	@echo "make clean    - remove caches and build artefacts"

sanity:
	./scripts/sanity-check

fast:
	./scripts/sanity-check --fast

fmt:
	ruff format .

lint:
	ruff check --fix .

types:
	mypy

test:
	$(PYTHON) -m unittest discover -s tests -t . -v

install:
	$(PYTHON) -m pip install -e '.[dev]'

clean:
	rm -rf build dist .ruff_cache .mypy_cache .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
