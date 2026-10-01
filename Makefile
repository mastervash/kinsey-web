PY ?= .venv/bin/python

dev-api:
	MW_DATA_DIR=var $(PY) -m maigret.server

dev-web:
	cd web && npm run dev

build:
	cd web && npm ci && npm run build

test:
	$(PY) -m pytest -q tests

verify-sites:
	$(PY) tools/verify_sites.py --limit 500

lint:
	$(PY) -m flake8 --count --select=E9,F63,F7,F82 --show-source --statistics maigret tests
