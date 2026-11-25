"""The research's recommended setup, pinned in resources/presets/."""

import json
from dataclasses import dataclass
from functools import lru_cache

from django.conf import settings

from analyses.extraction import prompt_text
from prompts.rendering import PromptRenderError

PRESET_PATH = "presets/recommended_v1.json"


@dataclass(frozen=True)
class StepPreset:
    model: str
    temperature: float
    prompt_name: str
    prompt_version: int
    system_prompt: str
    user_prompt: str
    output_schema: dict
    examples: tuple
    resources: dict
    count: int | None = None


@dataclass(frozen=True)
class Preset:
    version: str
    generation: StepPreset
    labelling: StepPreset


@lru_cache(maxsize=1)
def load():
    data = json.loads((settings.RESOURCES_DIR / PRESET_PATH).read_text(encoding="utf-8"))

    def step(name):
        return StepPreset(**{**data[name], "examples": tuple(data[name]["examples"])})

    return Preset(data["version"], step("generation"), step("labelling"))


def resolver(resources):
    """A resource() that reads only the given name-to-text mapping."""

    def resource(name):
        try:
            return resources[name]
        except KeyError as error:
            raise PromptRenderError(f'resource("{name}") is not part of this setup.') from error

    return resource


@dataclass(frozen=True)
class ExtractorPreset:
    model: str
    temperature: float
    name: str
    version: int
    text: str


def load_extractor():
    """The recommended extractor: the owner's prompt, Sonnet 5.5, temperature 0."""
    return ExtractorPreset(
        model="anthropic/claude-sonnet-5.5",
        temperature=0.0,
        name="System description extractor",
        version=1,
        text=prompt_text(),
    )
