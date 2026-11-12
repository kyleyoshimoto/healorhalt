"""Tier 2: ask a local vision LLM which element the broken locator now means.

The model gets the pruned element list, a screenshot with a numbered box over each candidate
(set-of-marks), the fingerprint from the last passing run and the step's intent. It must answer
with a HealProposal. A timeout, an unreachable server or a malformed answer all come back as no
proposal, never as an exception, so a slow or missing model can fail a heal but not a run.
"""

import contextlib
import io
import time
from dataclasses import dataclass
from typing import Any, Protocol, cast

import ollama
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field, ValidationError, field_validator

from healorhalt.fingerprint import Fingerprint, Node
from healorhalt.llm.ollama_client import DEFAULT_MODEL
from healorhalt.prune import Mark, PrunedScreen, describe_fingerprint, prune, window_rect

DEFAULT_TIMEOUT = 30.0
"""Seconds before a call is abandoned. The first call of a run also loads the model."""
IMAGE_WIDTH = 600
"""Screenshots are shrunk to this many pixels wide: enough to read, with far fewer image tokens."""
BOX_COLOR = (230, 0, 60)
MAX_ANSWER_TOKENS = 400
"""A HealProposal is well under 200 tokens; anything longer is the model rambling."""
SKIP_THINKING = {"role": "assistant", "content": "<think>\n\n</think>\n\n"}
"""Prefilled reply that closes the reasoning block before it starts.

Ollama 0.40's renderer for qwen3-vl ignores ``think=False`` and lets the model reason for
thousands of tokens (over 100 s per heal on an M-series Mac). Starting the reply with an empty
block skips that; ``think=False`` is still sent for models that honour it.
"""


class HealProposal(BaseModel):
    """The model's answer, validated before anything acts on it.

    Fields are generated in this order, so the model decides whether the element is still there
    before it picks a box, instead of picking one and then justifying it.
    """

    element_still_exists: bool = Field(
        description="False if the element is gone and nothing on screen does the same job"
    )
    candidate_id: int | None = Field(
        description="Number of the box around the element, or null if no box is the element"
    )
    confidence: float = Field(ge=0, le=1, description="How sure you are, from 0 to 1")
    rationale: str = Field(description="Why, in at most 12 words")

    @field_validator("confidence", mode="before")
    @classmethod
    def _percent_to_fraction(cls, value: object) -> object:
        """Models often answer 95 or 100 for "95%"; JSON-schema ranges aren't enforced by
        Ollama's grammar, so a number above 1 and at most 100 is read as a percentage."""
        if isinstance(value, int | float) and 1 < value <= 100:
            return value / 100
        return value


class ChatClient(Protocol):
    def chat(self, **kwargs: Any) -> Any: ...


@dataclass
class LLMCall:
    """One request to the model, as written to the audit log."""

    model: str
    prompt: str
    prompt_tokens_estimate: int
    """The pruned text's estimated size, excluding the image."""
    latency_s: float = 0.0
    response: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error: str | None = None
    proposal: HealProposal | None = None


def draw_marks(png: bytes, marks: list[Mark], window_width: int, width: int = IMAGE_WIDTH) -> bytes:
    """The screenshot shrunk to `width` pixels, with each mark boxed and numbered.

    Frames are in points and screenshots in pixels, so frames scale by image width over window
    width.
    """
    image = Image.open(io.BytesIO(png)).convert("RGB")
    image = image.resize(
        (width, round(image.height * width / image.width)), Image.Resampling.LANCZOS
    )
    scale = width / window_width
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=16)
    for mark in marks:
        x, y, w, h = (round(v * scale) for v in mark.rect)
        draw.rectangle((x, y, x + w, y + h), outline=BOX_COLOR, width=2)
        text = str(mark.id)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        tag_h = bottom - top + 4
        tag_y = y - tag_h if y >= tag_h else y
        draw.rectangle((x, tag_y, x + right - left + 6, tag_y + tag_h), fill=BOX_COLOR)
        draw.text((x + 3, tag_y + 2 - top), text, fill="white", font=font)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def build_prompt(
    locator_name: str, intent: str, action: str, fp: Fingerprint, screen: PrunedScreen
) -> str:
    purpose = f"the element that {intent}" if intent else "an element"
    lines = [
        f"You are repairing a UI test of an iOS app. A test step wants to {action} {purpose} "
        f"(the test calls it {locator_name!r}), but its locator no longer matches anything.",
        "",
        "On the last passing run the element was:",
        f"  {describe_fingerprint(fp)}",
        "",
        "The screenshot shows the screen now, with a numbered red box over each candidate. The "
        "same numbers label these elements (frames are x, y, width x height, in points):",
        screen.elements,
    ]
    if screen.context:
        lines += ["", "Other text on screen, nearest the old position first:", screen.context]
    lines += [
        "",
        "First decide whether the element is still on this screen. Apps rename identifiers, "
        "reword labels, move elements and wrap them in new containers, so some of those may "
        "differ, but the element must still do the same job. It may also be gone: removed, or "
        "this may be a different screen. Being the same type, or nearby, is not enough; a "
        "button that does something else is not the element. If it is gone, answer "
        "element_still_exists false and candidate_id null. Otherwise give the number of its box.",
        "",
        "Answer with the JSON object only. Keep rationale to 12 words or fewer.",
    ]
    return "\n".join(lines)


def make_client(timeout: float = DEFAULT_TIMEOUT, host: str | None = None) -> ChatClient:
    return cast(ChatClient, ollama.Client(host=host, timeout=timeout))


def warm_up(client: ChatClient, model: str = DEFAULT_MODEL) -> None:
    """Load the model now, so the first heal's latency doesn't include loading it."""
    with contextlib.suppress(Exception):  # a cold first heal is slower, not wrong
        client.chat(model=model, messages=[], keep_alive="30m")


def propose(
    fp: Fingerprint,
    root: Node,
    screenshot: bytes | None,
    client: ChatClient,
    locator_name: str,
    intent: str = "",
    action: str = "find",
    model: str = DEFAULT_MODEL,
) -> tuple[PrunedScreen, LLMCall]:
    """Ask the model for the element. The call's `proposal` is None on any failure."""
    screen = prune(fp, root)
    prompt = build_prompt(locator_name, intent, action, fp, screen)
    images: list[bytes] = []
    if screenshot is not None:
        images.append(draw_marks(screenshot, screen.marks, window_rect(root)[2] or 1))
    call = LLMCall(model=model, prompt=prompt, prompt_tokens_estimate=screen.tokens)
    started = time.monotonic()
    try:
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt, "images": images}, SKIP_THINKING],
            format=HealProposal.model_json_schema(),
            think=False,
            options={"temperature": 0, "num_predict": MAX_ANSWER_TOKENS},
            keep_alive="30m",
        )
    except Exception as e:  # any failure to get an answer means no proposal
        call.latency_s = time.monotonic() - started
        call.error = f"{type(e).__name__}: {e}"
        return screen, call
    call.latency_s = time.monotonic() - started
    call.response = str(response.message.content or "")
    call.prompt_tokens = getattr(response, "prompt_eval_count", None)
    call.completion_tokens = getattr(response, "eval_count", None)
    try:
        call.proposal = HealProposal.model_validate_json(call.response)
    except ValidationError as e:
        call.error = f"invalid answer ({e.error_count()} validation errors)"
    return screen, call


def chosen(screen: PrunedScreen, proposal: HealProposal) -> Mark | None:
    """The mark the proposal names, or None if it names none or a number that wasn't drawn."""
    if not proposal.element_still_exists or proposal.candidate_id is None:
        return None
    return next((m for m in screen.marks if m.id == proposal.candidate_id), None)
