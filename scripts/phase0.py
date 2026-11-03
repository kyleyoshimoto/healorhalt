"""Phase 0 exit check: boot the simulator, open Settings, capture it, ask qwen3-vl about it."""

import os
from pathlib import Path

from healorhalt.llm.ollama_client import ask_structured
from healorhalt.llm.schemas import ScreenDescription
from healorhalt.session import boot_simulator, capture, ios_session

SIM = os.environ.get("SIM", "iPhone 17")
OUT = Path("artifacts/phase0")


def main() -> None:
    boot_simulator(SIM)
    with ios_session(SIM, "com.apple.Preferences") as driver:
        png, xml = capture(driver, OUT, "settings")
    print(f"saved {png} and {xml}")

    answer = ask_structured(
        "This is an iOS screenshot. Give the screen title and the visible elements.",
        ScreenDescription,
        images=[png],
    )
    (OUT / "settings.json").write_text(answer.model_dump_json(indent=2), encoding="utf-8")
    print(answer.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
