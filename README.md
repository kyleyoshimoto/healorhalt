# HealOrHalt

Self-healing iOS UI tests that repair broken locators automatically, and refuse to heal past real bugs.

**Status:** early development (Phase 0: environment and skeleton).

## Why

Self-healing test tools can quietly turn a real regression green by clicking a look-alike element.
HealOrHalt verifies every heal by replaying the action and checking the next screen, and publishes
an open benchmark whose headline metric is the **false-heal rate**: how often a real bug gets healed
into a passing test.

## How it works

1. **Tier 0:** cache of previously approved heals
2. **Tier 1:** heuristic fingerprint match (label, ID, type, position, neighbors)
3. **Tier 2:** local vision LLM (qwen3-vl via Ollama, $0 API cost)

Every proposal goes through a verdict engine: **HEAL**, **ESCALATE** for human review, or **HALT**
as a likely real bug. The LLM never decides alone.

## Stack

Python 3.12 · uv · pytest · Appium 3 + XCUITest · iOS Simulator · Ollama · SQLite

## License

MIT
