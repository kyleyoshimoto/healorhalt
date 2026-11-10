SIM ?= iPhone 17
SIM_OS ?= 26.4
export SIM SIM_OS
.PHONY: setup lint fmt typecheck unit test sim bench phase0 app demo phase1 record phase2

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
	uv run python -c "from healorhalt.session import boot_simulator, resolve_udid; \
		boot_simulator(resolve_udid('$(SIM)', '$(SIM_OS)'))"
bench:
	@echo "bench/run_bench.py arrives in Phase 5"

phase0:
	uv run python scripts/phase0.py

APP_DIR := apps/demo-ios
app:
	cd $(APP_DIR) && xcodegen generate --quiet
	xcodebuild -project $(APP_DIR)/Pantry.xcodeproj -scheme Pantry -configuration Debug \
		-destination 'generic/platform=iOS Simulator' -derivedDataPath $(APP_DIR)/build -quiet build

# The demo suite needs a running Appium server (`appium`) and a built app (`make app`).
demo:
	uv run pytest tests/demo --heal=off
# Phase 1 exit check: the demo suite passes three runs in a row.
phase1:
	for run in 1 2 3; do echo "== run $$run"; uv run pytest tests/demo --heal=off -q || exit 1; done
# Save fingerprints of every element the demo suite finds; healing scores against these.
record:
	uv run pytest tests/demo --heal=off --record -q
# Phase 2 exit check: at least 8 of the 10 benign mutations recover with --heal=heuristic.
phase2:
	uv run python scripts/phase2.py
