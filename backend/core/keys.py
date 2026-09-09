"""Single source of truth for resolving and masking the OpenRouter API key.

Resolution order, highest priority first:

1. ``AppSettings.openrouter_key`` — set by an admin in the running app, so a
   key can be rotated without a redeploy.
2. ``settings.OPENROUTER_API_KEY`` — the environment, which is the bootstrap
   path for a fresh deployment and for CI.
"""

import re

from django.conf import settings

from core.models import AppSettings

MASK_VISIBLE = 4

# What an OpenRouter key LOOKS like, shared by `llm.client.OpenRouterClient
# ._guard` (refuses to send a request carrying one) and
# `pipeline.redaction.scrub` (refuses to archive one) — both import this
# SAME compiled pattern rather than declaring their own copy. They used to
# be two independent hand-written forms and drifted apart: one gained a left
# word-boundary first, and the other kept matching INSIDE ordinary words
# ("a risk-or-benefit tradeoff", "ask-or-tell disclosure" — both plausible
# in a corpus about security and risk, not contrived) for a full review
# round after that — corrupting archived prompts in one place and aborting
# real phase runs on a false positive in the other. `(?<![A-Za-z0-9])` is
# that boundary; no right boundary is needed, since `[A-Za-z0-9._-]+` is
# greedy and already consumes everything a real key or a look-alike could
# contain. One canonical pattern, defined next to everything else this
# module already claims to be the single source of truth for, is what
# actually keeps the two call sites from drifting apart a third time.
KEY_PATTERN = re.compile(r"(?<![A-Za-z0-9])sk-or-[A-Za-z0-9._-]+")


class MissingAPIKey(RuntimeError):
    """Raised when no key is configured from either source."""


def _stored_key():
    return (AppSettings.load().openrouter_key or "").strip()


def _environment_key():
    return (getattr(settings, "OPENROUTER_API_KEY", "") or "").strip()


def key_source():
    """Return which source a call would use: settings, environment, or none."""
    if _stored_key():
        return "settings"
    if _environment_key():
        return "environment"
    return "none"


def resolve_openrouter_key():
    key = _stored_key() or _environment_key()
    if not key:
        raise MissingAPIKey(
            "No OpenRouter API key is configured. Set one on the Settings "
            "screen, or provide OPENROUTER_API_KEY in the environment."
        )
    return key


def mask_key(value):
    """Show only the first and last few characters, never the middle."""
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= MASK_VISIBLE * 2:
        return "*" * 8
    return f"{value[:MASK_VISIBLE]}...{value[-MASK_VISIBLE:]}"
