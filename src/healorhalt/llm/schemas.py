"""pydantic schemas for LLM responses."""

from pydantic import BaseModel, Field


class ScreenElement(BaseModel):
    label: str = Field(description="Visible text of the element")
    kind: str = Field(description="Element type, e.g. button, cell, switch, text")


class ScreenDescription(BaseModel):
    screen_title: str = Field(description="Title shown at the top of the screen")
    elements: list[ScreenElement] = Field(description="Up to 10 visible elements, top to bottom")
