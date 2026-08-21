# Copyright (c) 2026 kogeler
# SPDX-License-Identifier: MIT

SHELL := bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help
.NOTPARALLEL:

ifeq ($(OS),Windows_NT)
PY ?= python
BIN := Scripts
EXE := .exe
PLATFORM := windows
else
PY ?= python3
BIN := bin
EXE :=
PLATFORM := linux
endif

DEVELOPMENT_LOCK := requirements-dev.txt
TEST_LOCK := requirements-test.txt
PACKAGE_LOCK := requirements-package.txt
DOCS_LOCK := requirements-docs.txt
COMPILE := --quiet --strip-extras --allow-unsafe --generate-hashes
LOCK_UPGRADE ?=
LOCK_BOOTSTRAP := pip==26.2.1 setuptools==84.0.0 pip-tools==7.6.1 \
	build==1.5.0 click==8.4.2 packaging==26.3 pyproject-hooks==1.2.0 wheel==0.48.0

ARCH ?= $(shell $(PY) -c "from scripts.build_standalone import standalone_architecture; print(standalone_architecture())" 2>/dev/null)
STANDALONE := dist/joplin-md-sync-$(PLATFORM)-$(ARCH)$(EXE)
VENV := venv
VENV_DEV := venv-dev
VENV_DOCS := venv-docs
VENV_LOCK := venv-lock
VENV_PACKAGE := venv-package
VENV_SMOKE := venv-smoke
VENV_TEST := venv-test
PYTHON := $(VENV)/$(BIN)/python
PYTHON_DEV := $(VENV_DEV)/$(BIN)/python
PYTHON_DOCS := $(VENV_DOCS)/$(BIN)/python
PYTHON_LOCK := $(VENV_LOCK)/$(BIN)/python
PYTHON_PACKAGE := $(VENV_PACKAGE)/$(BIN)/python
PYTHON_TEST := $(VENV_TEST)/$(BIN)/python
DEPS_STAMP := $(VENV)/.deps-installed
DEPS_DEV_STAMP := $(VENV_DEV)/.deps-installed
DEPS_DOCS_STAMP := $(VENV_DOCS)/.deps-installed
DEPS_LOCK_STAMP := $(VENV_LOCK)/.deps-installed
DEPS_PACKAGE_STAMP := $(VENV_PACKAGE)/.deps-installed
DEPS_TEST_STAMP := $(VENV_TEST)/.deps-installed
ARTIFACTS := .artifacts
DEPENDENCY_SNAPSHOT := $(ARTIFACTS)/dependency-snapshot.json
RELEASE_NOTES := $(ARTIFACTS)/release-notes.md
DOCS_SITE_URL := https://joplin-mcp.romancello.net/
DOCS_SCREENSHOTS ?= $(ARTIFACTS)/docs-screenshots
VERSION := $(shell cat .version)
TEST_WORKERS ?= auto
PYTEST_XDIST := -n $(TEST_WORKERS) --dist=worksteal
COVERAGE_MIN ?= 87
RUFF_OUTPUT_FORMAT ?=
RUFF_OUTPUT := $(if $(RUFF_OUTPUT_FORMAT),--output-format=$(RUFF_OUTPUT_FORMAT))
RUFF_SOURCES := src tests tests_live scripts .github/scripts docs/site/hooks.py
PYTHON_SOURCES := src tests tests_live scripts .github/scripts docs/site/hooks.py
ACTIONLINT_IMAGE := docker.io/rhysd/actionlint@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667
CONTAINER ?= $(shell command -v podman 2>/dev/null || command -v docker 2>/dev/null)

.PHONY: help venv venv-dev venv-test venv-package venv-docs venv-lock lock refresh-dependencies freeze \
	freeze-check docs-build docs-audit docs-screenshots docs-serve format-check lint typecheck bandit syntax \
	test test-full test-live test-service-installer audit dependency-snapshot \
	validate-actions release-notes check ci build zipapp standalone checksums \
	package smoke smoke-artifacts smoke-wheel smoke-zipapp smoke-standalone \
	verify-release clean

help: ## list available targets
	@grep -hE '^[a-zA-Z][a-zA-Z0-9_-]*:.*##' $(MAKEFILE_LIST) | \
		awk -F':.*## ' '{printf "  %-20s %s\n", $$1, $$2}'

$(PYTHON):
	$(PY) -m venv $(VENV)

$(DEPS_STAMP): $(PYTHON) pyproject.toml .version
	$(PYTHON) -m pip install --no-deps -e .
	$(PYTHON) -m pip check
	touch $(DEPS_STAMP)

venv: $(DEPS_STAMP) ## runtime venv with the CLI installed editable

$(PYTHON_DEV):
	$(PY) -m venv $(VENV_DEV)

$(DEPS_DEV_STAMP): $(PYTHON_DEV) $(DEVELOPMENT_LOCK)
	$(PYTHON_DEV) -m pip install --quiet --require-hashes --only-binary=:all: \
		--requirement $(DEVELOPMENT_LOCK)
	$(PYTHON_DEV) -m pip check
	touch $(DEPS_DEV_STAMP)

venv-dev: $(DEPS_DEV_STAMP) ## dev venv from the hash-verified lock

$(PYTHON_TEST):
	$(PY) -m venv $(VENV_TEST)

$(DEPS_TEST_STAMP): $(PYTHON_TEST) $(TEST_LOCK)
	$(PYTHON_TEST) -m pip install --quiet --require-hashes --only-binary=:all: \
		--requirement $(TEST_LOCK)
	$(PYTHON_TEST) -m pip check
	touch $(DEPS_TEST_STAMP)

venv-test: $(DEPS_TEST_STAMP) ## test venv from the cross-platform hash lock

$(PYTHON_PACKAGE):
	$(PY) -m venv $(VENV_PACKAGE)

$(DEPS_PACKAGE_STAMP): $(PYTHON_PACKAGE) $(PACKAGE_LOCK)
	$(PYTHON_PACKAGE) -m pip install --quiet --require-hashes --only-binary=:all: \
		--requirement $(PACKAGE_LOCK)
	$(PYTHON_PACKAGE) -m pip check
	touch $(DEPS_PACKAGE_STAMP)

venv-package: $(DEPS_PACKAGE_STAMP) ## packaging venv from the cross-platform hash lock

$(PYTHON_DOCS):
	$(PY) -m venv $(VENV_DOCS)

$(DEPS_DOCS_STAMP): $(PYTHON_DOCS) $(DOCS_LOCK)
	$(PYTHON_DOCS) -m pip install --quiet --require-hashes --only-binary=:all: \
		--requirement $(DOCS_LOCK)
	$(PYTHON_DOCS) -m pip check
	touch $(DEPS_DOCS_STAMP)

venv-docs: $(DEPS_DOCS_STAMP) ## docs venv from the hash-verified lock

$(PYTHON_LOCK):
	$(PY) -m venv $(VENV_LOCK)

$(DEPS_LOCK_STAMP): $(PYTHON_LOCK) Makefile
	$(PYTHON_LOCK) -m pip install --quiet --only-binary=:all: $(LOCK_BOOTSTRAP)
	$(PYTHON_LOCK) -m pip check
	touch $(DEPS_LOCK_STAMP)

venv-lock: $(DEPS_LOCK_STAMP) ## isolated exact-pinned pip-compile resolver

lock: venv-lock ## regenerate all four hash-verified dependency locks
	$(PYTHON_LOCK) -m piptools compile $(COMPILE) $(LOCK_UPGRADE) --extra=dev \
		--output-file=$(DEVELOPMENT_LOCK) pyproject.toml
	$(PYTHON_LOCK) -m piptools compile $(COMPILE) $(LOCK_UPGRADE) --extra=test \
		--output-file=$(TEST_LOCK) pyproject.toml
	$(PYTHON_LOCK) -m piptools compile $(COMPILE) $(LOCK_UPGRADE) --extra=package \
		--output-file=$(PACKAGE_LOCK) pyproject.toml
	$(PYTHON_LOCK) -m piptools compile $(COMPILE) $(LOCK_UPGRADE) --extra=docs \
		--output-file=$(DOCS_LOCK) pyproject.toml

refresh-dependencies: ## re-resolve all locks after updating direct pins
	$(MAKE) lock LOCK_UPGRADE=--upgrade

freeze: refresh-dependencies ## compatibility alias for refresh-dependencies

freeze-check: venv-lock ## reject lock drift without upgrading dependencies
	@temporary="$$(mktemp -d)"; trap 'find "$$temporary" -depth -delete' EXIT; \
		$(PYTHON_LOCK) -m piptools compile $(COMPILE) --extra=dev \
			--output-file="$$temporary/development.txt" pyproject.toml; \
		$(PYTHON_LOCK) -m piptools compile $(COMPILE) --extra=test \
			--output-file="$$temporary/test.txt" pyproject.toml; \
		$(PYTHON_LOCK) -m piptools compile $(COMPILE) --extra=package \
			--output-file="$$temporary/package.txt" pyproject.toml; \
		$(PYTHON_LOCK) -m piptools compile $(COMPILE) --extra=docs \
			--output-file="$$temporary/docs.txt" pyproject.toml; \
		diff -u <(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$$/d' $(DEVELOPMENT_LOCK)) \
			<(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$$/d' "$$temporary/development.txt"); \
		diff -u <(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$$/d' $(TEST_LOCK)) \
			<(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$$/d' "$$temporary/test.txt"); \
		diff -u <(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$$/d' $(PACKAGE_LOCK)) \
			<(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$$/d' "$$temporary/package.txt"); \
		diff -u <(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$$/d' $(DOCS_LOCK)) \
			<(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$$/d' "$$temporary/docs.txt")

docs-build: venv-docs ## build the documentation site with strict checks
	$(VENV_DOCS)/$(BIN)/mkdocs build --strict
	test -s site/sitemap.xml
	test -s site/sitemap.xml.gz
	grep -F '<loc>$(DOCS_SITE_URL)</loc>' site/sitemap.xml

docs-audit: docs-build ## audit generated HTML, routes, links, anchors, canonical URLs, and assets
	$(PY) scripts/audit_docs_site.py --site-dir site --site-url $(DOCS_SITE_URL)

docs-screenshots: docs-audit ## capture representative desktop and mobile renders with Chromium
	$(PY) scripts/capture_docs_site.py --site-dir site --output-dir $(DOCS_SCREENSHOTS)

docs-serve: venv-docs ## serve documentation locally with live reload
	$(VENV_DOCS)/$(BIN)/mkdocs serve

format-check: venv-dev ## check Ruff formatting without changing files
	$(VENV_DEV)/$(BIN)/ruff format --check $(RUFF_SOURCES)

lint: venv-dev ## run Ruff lint checks
	$(VENV_DEV)/$(BIN)/ruff check $(RUFF_OUTPUT) $(RUFF_SOURCES)

typecheck: venv-dev ## run strict mypy checks
	$(VENV_DEV)/$(BIN)/mypy

bandit: venv-dev ## scan runtime Python for common security defects
	$(PYTHON_DEV) -m bandit -q -c pyproject.toml -r src/joplin_md_sync

syntax: venv-dev ## compile Python sources and parse maintained shell scripts
	$(PYTHON_DEV) -m compileall -q $(PYTHON_SOURCES)
	bash -n scripts/joplin_terminal_service/collect_joplin_debug.sh

test: venv-test ## full test suite in parallel
	$(PYTHON_TEST) -m pytest $(PYTEST_XDIST) tests

test-full: venv-test ## full test suite with coverage reports and gate
	mkdir -p $(ARTIFACTS)
	$(PYTHON_TEST) -m pytest $(PYTEST_XDIST) tests \
		--cov=src/joplin_md_sync --cov-report=term-missing \
		--cov-report=xml:$(ARTIFACTS)/coverage.xml --cov-fail-under=$(COVERAGE_MIN)
	$(PYTHON_TEST) -m coverage report --format=markdown > $(ARTIFACTS)/coverage-report.md

test-live: venv-test ## opt-in live MCP and GPT Actions tests; reads ./token
	$(PYTHON_TEST) -m pytest -q tests_live

test-service-installer: venv-test ## Linux headless service installer tests
	$(PYTHON_TEST) -m unittest discover -s scripts/joplin_terminal_service/tests -v

audit: venv-dev ## audit every non-empty exact dependency lock
	$(PYTHON_DEV) -m pip_audit --strict --disable-pip \
		--progress-spinner=off --requirement $(DEVELOPMENT_LOCK)
	$(PYTHON_DEV) -m pip_audit --strict --disable-pip \
		--progress-spinner=off --requirement $(TEST_LOCK)
	$(PYTHON_DEV) -m pip_audit --strict --disable-pip \
		--progress-spinner=off --requirement $(PACKAGE_LOCK)
	$(PYTHON_DEV) -m pip_audit --strict --disable-pip \
		--progress-spinner=off --requirement $(DOCS_LOCK)

dependency-snapshot: ## build GitHub dependency manifests offline
	mkdir -p $(ARTIFACTS)
	$(PY) .github/scripts/dependency_snapshot.py --output $(DEPENDENCY_SNAPSHOT)

validate-actions: ## lint GitHub workflows in an immutable isolated image
	@test -n "$(CONTAINER)" || { printf '%s\n' 'Podman or Docker is required' >&2; exit 1; }
	$(CONTAINER) run --rm --network=none --read-only --cap-drop=all \
		--security-opt=no-new-privileges --volume "$(CURDIR):/repo:ro,z" \
		--workdir /repo $(ACTIONLINT_IMAGE) -config-file .github/actionlint.yaml

release-notes: ## generate the exact current-version GitHub release body
	mkdir -p $(ARTIFACTS)
	$(PY) .github/scripts/release_notes.py --output $(RELEASE_NOTES)

check: lint typecheck bandit syntax test verify-release dependency-snapshot ## local cross-platform gates

ci: lint typecheck bandit syntax test-full test-service-installer verify-release \
	freeze-check docs-audit dependency-snapshot validate-actions audit ## complete Linux CI contract

build: venv-package ## build wheel and sdist into dist/
	rm -rf dist build
	$(PYTHON_PACKAGE) -m build

zipapp: ## build the standalone zipapp
	$(PY) scripts/build_zipapp.py

standalone: venv-package ## build the current platform one-file executable
	$(PYTHON_PACKAGE) scripts/build_standalone.py

checksums: ## write SHA-256 checksums for all files already in dist/
	$(PY) -c "import hashlib, pathlib; root = pathlib.Path('dist'); (root / 'SHA256SUMS.txt').write_text(''.join(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n' for path in sorted(root.iterdir()) if path.name != 'SHA256SUMS.txt'), encoding='ascii')"
	$(PY) -c "print(open('dist/SHA256SUMS.txt', encoding='ascii').read(), end='')"

package: build zipapp ## build all current-platform release artifacts
	$(MAKE) standalone
	$(MAKE) checksums

smoke-wheel: ## install and exercise the already-built wheel
	rm -rf $(VENV_SMOKE)
	$(PY) -m venv $(VENV_SMOKE)
	$(VENV_SMOKE)/$(BIN)/python -m pip install --quiet --no-deps dist/joplin_md_sync-$(VERSION)-py3-none-any.whl
	$(VENV_SMOKE)/$(BIN)/joplin-md-sync version
	$(VENV_SMOKE)/$(BIN)/python -m joplin_md_sync capabilities --json > /dev/null
	$(VENV_SMOKE)/$(BIN)/joplin-md-sync gpt-actions export-openapi --server-url https://joplin.example.invalid --output $(VENV_SMOKE)/chatgpt-action.openapi.json > /dev/null
	$(VENV_SMOKE)/$(BIN)/python -c "import json, pathlib; doc=json.loads(pathlib.Path('$(VENV_SMOKE)/chatgpt-action.openapi.json').read_text()); assert doc['openapi'] == '3.1.0'; assert doc['servers'] == [{'url': 'https://joplin.example.invalid'}]; assert doc['paths']"
	rm -rf $(VENV_SMOKE)

smoke-zipapp: ## exercise the already-built standalone zipapp
	$(PY) dist/joplin-md-sync.pyz version
	$(PY) dist/joplin-md-sync.pyz capabilities --json > /dev/null
	$(PY) dist/joplin-md-sync.pyz gpt-actions export-openapi --server-url https://joplin.example.invalid --output dist/chatgpt-action-zipapp.json > /dev/null
	$(PY) -c "import json, pathlib; doc=json.loads(pathlib.Path('dist/chatgpt-action-zipapp.json').read_text()); assert doc['openapi'] == '3.1.0'; assert doc['servers'] == [{'url': 'https://joplin.example.invalid'}]; assert doc['paths']"
	rm -f dist/chatgpt-action-zipapp.json

smoke-standalone: ## exercise the current-platform native executable
	$(STANDALONE) version
	$(STANDALONE) capabilities --json > /dev/null
	$(STANDALONE) gpt-actions export-openapi --server-url https://joplin.example.invalid --output dist/chatgpt-action-standalone.json > /dev/null
	$(PY) -c "import json, pathlib; doc=json.loads(pathlib.Path('dist/chatgpt-action-standalone.json').read_text()); assert doc['openapi'] == '3.1.0'; assert doc['servers'] == [{'url': 'https://joplin.example.invalid'}]; assert doc['paths']"
	rm -f dist/chatgpt-action-standalone.json

smoke-artifacts: smoke-wheel smoke-zipapp smoke-standalone ## exercise built artifacts

smoke: package smoke-artifacts ## build and exercise all current-platform artifacts

verify-release: ## verify version metadata and optional release tag/inventory
	$(PY) scripts/verify_release.py $(if $(TAG),--tag $(TAG)) $(if $(REQUIRE_ALL_STANDALONES),--require-all-standalones)

clean: ## remove generated environments, artifacts, and caches
	rm -rf $(VENV) $(VENV_DEV) $(VENV_TEST) $(VENV_PACKAGE) $(VENV_DOCS) $(VENV_LOCK) $(VENV_SMOKE) \
		dist build site $(ARTIFACTS) src/*.egg-info .mypy_cache .ruff_cache \
		.pytest_cache .coverage htmlcov
	find . -name __pycache__ -type d -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null || true
