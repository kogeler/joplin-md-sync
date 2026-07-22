# Copyright © 2026 kogeler
# SPDX-License-Identifier: MIT
#
# Two separate local virtual environments:
#   venv/      runtime  — the package itself (editable), zero third-party deps
#   venv-dev/  tooling  — ruff, mypy, pytest, PyInstaller, build
#                          (declared in [dependency-groups],
#                          pinned via pip freeze in requirements-dev.txt)
# CI reuses these targets (see .github/workflows/*.yml).

ifeq ($(OS),Windows_NT)
PY  ?= python
BIN := Scripts
EXE := .exe
PLATFORM := windows
else
PY  ?= python3
BIN := bin
EXE :=
PLATFORM := linux
endif

ARCH             ?= $(shell $(PY) -c "from scripts.build_standalone import standalone_architecture; print(standalone_architecture())" 2>/dev/null)
STANDALONE       := dist/joplin-md-sync-$(PLATFORM)-$(ARCH)$(EXE)
VENV           := venv
VENV_DEV       := venv-dev
VENV_SMOKE     := venv-smoke
PYTHON         := $(VENV)/$(BIN)/python
PYTHON_DEV     := $(VENV_DEV)/$(BIN)/python
DEPS_STAMP     := $(VENV)/.deps-installed
DEPS_DEV_STAMP := $(VENV_DEV)/.deps-installed
VERSION        := $(shell cat .version)
TEST_WORKERS   ?= 4

.PHONY: help venv venv-dev freeze test test-live test-service-installer lint typecheck check build zipapp standalone checksums package smoke smoke-artifacts smoke-wheel smoke-zipapp smoke-standalone verify-release clean

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

test: venv-dev           ## full test suite in parallel (override TEST_WORKERS=N)
	$(PYTHON_DEV) -m pytest -n $(TEST_WORKERS) tests

test-live: venv-dev      ## opt-in live MCP + GPT Actions tests (reads ./token)
	$(PYTHON_DEV) -m pytest -q tests_live

test-service-installer: venv-dev  ## Linux headless service installer tests
	$(PYTHON_DEV) -m unittest discover -s scripts/joplin_terminal_service/tests -v

lint: venv-dev           ## static checks (ruff)
	$(VENV_DEV)/$(BIN)/ruff check src tests tests_live scripts

typecheck: venv-dev      ## strict typing (mypy)
	$(VENV_DEV)/$(BIN)/mypy

check: lint typecheck test  ## cross-platform lint, typing, and package tests

build: venv-dev          ## wheel + sdist into dist/
	rm -rf dist build
	$(PYTHON_DEV) -m build

zipapp:                  ## standalone dist/joplin-md-sync.pyz (stdlib only)
	$(PY) scripts/build_zipapp.py

standalone: venv-dev     ## current platform's one-file executable
	$(PYTHON_DEV) scripts/build_standalone.py

checksums:               ## checksums for all files already in dist/
	$(PY) -c "import hashlib, pathlib; root = pathlib.Path('dist'); (root / 'SHA256SUMS.txt').write_text(''.join(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n' for path in sorted(root.iterdir()) if path.name != 'SHA256SUMS.txt'), encoding='ascii')"
	$(PY) -c "print(open('dist/SHA256SUMS.txt', encoding='ascii').read(), end='')"

package: build zipapp    ## wheel, sdist, pyz, native executable, checksums
	$(MAKE) standalone
	$(MAKE) checksums

smoke-wheel:             ## install the already-built wheel in a clean venv
	rm -rf $(VENV_SMOKE)
	$(PY) -m venv $(VENV_SMOKE)
	$(VENV_SMOKE)/$(BIN)/python -m pip install --quiet dist/joplin_md_sync-$(VERSION)-py3-none-any.whl
	$(VENV_SMOKE)/$(BIN)/joplin-md-sync version
	$(VENV_SMOKE)/$(BIN)/python -m joplin_md_sync capabilities --json > /dev/null
	$(VENV_SMOKE)/$(BIN)/joplin-md-sync gpt-actions export-openapi --server-url https://joplin.example.invalid --output $(VENV_SMOKE)/chatgpt-action.openapi.json > /dev/null
	$(VENV_SMOKE)/$(BIN)/python -c "import json, pathlib; doc=json.loads(pathlib.Path('$(VENV_SMOKE)/chatgpt-action.openapi.json').read_text()); assert doc['openapi'] == '3.1.0'; assert doc['servers'] == [{'url': 'https://joplin.example.invalid'}]; assert doc['paths']"
	rm -rf $(VENV_SMOKE)

smoke-zipapp:            ## run the already-built standalone zipapp
	$(PY) dist/joplin-md-sync.pyz version
	$(PY) dist/joplin-md-sync.pyz capabilities --json > /dev/null
	$(PY) dist/joplin-md-sync.pyz gpt-actions export-openapi --server-url https://joplin.example.invalid --output dist/chatgpt-action-zipapp.json > /dev/null
	$(PY) -c "import json, pathlib; doc=json.loads(pathlib.Path('dist/chatgpt-action-zipapp.json').read_text()); assert doc['openapi'] == '3.1.0'; assert doc['servers'] == [{'url': 'https://joplin.example.invalid'}]; assert doc['paths']"
	rm -f dist/chatgpt-action-zipapp.json

smoke-standalone:        ## run the current platform's native executable
	$(STANDALONE) version
	$(STANDALONE) capabilities --json > /dev/null
	$(STANDALONE) gpt-actions export-openapi --server-url https://joplin.example.invalid --output dist/chatgpt-action-standalone.json > /dev/null
	$(PY) -c "import json, pathlib; doc=json.loads(pathlib.Path('dist/chatgpt-action-standalone.json').read_text()); assert doc['openapi'] == '3.1.0'; assert doc['servers'] == [{'url': 'https://joplin.example.invalid'}]; assert doc['paths']"
	rm -f dist/chatgpt-action-standalone.json

smoke-artifacts: smoke-wheel smoke-zipapp smoke-standalone  ## exercise built artifacts

smoke: package smoke-artifacts  ## build and exercise all release artifacts

verify-release:          ## consistency checks; pass TAG=vX.Y.Z to verify a tag
	$(PY) scripts/verify_release.py $(if $(TAG),--tag $(TAG)) $(if $(REQUIRE_ALL_STANDALONES),--require-all-standalones)

clean:                   ## remove venvs, build artifacts, and caches
	rm -rf $(VENV) $(VENV_DEV) $(VENV_SMOKE) dist build src/*.egg-info \
		.mypy_cache .ruff_cache .coverage
	find . -name __pycache__ -type d -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null || true
