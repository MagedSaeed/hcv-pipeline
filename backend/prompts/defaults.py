"""The default prompt templates `seed_prompts` installs as version 1.

The two generation templates are deliberately identical except for one block:
variant B adds the taxonomy, variant A never mentions it. Keeping everything
else word-for-word the same is what lets a difference in output be attributed
to the taxonomy rather than to wording.
"""

GENERATION_SYSTEM_PROMPT = """\
You are a security analyst who writes threat scenarios for smart-city systems. \
You write scenarios in which people — what they do, what they fail to do, and \
what the system relies on them for — are what lets an attack or failure land. \
You write facts, not verdicts, and you never invent details a system \
description does not support."""

_GENERATION_TASK = """\
{% if generate_systems %}\
## Task

Write {{ n_systems }} new smart-city system description{{ "s" if n_systems != 1 }}. \
Each must be a different kind of system from the examples below, described in \
the same concrete style: its components with short ids, the people and \
organisations involved with their roles, how they connect, and the settings \
and procedures in force.
{% if generate_scenarios %}
For each system, write {{ n_scenarios_per_system }} \
scenario{{ "s" if n_scenarios_per_system != 1 }} against it.
{% endif %}\
{% else %}\
## Task

Write {{ n_scenarios_per_system }} scenario{{ "s" if n_scenarios_per_system != 1 }} \
against this system.

### {{ target_system.title }}

{{ target_system.body }}
{% endif %}
{% if seed_systems %}
## Example systems

{% for system in seed_systems %}\
### {{ system.title }}

{{ system.body }}

{% endfor %}\
{% endif %}\
{% if fewshots %}
## Worked examples

These show the expected content and style. Your answer must still use the JSON \
format described at the end.

{% for shot in fewshots %}\
### Example {{ loop.index }}: {{ shot.name }}

Input:

{{ shot.input }}

Output:

{{ shot.output }}

{% endfor %}\
{% endif %}\
"""

_SCENARIO_RULES = """
## How to write a scenario

Fill in the scenario card:

- actor: everyone involved, by their id from the description. Say who acts, and \
who the system depends on to judge or to do a step it does not do itself.
- trigger: the ordinary occasion in one line, then what the people do, as a \
numbered list.
- standing_state: what is already true in the description that makes the acts \
land. Facts only, read from the description, never invented.
- asset: the one element the acts reach, by its id.
- threat: the STRIDE or LINDDUN category.
- outcome: what is now true, with a number in it. No verdict.
- narrative_steps: the scenario as numbered steps, each saying what a person does \
and what the system does in response.

Give scenarios ids sc_01, sc_02, … within each system.
"""

_GUIDED_BLOCK = """
## Human-centric vulnerability taxonomy

Ground every scenario in one or more of these vulnerabilities. Do not write the \
ids or names of the vulnerabilities into the scenarios themselves.

{{ hcv_table() }}
"""

_OUTPUT_RULES = """
## Output

Reply with JSON only.
{% if generate_systems %}\
Put each system in `system_descriptions` as `{"title", "body", "scenarios": [...]}`.
{% else %}\
Put the scenarios in `scenarios`.
{% endif %}\
Each scenario is `{"scenario_id", "actor", "trigger", "standing_state", "asset", \
"threat", "outcome", "narrative_steps": [...]}`.
"""

GENERATION_VARIANT_A = _GENERATION_TASK + _SCENARIO_RULES + _OUTPUT_RULES
GENERATION_VARIANT_B = _GENERATION_TASK + _SCENARIO_RULES + _GUIDED_BLOCK + _OUTPUT_RULES

LABELLING_SYSTEM_PROMPT = """\
You label smart-city threat scenarios with human-centric vulnerabilities. You \
choose only from the closed list you are given, and you label a vulnerability \
only when the scenario text shows it."""

LABELLING_USER_PROMPT = """\
## Vulnerabilities (closed list)

{{ hcv_table() }}

## System

### {{ system_description.title }}

{{ system_description.body }}

{% if fewshots %}\
## Worked examples

{% for shot in fewshots %}\
### Example {{ loop.index }}: {{ shot.name }}

Scenario:

{{ shot.input }}

Labels:

{{ shot.output }}

{% endfor %}\
{% endif %}\
## Scenario {{ scenario.scenario_id }}

- Actor: {{ scenario.actor }}
- Trigger: {{ scenario.trigger }}
- Standing state: {{ scenario.standing_state }}
- Asset: {{ scenario.asset }}
- Threat: {{ scenario.threat }}
- Outcome: {{ scenario.outcome }}

{{ scenario.narrative }}

## Task

List every vulnerability from the closed list that this scenario shows. For \
each, give the `hcv_id`, `evidence` quoting the scenario text that shows it, and \
a one-sentence `rationale`. Use only ids from the list. If none apply, return an \
empty list.

Reply with JSON only: `{"labels": [{"hcv_id", "evidence", "rationale"}]}`.
"""

DEFAULT_TEMPLATES = [
    {
        "phase": 2,
        "name": "default-generation-variant-a",
        "system_prompt": GENERATION_SYSTEM_PROMPT,
        "user_prompt": GENERATION_VARIANT_A,
    },
    {
        "phase": 2,
        "name": "default-generation-variant-b",
        "system_prompt": GENERATION_SYSTEM_PROMPT,
        "user_prompt": GENERATION_VARIANT_B,
    },
    {
        "phase": 3,
        "name": "default-labelling",
        "system_prompt": LABELLING_SYSTEM_PROMPT,
        "user_prompt": LABELLING_USER_PROMPT,
    },
]
