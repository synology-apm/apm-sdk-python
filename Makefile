.PHONY: build whl test check-mcp-coverage check-version-consistency bump-external-versions github-act-simulation record-integration-cassettes smoke-test smoke-test-cli smoke-test-sdk docs clean help
.DEFAULT_GOAL := help

help: ## List available targets
	@grep -E '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  make %-28s %s\n", $$1, $$2}'

test: ## Run the pre-commit checklist (unit + integration tests, lint, mypy, coverage, MCP coverage check, version consistency check)
	uv run pytest tests/unit/ --cov=synology_apm.sdk --cov=synology_apm.cli --cov=synology_apm.mcp --cov=examples --cov-report=term --cov-report=html -n auto -q
	uv run pytest tests/integration/ --record-mode=none --import-mode=importlib -q
	uv run ruff check packages/synology-apm-sdk/src packages/synology-apm-cli/src packages/synology-apm-mcp/src tests examples scripts
	uv run mypy
	uv run python scripts/check_mcp_coverage.py
	uv run python scripts/check_version_consistency.py

check-mcp-coverage: ## Verify SDK ↔ MCP tool coverage (mcp_coverage.toml vs registered tools)
	uv run python scripts/check_mcp_coverage.py

check-version-consistency: ## Verify the three packages share one lockstep version (and SDK dependency pins match it)
	uv run python scripts/check_version_consistency.py

bump-external-versions: ## Rewrite outdated GH Actions pins and upgrade uv.lock within existing constraints (needs network; review `git diff`, then run `make test`)
	uv run python scripts/check_actions_versions.py --write
	uv lock --upgrade

github-act-simulation: ## Locally test release.yml's build+verify-dist jobs via `act` (needs act + Docker; never runs publish-*)
	@V=$$(uv run python -c "import tomllib; print(tomllib.load(open('packages/synology-apm-sdk/pyproject.toml', 'rb'))['project']['version'])"); \
	echo "{\"ref\": \"refs/tags/v$$V\"}" > /tmp/act-release-event.json; \
	act push -W .github/workflows/release.yml -j verify-dist \
		--eventpath /tmp/act-release-event.json \
		--artifact-server-path /tmp/act-artifacts

record-integration-cassettes: ## Record missing integration cassettes against a real APM (needs .env)
	uv run pytest tests/integration/ --record-mode=new_episodes --import-mode=importlib -v

smoke-test: smoke-test-cli smoke-test-sdk ## Run both live smoke tests against the .env-configured APM (CLI + SDK)

smoke-test-cli: ## Run the CLI live smoke test against the .env-configured APM
	uv run python -m tests.smoke.cli

smoke-test-sdk: ## Run the SDK live smoke test against the .env-configured APM
	uv run python -m tests.smoke.sdk

build: test whl ## Run test, then build wheel + sdist → dist/

whl: ## Build wheel + sdist → dist/ (skips test)
	@echo "Cleaning old build artifacts..."
	rm -rf dist/synology-apm-sdk dist/synology-apm-cli dist/synology-apm-mcp
	@echo "Building synology-apm-sdk..."
	uv build --package synology-apm-sdk -o dist/synology-apm-sdk
	@echo "Building synology-apm-cli..."
	uv build --package synology-apm-cli -o dist/synology-apm-cli
	@echo "Building synology-apm-mcp..."
	uv build --package synology-apm-mcp -o dist/synology-apm-mcp
	@echo ""
	@echo "dist/synology-apm-sdk contents:"
	@ls -1 dist/synology-apm-sdk
	@echo ""
	@echo "dist/synology-apm-cli contents:"
	@ls -1 dist/synology-apm-cli
	@echo ""
	@echo "dist/synology-apm-mcp contents:"
	@ls -1 dist/synology-apm-mcp

docs: ## Build Sphinx API docs → docs/_build/html
	$(MAKE) -C docs html

clean: ## Remove dist/ and coverage/docs build artifacts
	rm -rf dist/ htmlcov/
	$(MAKE) -C docs clean
