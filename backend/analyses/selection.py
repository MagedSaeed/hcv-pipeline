"""Which prompts and examples an analysis may use, and pinning the chosen ones by content."""

import re
from dataclasses import dataclass, replace
from functools import cached_property
from types import SimpleNamespace

from analyses import preset, state
from analyses.models import AnalysisScenario
from corpus.models import FewShotExample
from prompts import rendering
from prompts.models import PromptTemplate
from prompts.rendering import VARIANT_GUIDED, PromptRenderError, TaxonomyLeak

PRESET_REF = "preset"
CONTROL_NOTE = "writes control scenarios, not marked"
COUNT_RANGE = (1, 20)
TEMPERATURE_RANGE = (0, 2)
MODEL_MAX_LENGTH = 200

_PHASE = {state.GENERATION: 2, state.LABELLING: 3}
_GUIDED_BODY = re.compile(r"hcv_table\(|\btaxonomy\b")
_HCV_ID = re.compile(r"HCV-\d+")
_SAMPLE_SYSTEM = ("Sample system", "A sample system description.")


class SelectionError(ValueError):
    def __init__(self, field, message):
        super().__init__(message)
        self.field = field
        self.message = message


@dataclass(frozen=True)
class _Prompt:
    ref: str | int
    name: str
    version: int
    system_prompt: str
    user_prompt: str
    output_schema: dict
    resources: dict | None = None  # None reads the research ResourceFile rows
    takes_count: bool = False
    error: str = ""
    sample: tuple = ()

    def content(self):
        return {
            "system_prompt": self.system_prompt,
            "user_prompt": self.user_prompt,
            "output_schema": self.output_schema,
        }

    def lookup(self):
        if self.resources is not None:
            return preset.resolver(self.resources)
        return rendering._make_resource(VARIANT_GUIDED)


@dataclass(frozen=True)
class _Example:
    ref: str | int
    name: str
    input: str
    output: str
    error: str = ""


def _template(content):
    return SimpleNamespace(pk=None, **content)


def _shots(examples):
    return [
        SimpleNamespace(
            name=example["name"],
            resolved_input=example["input"],
            resolved_output=example["output"],
            input_file=None,
            output_file=None,
        )
        for example in examples
    ]


def _system(title, description):
    return SimpleNamespace(title=title, body=description, slug="")


def _render(kind, pinned, title, description, taxonomy, resources, scenario=None):
    content = {key: pinned[key] for key in ("system_prompt", "user_prompt", "output_schema")}
    if kind == state.GENERATION:
        context = rendering.build_phase2_context(
            variant=VARIANT_GUIDED,
            seed_systems=[],
            fewshots=_shots(pinned["examples"]),
            generate_systems=False,
            generate_scenarios=True,
            n_systems=1,
            n_scenarios_per_system=pinned["count"],
            target_system=_system(title, description),
            taxonomy=taxonomy,
        )
    else:
        context = rendering.build_phase3_context(
            scenario=scenario,
            system_description=_system(title, description),
            taxonomy=taxonomy,
            fewshots=_shots(pinned["examples"]),
        )
    return rendering.render(_template(content), context, resources=resources)


def render_generation(pinned, title, description, taxonomy):
    lookup = preset.resolver(pinned["resources"])
    return _render(state.GENERATION, pinned, title, description, taxonomy, lookup)


def render_labelling(pinned, title, description, taxonomy, scenario):
    lookup = preset.resolver(pinned["resources"])
    return _render(state.LABELLING, pinned, title, description, taxonomy, lookup, scenario)


def _sample_scenario():
    return AnalysisScenario(scenario_id="sc_01", narrative="A sample scenario.")


class _Catalogue:
    """One step's selectable prompts and examples, Recommended first."""

    def __init__(self, kind, taxonomy):
        self.kind = kind
        self.taxonomy = taxonomy
        self.preset = getattr(preset.load(), kind)

    def _dry_run(self, prompt):
        def render(count):
            pinned = {**prompt.content(), "examples": [], "count": count}
            rendered = _render(
                self.kind,
                pinned,
                *_SAMPLE_SYSTEM,
                self.taxonomy,
                prompt.lookup(),
                _sample_scenario(),
            )
            return rendered.system, rendered.user

        try:
            sample = render(8)
            takes_count = self.kind == state.GENERATION and sample != render(7)
        except (PromptRenderError, TaxonomyLeak) as error:
            return replace(prompt, error=str(error))
        return replace(prompt, sample=sample, takes_count=takes_count)

    @cached_property
    def prompts(self):
        step = self.preset
        recommended = self._dry_run(
            _Prompt(
                PRESET_REF,
                step.prompt_name,
                step.prompt_version,
                step.system_prompt,
                step.user_prompt,
                step.output_schema,
                step.resources,
            )
        )
        found = [recommended]
        rows = PromptTemplate.objects.filter(phase=_PHASE[self.kind], is_active=True)
        for row in rows.order_by("name", "-version"):
            if self.kind == state.GENERATION and not _is_variant_b(row):
                continue
            entry = self._dry_run(
                _Prompt(
                    row.pk,
                    row.name,
                    row.version,
                    row.system_prompt,
                    row.user_prompt,
                    row.output_schema,
                )
            )
            if (
                not entry.error
                and entry.sample == recommended.sample
                and entry.output_schema == recommended.output_schema
            ):
                continue
            found.append(entry)
        return found

    @cached_property
    def examples(self):
        found = [
            _Example(f"{PRESET_REF}:{index}", shot["name"], shot["input"], shot["output"])
            for index, shot in enumerate(self.preset.examples)
        ]
        pinned = {(shot.input, shot.output) for shot in found}
        rows = FewShotExample.objects.filter(phase=_PHASE[self.kind], is_active=True)
        for row in rows.select_related("input_file", "output_file"):
            content = (row.resolved_input, row.resolved_output)
            if content not in pinned:
                found.append(_Example(row.pk, row.name, *content))
        return [replace(shot, error=self._foreign_ids(shot)) for shot in found]

    @cached_property
    def _allowed_ids(self):
        return set(self.taxonomy.hcvs.values_list("hcv_id", flat=True))

    def _foreign_ids(self, example):
        if foreign := sorted(set(_HCV_ID.findall(example.output)) - self._allowed_ids):
            return f"Cites {', '.join(foreign)}, which this taxonomy doesn't have."
        return ""

    def options(self):
        return {
            "prompts": [
                {
                    "ref": entry.ref,
                    "name": entry.name,
                    "version": entry.version,
                    "recommended": entry.ref == PRESET_REF,
                    "takes_count": entry.takes_count,
                    "note": CONTROL_NOTE if "is_control" in entry.user_prompt else "",
                    "error": entry.error,
                }
                for entry in self.prompts
            ],
            "examples": [
                {
                    "ref": entry.ref,
                    "name": entry.name,
                    "recommended": isinstance(entry.ref, str),
                    "error": entry.error,
                }
                for entry in self.examples
            ],
            "defaults": state.default_settings(self.kind),
        }

    def chosen_prompt(self, ref):
        entry = next((entry for entry in self.prompts if entry.ref == ref), None)
        if entry is None:
            raise SelectionError("prompt", "That prompt is no longer available, choose another.")
        if entry.error:
            raise SelectionError("prompt", f"{entry.name} can't be used: {entry.error}")
        return entry

    def chosen_examples(self, refs):
        if not isinstance(refs, list):
            raise SelectionError("examples", "Worked examples must be a list.")
        chosen = [entry for entry in self.examples if entry.ref in refs]
        listed = [entry.ref for entry in chosen]
        if any(ref not in listed for ref in refs):
            raise SelectionError(
                "examples", "A worked example is no longer available, choose again."
            )
        for entry in chosen:
            if entry.error:
                raise SelectionError("examples", f"{entry.name} can't be used: {entry.error}")
        return chosen

    def validate(self, settings):
        fields = set(state.default_settings(self.kind))
        if not isinstance(settings, dict) or set(settings) != fields:
            raise SelectionError("settings", f"Settings must have exactly {sorted(fields)}.")
        model = settings["model"]
        if not isinstance(model, str) or not model.strip() or len(model) > MODEL_MAX_LENGTH:
            raise SelectionError("model", "Choose a model.")
        temperature, (low, high) = settings["temperature"], TEMPERATURE_RANGE
        if type(temperature) not in (int, float) or not low <= temperature <= high:
            raise SelectionError("temperature", f"Temperature must be from {low} to {high}.")
        cleaned = {"model": model.strip(), "temperature": float(temperature)}
        if self.kind == state.GENERATION:
            count, (low, high) = settings["count"], COUNT_RANGE
            if type(count) is not int or not low <= count <= high:
                raise SelectionError(
                    "count", f"Number of scenarios must be a whole number from {low} to {high}."
                )
            cleaned["count"] = count
        cleaned["prompt"] = self.chosen_prompt(settings["prompt"]).ref
        cleaned["examples"] = [entry.ref for entry in self.chosen_examples(settings["examples"])]
        return cleaned


def _is_variant_b(row):
    if row.variant:
        return row.variant == VARIANT_GUIDED
    return bool(_GUIDED_BODY.search(row.user_prompt))


def options(taxonomy):
    return {kind: _Catalogue(kind, taxonomy).options() for kind in _PHASE}


def validate(kind, settings, taxonomy):
    return _Catalogue(kind, taxonomy).validate(settings)


def pin(analysis, kind, scenarios=()):
    """The analysis's selection by content, so later research edits can't change the run."""
    catalogue = _Catalogue(kind, analysis.taxonomy)
    settings = catalogue.validate(getattr(analysis, f"{kind}_settings"))
    prompt = catalogue.chosen_prompt(settings["prompt"])
    pinned = {
        **prompt.content(),
        "prompt_label": f"{prompt.name} v{prompt.version}",
        "model": settings["model"],
        "temperature": settings["temperature"],
        "count": settings.get("count") if prompt.takes_count else None,
        "examples": [
            {"name": shot.name, "input": shot.input, "output": shot.output}
            for shot in catalogue.chosen_examples(settings["examples"])
        ],
        "is_custom": settings != state.default_settings(kind),
        "preset_version": analysis.preset_version,
        "taxonomy_id": analysis.taxonomy_id,
    }
    used, lookup = {}, prompt.lookup()

    def record(name):
        used[name] = lookup(name)
        return used[name]

    title = analysis.prompt_title or analysis.name
    for scenario in list(scenarios) or [_sample_scenario()]:
        try:
            _render(kind, pinned, title, analysis.description, analysis.taxonomy, record, scenario)
        except (PromptRenderError, TaxonomyLeak) as error:
            raise SelectionError("prompt", f"{prompt.name} can't be used: {error}") from error
        if kind == state.GENERATION:
            break
    pinned["resources"] = used
    return pinned
