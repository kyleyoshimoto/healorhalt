"""Ollama client with pydantic-validated structured output."""

from pathlib import Path

import ollama
from pydantic import BaseModel

DEFAULT_MODEL = "qwen3-vl:8b"


def ask_structured[T: BaseModel](
    prompt: str,
    schema: type[T],
    images: list[Path] | None = None,
    model: str = DEFAULT_MODEL,
) -> T:
    """Ask a local model for JSON matching `schema` and validate the reply."""
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt, "images": [str(p) for p in images or []]}],
        format=schema.model_json_schema(),
        think=False,
        options={"temperature": 0},
    )
    return schema.model_validate_json(response.message.content or "")
