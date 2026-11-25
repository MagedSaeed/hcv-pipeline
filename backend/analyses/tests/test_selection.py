import pytest

from analyses import preset, selection, state
from analyses.selection import SelectionError
from corpus.models import FewShotExample, ResourceFile
from prompts.models import PromptTemplate

pytestmark = pytest.mark.django_db

GUIDED = (
    "Write {{ n_scenarios_per_system }} scenarios.\n{{ target_system.body }}\n{{ hcv_table() }}"
)


def prompt(name="Guided", phase=2, **fields):
    fields = {"variant": "B", "user_prompt": GUIDED, "is_active": True, "version": 1, **fields}
    if phase == 3:
        fields["variant"] = ""
    return PromptTemplate.objects.create(phase=phase, name=name, **fields)


def example(name="Example", phase=3, output='{"hcv_id": "HCV-17"}', text="in"):
    return FewShotExample.objects.create(
        phase=phase, name=name, input_text=text, output_text=output
    )


def settings_for(kind, **changes):
    return {**state.default_settings(kind), **changes}


def named(entries):
    return {entry["name"]: entry for entry in entries}


def test_recommended_is_first_and_identical_rows_merge(v12):
    loaded = preset.load()
    for name, text in loaded.generation.resources.items():
        ResourceFile.objects.create(name=name, text=text)
    prompt(
        "Copy of the research prompt",
        system_prompt=loaded.generation.system_prompt,
        user_prompt=loaded.generation.user_prompt,
    )
    shot = loaded.labelling.examples[0]
    example(shot["name"], output=shot["output"], text=shot["input"])

    found = selection.options(v12)

    prompts = found["generation"]["prompts"]
    assert len(prompts) == 1
    assert prompts[0]["ref"] == "preset"
    assert prompts[0]["recommended"] is True
    assert prompts[0]["name"] == "Zero-shot Variant B"
    assert prompts[0]["takes_count"] is True
    assert prompts[0]["error"] == ""
    examples = found["labelling"]["examples"]
    assert [entry["ref"] for entry in examples] == [f"preset:{index}" for index in range(5)]
    assert all(entry["recommended"] and not entry["error"] for entry in examples)
    assert found["generation"]["defaults"] == state.default_settings("generation")


def test_an_edited_copy_of_the_research_prompt_is_listed_separately(v12):
    loaded = preset.load().generation
    ResourceFile.objects.create(name="hcv-vocabulary-basics", text="an edited vocabulary")
    row = prompt(loaded.prompt_name, user_prompt=loaded.user_prompt)

    refs = [entry["ref"] for entry in selection.options(v12)["generation"]["prompts"]]

    assert refs == ["preset", row.pk]


def test_only_active_variant_b_prompts_are_listed(v12):
    prompt("a", variant="A", user_prompt="Write it.")
    prompt("b")
    prompt("blank guided", variant="")
    prompt("blank unguided", variant="", user_prompt="Write it.")
    prompt("inactive", is_active=False)
    prompt("labelling", phase=3)

    found = selection.options(v12)

    names = named(found["generation"]["prompts"])
    assert set(names) == {"Zero-shot Variant B", "b", "blank guided"}


def test_takes_count_is_false_for_a_prompt_without_one(v12):
    prompt("no count", user_prompt="Write scenarios. {{ hcv_table() }}")
    prompt("with count")

    names = named(selection.options(v12)["generation"]["prompts"])

    assert names["no count"]["takes_count"] is False
    assert names["with count"]["takes_count"] is True


def test_control_prompts_are_noted(v12):
    prompt("Diverse", user_prompt=GUIDED + "\n- is_control: true for a control scenario.")

    names = named(selection.options(v12)["generation"]["prompts"])

    assert names["Diverse"]["note"] == "writes control scenarios, not marked"
    assert names["Zero-shot Variant B"]["note"] == ""


def test_broken_prompt_carries_its_error_and_fails_validate(v12):
    row = prompt("broken", user_prompt='{{ resource("missing") }} {{ hcv_table() }}')

    entry = named(selection.options(v12)["generation"]["prompts"])["broken"]

    assert "missing" in entry["error"]
    with pytest.raises(SelectionError) as raised:
        selection.validate("generation", settings_for("generation", prompt=row.pk), v12)
    assert raised.value.field == "prompt"


def test_foreign_example_carries_its_error_and_fails_validate(v12):
    row = example("foreign", output='{"hcv_id": "HCV-99"}')

    entry = named(selection.options(v12)["labelling"]["examples"])["foreign"]

    assert "HCV-99" in entry["error"]
    with pytest.raises(SelectionError) as raised:
        selection.validate("labelling", settings_for("labelling", examples=[row.pk]), v12)
    assert raised.value.field == "examples"


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"count": 0}, "count"),
        ({"count": 21}, "count"),
        ({"count": 2.5}, "count"),
        ({"count": True}, "count"),
        ({"count": None}, "count"),
        ({"temperature": -0.1}, "temperature"),
        ({"temperature": 2.1}, "temperature"),
        ({"temperature": "warm"}, "temperature"),
        ({"temperature": float("nan")}, "temperature"),
        ({"model": "  "}, "model"),
        ({"model": None}, "model"),
        ({"prompt": 999}, "prompt"),
        ({"prompt": {}}, "prompt"),
        ({"examples": "all"}, "examples"),
        ({"examples": [{}]}, "examples"),
        ({"surprise": 1}, "settings"),
    ],
)
def test_validate_bounds(v12, change, field):
    with pytest.raises(SelectionError) as raised:
        selection.validate("generation", settings_for("generation", **change), v12)

    assert raised.value.field == field


def test_temperature_is_accepted_for_any_model_and_normalised(v12):
    cleaned = selection.validate(
        "labelling", settings_for("labelling", model="any/model", temperature=2), v12
    )

    assert cleaned["model"] == "any/model"
    assert cleaned["temperature"] == 2.0
    assert isinstance(cleaned["temperature"], float)


def test_examples_keep_the_listed_order(v12):
    chosen = ["preset:3", "preset:0"]

    cleaned = selection.validate("labelling", settings_for("labelling", examples=chosen), v12)

    assert cleaned["examples"] == ["preset:0", "preset:3"]


def test_generation_renders_for_the_supplied_system(analysis):
    pinned = selection.pin(analysis, "generation")

    rendered = selection.render_generation(
        pinned, analysis.name, analysis.description, analysis.taxonomy
    )

    assert "Write 8 scenarios against this system." in rendered.user
    assert analysis.description in rendered.user
    assert "HCV-21" in rendered.user
    assert pinned["is_custom"] is False


def test_pin_drops_the_count_of_a_prompt_without_one(analysis):
    row = prompt("no count", user_prompt="Write scenarios. {{ hcv_table() }}")
    analysis.generation_settings = settings_for("generation", prompt=row.pk)

    assert selection.pin(analysis, "generation")["count"] is None


def test_pin_survives_editing_and_deleting_the_research_rows(analysis):
    resource = ResourceFile.objects.create(name="words", text="ORIGINAL WORDS")
    row = prompt("custom", user_prompt=GUIDED + '\n{{ resource("words") }}')
    shot = example("shot", phase=2, output="ORIGINAL SHOT")
    analysis.generation_settings = settings_for("generation", prompt=row.pk, examples=[shot.pk])
    pinned = selection.pin(analysis, "generation")
    before = selection.render_generation(pinned, "T", "D", analysis.taxonomy)

    resource.delete()
    shot.delete()
    row.delete()
    after = selection.render_generation(pinned, "T", "D", analysis.taxonomy)

    assert after == before
    assert "ORIGINAL WORDS" in after.user
    assert pinned["examples"][0]["output"] == "ORIGINAL SHOT"
    assert pinned["is_custom"] is True


def test_deleted_selection_must_be_chosen_again(analysis):
    row = prompt("soon gone")
    analysis.generation_settings = settings_for("generation", prompt=row.pk)
    row.delete()

    with pytest.raises(SelectionError, match="choose"):
        selection.pin(analysis, "generation")


def test_default_labelling_has_five_examples_and_zero_shot_none(analysis):
    scenario = analysis.scenarios.create(scenario_id="sc_01", narrative="PO1 opens the console.")

    def count():
        pinned = selection.pin(analysis, "labelling", [scenario])
        rendered = selection.render_labelling(
            pinned, analysis.name, analysis.description, analysis.taxonomy, scenario
        )
        return rendered.user.count("### Example ")

    assert count() == 5
    analysis.labelling_settings = settings_for("labelling", examples=[])
    assert count() == 0
