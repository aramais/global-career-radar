PYTHON ?= python3
VENV ?= .venv

.PHONY: setup install test lint run digest export html

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e .[dev]

install:
	pip install -e .

test:
	pytest

lint:
	ruff check src tests

run:
	job-intake run --config config/settings.yaml

digest:
	job-intake digest --config config/settings.yaml

export:
	job-intake export-csv --config config/settings.yaml --output data/shortlisted_jobs.csv

html:
	job-intake render-html --config config/settings.yaml --output data/review.html
