ifndef PYTHON
ifdef STELLA_PYTHON
PYTHON := $(STELLA_PYTHON)
else
PYTHON = $(shell python3 scripts/check_runtime.py)
endif
endif
ISOLATED = STELLA_PYTHON="$(PYTHON)" "$(PYTHON)" scripts/run_isolated.py

.PHONY: setup setup-legacy dev lint test test-legacy test-desktop test-desktop-upgrade test-integration test-e2e eval audit audit-legacy check check-legacy

setup:
	npm ci --ignore-scripts

setup-legacy:
	"$(PYTHON)" -m pip install -r requirements.txt -r requirements-dev.txt
	npm --prefix desktop ci
	"$(PYTHON)" -m playwright install chromium

dev:
	npm run dev

lint:
	$(ISOLATED) "$(PYTHON)" -m ruff check --select E4,E7,E9,F agent application bridge channel cli common models scripts tests evals translate voice app.py config.py
	$(ISOLATED) npm --prefix desktop run lint
	$(ISOLATED) npm --prefix desktop run typecheck

test:
	npm test

# Retained Python runtime; use only for explicitly scoped legacy maintenance.
test-desktop:
	"$(PYTHON)" product/tests/official_desktop_check.py

test-desktop-upgrade:
	"$(PYTHON)" product/tests/upgrade-compatibility-check.py

test-legacy:
	$(ISOLATED) "$(PYTHON)" -m pytest tests -q

test-integration:
	$(ISOLATED) "$(PYTHON)" -m pytest tests/test_harness_runtime.py tests/test_briefing_pipeline.py tests/test_security_gateway.py tests/test_scheduler_delivery.py -q

test-e2e:
	$(ISOLATED) "$(PYTHON)" scripts/ui_check.py

eval:
	$(ISOLATED) "$(PYTHON)" evals/run.py

audit:
	$(MAKE) check

audit-legacy:
	STELLA_PYTHON="$(PYTHON)" "$(PYTHON)" scripts/quality_check.py full

check:
	npm run product:build
	npm test

check-legacy:
	STELLA_PYTHON="$(PYTHON)" "$(PYTHON)" scripts/quality_check.py incremental
