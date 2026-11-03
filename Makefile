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

UV ?= uv
PROJECT_PYTHON := $(shell $(PY) -c "import sys; print(sys.executable)")

ARCH ?= $(shell $(PY) -c "from scripts.build_standalone import standalone_architecture; print(standalone_architecture())" 2>/dev/null)
STANDALONE := dist/joplin-md-sync-$(PLATFORM)-$(ARCH)$(EXE)
# Environments are private to one OS machine identity and local user, so one
# checkout on a shared or network drive can serve several hosts.
VENV_ROOT := $(shell $(PY) -I scripts/venv_root.py)
ifeq ($(VENV_ROOT),)
$(error Cannot select machine-specific environments with '$(PY) -I scripts/venv_root.py'; it needs a working Python and a valid OS machine ID)
endif
VENV := $(VENV_ROOT)/venv
VENV_DEV := $(VENV_ROOT)/venv-dev
VENV_DOCS := $(VENV_ROOT)/venv-docs
VENV_PACKAGE := $(VENV_ROOT)/venv-package
VENV_SMOKE := $(VENV_ROOT)/venv-smoke
VENV_TEST := $(VENV_ROOT)/venv-test
PYTHON := $(VENV)/$(BIN)/python
PYTHON_DEV := $(VENV_DEV)/$(BIN)/python
PYTHON_DOCS := $(VENV_DOCS)/$(BIN)/python
PYTHON_PACKAGE := $(VENV_PACKAGE)/$(BIN)/python
PYTHON_TEST := $(VENV_TEST)/$(BIN)/python
ARTIFACTS := .artifacts
RELEASE_NOTES := $(ARTIFACTS)/release-notes.md
DOCS_SITE_URL := https://joplin-mcp.romancello.net/
DOCS_SCREENSHOTS ?= $(ARTIFACTS)/docs-screenshots
VERSION := $(shell cat .version)
SOURCE_DATE_EPOCH ?= $(shell git log -1 --format=%ct 2>/dev/null || printf '315532800')
TEST_WORKERS ?= auto
PYTEST_XDIST := -n $(TEST_WORKERS) --dist=worksteal
COVERAGE_MIN ?= 87
RUFF_OUTPUT_FORMAT ?=
RUFF_OUTPUT := $(if $(RUFF_OUTPUT_FORMAT),--output-format=$(RUFF_OUTPUT_FORMAT))
RUFF_SOURCES := src tests tests_live scripts .github/scripts docs/site/hooks.py
PYTHON_SOURCES := src tests tests_live scripts .github/scripts docs/site/hooks.py
ACTIONLINT_IMAGE := docker.io/rhysd/actionlint@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667
CONTAINER ?= $(shell command -v podman 2>/dev/null || command -v docker 2>/dev/null)

.PHONY: help venv venv-dev venv-test venv-package venv-docs lock lock-check refresh-dependencies \
	docs-build docs-audit docs-screenshots docs-serve format-check lint typecheck bandit syntax \
	test test-full test-live test-live-stdio-standalone test-service-installer test-service-live audit \
	validate-actions release-notes check ci build zipapp standalone checksums \
	package smoke smoke-artifacts smoke-wheel smoke-sdist smoke-zipapp smoke-standalone \
	verify-release clean

help: ## list available targets
	@grep -hE '^[a-zA-Z][a-zA-Z0-9_-]*:.*##' $(MAKEFILE_LIST) | \
		awk -F':.*## ' '{printf "  %-20s %s\n", $$1, $$2}'

venv: ## runtime-only editable installation from the project lock
	UV_PROJECT_ENVIRONMENT="$(VENV)" $(UV) sync --locked --python "$(PROJECT_PYTHON)" --no-default-groups

venv-dev: ## quality tools, without installing the project or other groups
	UV_PROJECT_ENVIRONMENT="$(VENV_DEV)" $(UV) sync --locked --python "$(PROJECT_PYTHON)" --only-group quality --no-build

venv-test: ## portable test tools from the same universal lock
	UV_PROJECT_ENVIRONMENT="$(VENV_TEST)" $(UV) sync --locked --python "$(PROJECT_PYTHON)" --only-group test --no-build

venv-package: ## release tools and build backend from the same lock
	UV_PROJECT_ENVIRONMENT="$(VENV_PACKAGE)" $(UV) sync --locked --python "$(PROJECT_PYTHON)" --only-group build --no-build

venv-docs: ## documentation tools from the same lock
	UV_PROJECT_ENVIRONMENT="$(VENV_DOCS)" $(UV) sync --locked --python "$(PROJECT_PYTHON)" --only-group docs --no-build

lock: ## update uv.lock after editing project requirements
	$(UV) lock

lock-check: ## let uv validate the committed lock without rewriting it
	$(UV) lock --check

refresh-dependencies: ## upgrade the complete graph within declared requirements
	$(UV) lock --upgrade

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

test-live: venv-test ## live protocols against ephemeral Joplin Desktop 3.6.15 (Linux AMD64)
	$(PYTHON_TEST) -m pytest -q tests_live

test-live-stdio-standalone: standalone venv-test ## native stdio acceptance against ephemeral Joplin Desktop
	JOPLIN_MD_SYNC_LIVE_EXECUTABLE="$(abspath $(STANDALONE))" \
		$(PYTHON_TEST) -m pytest -q tests_live/test_mcp_stdio_live.py

test-service-installer: venv-test ## Linux headless service installer tests
	$(PYTHON_TEST) -m unittest discover -s scripts/joplin_terminal_service/tests -v

test-service-live: ## real Terminal installation and offline S3 round trip in an isolated container
	@test -n "$(CONTAINER)" || { printf '%s\n' 'Podman or Docker is required' >&2; exit 1; }
	$(CONTAINER) build -f scripts/joplin_terminal_service/acceptance/Dockerfile \
		-t localhost/joplin-service-acceptance scripts/joplin_terminal_service
	$(CONTAINER) run --rm --network=none \
		-v "$(CURDIR):/workspace:ro" localhost/joplin-service-acceptance

audit: venv-dev ## audit a native export of every locked dependency group
	mkdir -p $(ARTIFACTS)/audit
	$(UV) export --locked --all-groups --no-emit-project --format pylock.toml --output-file $(ARTIFACTS)/audit/pylock.toml > /dev/null
	$(PYTHON_DEV) -m pip_audit --locked $(ARTIFACTS)/audit --strict --progress-spinner=off

validate-actions: ## lint GitHub workflows in an immutable isolated image
	@test -n "$(CONTAINER)" || { printf '%s\n' 'Podman or Docker is required' >&2; exit 1; }
	$(CONTAINER) run --rm --network=none --read-only --cap-drop=all \
		--security-opt=no-new-privileges --volume "$(CURDIR):/repo:ro,z" \
		--workdir /repo $(ACTIONLINT_IMAGE) -config-file .github/actionlint.yaml

release-notes: ## generate the exact current-version GitHub release body
	mkdir -p $(ARTIFACTS)
	$(PY) .github/scripts/release_notes.py --output $(RELEASE_NOTES)

check: lock-check lint typecheck bandit syntax test verify-release ## local source and native lock gates

ci: lint typecheck bandit syntax test-full test-service-installer verify-release \
	lock-check docs-audit validate-actions audit ## complete Linux CI contract

build: venv-package ## reproducibly build wheel and sdist into dist/
	rm -rf dist build
	SOURCE_DATE_EPOCH="$(SOURCE_DATE_EPOCH)" $(PYTHON_PACKAGE) -m build --no-isolation
	$(PY) scripts/normalize_sdist.py --epoch "$(SOURCE_DATE_EPOCH)" \
		dist/joplin_md_sync-$(VERSION).tar.gz

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

smoke-sdist: ## install and exercise the already-built source distribution
	rm -rf $(VENV_SMOKE)
	$(PY) -m venv $(VENV_SMOKE)
	$(VENV_SMOKE)/$(BIN)/python -m pip install --quiet --no-deps \
		dist/joplin_md_sync-$(VERSION).tar.gz
	$(VENV_SMOKE)/$(BIN)/joplin-md-sync version
	$(VENV_SMOKE)/$(BIN)/python -m joplin_md_sync capabilities --json > /dev/null
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

smoke-artifacts: smoke-wheel smoke-sdist smoke-zipapp smoke-standalone ## exercise built artifacts

smoke: package smoke-artifacts ## build and exercise all current-platform artifacts

verify-release: ## verify version metadata and optional release tag/inventory
	$(PY) scripts/verify_release.py $(if $(TAG),--tag $(TAG)) $(if $(REQUIRE_ALL_STANDALONES),--require-all-standalones)

clean: ## remove this host's environments, generated artifacts, and caches
	rm -rf $(VENV) $(VENV_DEV) $(VENV_TEST) $(VENV_PACKAGE) $(VENV_DOCS) $(VENV_SMOKE) \
		dist build site $(ARTIFACTS) src/*.egg-info .mypy_cache .ruff_cache \
		.pytest_cache .coverage htmlcov
	rmdir $(VENV_ROOT) .venvs 2>/dev/null || true
	find . \( -path ./.git -o -path ./.venvs -o -path './venv*' -o -path ./.venv \) -prune \
		-o -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
