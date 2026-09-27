"""Checks a prompt template without rendering it, so saving needs no scenario."""

from jinja2 import TemplateSyntaxError, meta
from jinja2.sandbox import SandboxedEnvironment

from prompts.rendering import VARIANT_UNBIASED

_ENV = SandboxedEnvironment()
_JINJA_GLOBALS = frozenset(_ENV.globals)
_PHASE2_NAMES = frozenset(
    {
        "seed_systems",
        "fewshots",
        "generate_systems",
        "generate_scenarios",
        "n_systems",
        "n_scenarios_per_system",
        "target_system",
        "resource",
    }
)
_TAXONOMY_NAMES = frozenset({"taxonomy", "hcv_table"})
_PHASE3_NAMES = frozenset({"scenario", "system_description", "fewshots", "resource"})


def provided_names(phase, variant):
    """Every name a template of this phase and variant can use when rendered."""
    if phase == 3:
        names = _PHASE3_NAMES | _TAXONOMY_NAMES
    elif variant == VARIANT_UNBIASED:
        names = _PHASE2_NAMES
    else:  # B, or no variant chosen yet
        names = _PHASE2_NAMES | _TAXONOMY_NAMES
    return names | _JINJA_GLOBALS


def field_syntax_error(field, source):
    """This prompt's syntax error as "User prompt, line 3: ...", or None."""
    try:
        _ENV.parse(source or "")
    except TemplateSyntaxError as error:
        label = "System prompt" if field == "system_prompt" else "User prompt"
        return f"{label}, line {error.lineno}: {error.message}"
    return None


def syntax_error(system_prompt, user_prompt):
    """The first syntax error in either prompt, or None."""
    return field_syntax_error("system_prompt", system_prompt) or field_syntax_error(
        "user_prompt", user_prompt
    )


def unknown_variables(phase, variant, system_prompt, user_prompt):
    """Top-level names the template uses that rendering won't provide.

    Attribute typos (`scenario.narative`) aren't caught; rendering catches those.
    """
    if syntax_error(system_prompt, user_prompt):
        return []
    used = set()
    for source in (system_prompt, user_prompt):
        used |= meta.find_undeclared_variables(_ENV.parse(source or ""))
    return sorted(used - provided_names(phase, variant))
