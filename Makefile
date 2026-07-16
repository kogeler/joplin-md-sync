# Copyright © 2026 kogeler
# SPDX-License-Identifier: MIT
#
# Two separate local virtual environments:
#   venv/      runtime  — the package itself (editable), zero third-party deps
#   venv-dev/  tooling  — ruff, mypy, build (declared in [dependency-groups],
#                          pinned via pip freeze in requirements-dev.txt)
# CI reuses these targets (see .github/workflows/*.yml).

ifeq ($(OS),Windows_NT)
PY  ?= python
BIN := Scripts
else
PY  ?= python3
BIN := bin
endif

VENV           := venv
VENV_DEV       := venv-dev
VENV_SMOKE     := venv-smoke
PYTHON         := $(VENV)/$(BIN)/python
PYTHON_DEV     := $(VENV_DEV)/$(BIN)/python
DEPS_STAMP     := $(VENV)/.deps-installed
DEPS_DEV_STAMP := $(VENV_DEV)/.deps-installed
VERSION        := $(shell cat .version)

.PHONY: help venv venv-dev freeze test lint typecheck check build zipapp package smoke verify-release clean

help:                    ## list available targets
	@grep -hE '^[a-zA-Z][a-zA-Z0-9_-]*:.*##' $(MAKEFILE_LIST) | \
		awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

$(PYTHON):
	$(PY) -m venv $(VENV)

$(DEPS_STAMP): $(PYTHON) pyproject.toml .version
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e .
	touch $(DEPS_STAMP)

venv: $(DEPS_STAMP)      ## runtime venv with the CLI installed (editable)

$(PYTHON_DEV):
	$(PY) -m venv $(VENV_DEV)

$(DEPS_DEV_STAMP): $(PYTHON_DEV) requirements-dev.txt
	$(PYTHON_DEV) -m pip install --upgrade pip
	$(PYTHON_DEV) -m pip install -r requirements-dev.txt
	touch $(DEPS_DEV_STAMP)

venv-dev: $(DEPS_DEV_STAMP)  ## dev venv from the pinned requirements-dev.txt lock

freeze:                  ## re-resolve [dependency-groups] dev and refresh the lock
	rm -rf $(VENV_DEV)
	$(PY) -m venv $(VENV_DEV)
	$(PYTHON_DEV) -m pip install --upgrade pip
	$(PYTHON_DEV) -m pip install --group dev
	$(PYTHON_DEV) -m pip freeze > requirements-dev.txt
	touch $(DEPS_DEV_STAMP)

test: venv-dev           ## full unittest suite (unit + integration, fake Joplin)
	$(PYTHON_DEV) -m unittest discover -s tests -v

lint: venv-dev           ## static checks (ruff)
	$(VENV_DEV)/$(BIN)/ruff check src tests scripts

typecheck: venv-dev      ## strict typing (mypy)
	$(VENV_DEV)/$(BIN)/mypy

check: lint typecheck test  ## everything CI runs before building

build: venv-dev          ## wheel + sdist into dist/
	$(PYTHON_DEV) -m build

zipapp:                  ## standalone dist/joplin-md-sync.pyz (stdlib only)
	$(PY) scripts/build_zipapp.py

package: build zipapp    ## all release artifacts + SHA-256 checksums
	cd dist && $(PY) -c "import hashlib, pathlib; \
		print('\n'.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}' \
		for p in sorted(pathlib.Path().iterdir()) if p.name != 'SHA256SUMS.txt'))" \
		> SHA256SUMS.txt
	cat dist/SHA256SUMS.txt

smoke: package           ## install the built wheel into a clean venv and exercise the CLI
	rm -rf $(VENV_SMOKE)
	$(PY) -m venv $(VENV_SMOKE)
	$(VENV_SMOKE)/$(BIN)/python -m pip install --quiet dist/joplin_md_sync-$(VERSION)-py3-none-any.whl
	$(VENV_SMOKE)/$(BIN)/joplin-md-sync version
	$(VENV_SMOKE)/$(BIN)/python -m joplin_md_sync capabilities --json > /dev/null
	$(PY) dist/joplin-md-sync.pyz version
	rm -rf $(VENV_SMOKE)

verify-release:          ## consistency checks; pass TAG=vX.Y.Z to verify a tag
	$(PY) scripts/verify_release.py $(if $(TAG),--tag $(TAG))

clean:                   ## remove venvs, build artifacts, and caches
	rm -rf $(VENV) $(VENV_DEV) $(VENV_SMOKE) dist build src/*.egg-info \
		.mypy_cache .ruff_cache .coverage
	find . -name __pycache__ -type d -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null || true
