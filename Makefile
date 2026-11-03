SIM ?= iPhone 17
.PHONY: setup lint fmt typecheck unit test sim bench

setup:
	uv sync
lint:
	uv run ruff check .
	uv run ruff format --check .
fmt:
	uv run ruff check --fix .
	uv run ruff format .
typecheck:
	uv run mypy
unit:
	uv run pytest tests/unit
test: lint typecheck unit
sim:
	xcrun simctl boot "$(SIM)" || true
	open -a Simulator
bench:
	@echo "bench/run_bench.py arrives in Phase 5"
