.PHONY: build whl test record-integration-cassettes smoke-test smoke-test-cli smoke-test-sdk docs clean build-macos build-windows help
.DEFAULT_GOAL := help

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  test                         Run the pre-commit checklist (unit + integration tests, lint, mypy, coverage, skills check)"
	@echo "  record-integration-cassettes Record missing integration cassettes against a real APM (needs .env)"
	@echo "  smoke-test         Run both live smoke tests against the .env-configured APM (CLI + SDK)"
	@echo "  smoke-test-cli     Run the CLI live smoke test against the .env-configured APM"
	@echo "  smoke-test-sdk     Run the SDK live smoke test against the .env-configured APM"
	@echo "  build              Run test, then build wheel + sdist → dist/"
	@echo "  whl                Build wheel + sdist → dist/ (skips test)"
	@echo "  docs               Build Sphinx API docs → docs/_build/html"
	@echo "  clean              Remove dist/"
	@echo "  build-macos        Instructions for macOS binary build"
	@echo "  build-windows      Instructions for Windows binary build"

test:
	uv run pytest tests/unit/ --cov=synology_apm.sdk --cov=synology_apm.cli -q
	uv run pytest tests/integration/ --record-mode=none --import-mode=importlib -q
	uv run ruff check packages/synology-apm-sdk/src packages/synology-apm-cli/src tests examples scripts
	uv run mypy
	uv run python scripts/generate_skills.py --check

record-integration-cassettes:
	uv run pytest tests/integration/ --record-mode=new_episodes --import-mode=importlib -v

smoke-test: smoke-test-cli smoke-test-sdk

smoke-test-cli:
	uv run python -m tests.smoke.cli

smoke-test-sdk:
	uv run python -m tests.smoke.sdk

build: test whl

whl:
	@echo "Cleaning old build artifacts..."
	rm -rf dist/synology-apm-sdk dist/synology-apm-cli
	@echo "Building synology-apm-sdk..."
	uv build --package synology-apm-sdk -o dist/synology-apm-sdk
	@echo "Building synology-apm-cli..."
	uv build --package synology-apm-cli -o dist/synology-apm-cli
	@echo ""
	@echo "dist/synology-apm-sdk contents:"
	@ls -1 dist/synology-apm-sdk
	@echo ""
	@echo "dist/synology-apm-cli contents:"
	@ls -1 dist/synology-apm-cli

docs:
	$(MAKE) -C docs html

clean:
	rm -rf dist/
	$(MAKE) -C docs clean

build-macos:
	@echo "Run on macOS:"
	@echo "  CLI binary:     scripts/build-cli-macos.sh <path-to-synology-apm-cli-wheel>      → dist/binaries/macos/"
	@echo "  Example: scripts/build-cli-macos.sh dist/synology-apm-cli/synology_apm_cli-0.1.0-py3-none-any.whl"

build-windows:
	@echo "Run on Windows (PowerShell):"
	@echo "  CLI binary:     scripts/build-cli-windows.ps1 <path-to-synology-apm-cli-wheel>      → dist/binaries/windows/"
	@echo "  Example: scripts/build-cli-windows.ps1 dist\synology-apm-cli\synology_apm_cli-0.1.0-py3-none-any.whl"
