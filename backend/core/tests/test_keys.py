import pytest

from core.keys import MissingAPIKey, key_source, mask_key, resolve_openrouter_key
from core.models import AppSettings


@pytest.mark.django_db
def test_settings_key_wins_over_environment(settings):
    settings.OPENROUTER_API_KEY = "sk-or-from-environment"
    config = AppSettings.load()
    config.openrouter_key = "sk-or-from-database"
    config.save()

    assert resolve_openrouter_key() == "sk-or-from-database"
    assert key_source() == "settings"


@pytest.mark.django_db
def test_falls_back_to_environment_when_settings_blank(settings):
    settings.OPENROUTER_API_KEY = "sk-or-from-environment"
    assert resolve_openrouter_key() == "sk-or-from-environment"
    assert key_source() == "environment"


@pytest.mark.django_db
def test_clearing_the_stored_key_falls_back(settings):
    settings.OPENROUTER_API_KEY = "sk-or-from-environment"
    config = AppSettings.load()
    config.openrouter_key = "sk-or-from-database"
    config.save()
    config.openrouter_key = ""
    config.save()

    assert resolve_openrouter_key() == "sk-or-from-environment"


@pytest.mark.django_db
def test_missing_key_raises_with_actionable_message(settings):
    settings.OPENROUTER_API_KEY = ""
    assert key_source() == "none"
    with pytest.raises(MissingAPIKey, match="No OpenRouter API key"):
        resolve_openrouter_key()


@pytest.mark.django_db
def test_app_settings_is_a_singleton():
    first = AppSettings.load()
    second = AppSettings.load()
    assert first.pk == second.pk == 1
    assert AppSettings.objects.count() == 1


def test_mask_key_shows_only_the_ends():
    assert mask_key("sk-or-v1-abcdefghijklmnop") == "sk-o...mnop"


def test_mask_key_of_blank_is_blank():
    assert mask_key("") == ""


def test_mask_key_never_reveals_a_short_key():
    # At most MASK_VISIBLE*2 characters: showing both ends would show all of it.
    assert mask_key("sk-or123") == "********"


def test_mask_key_of_a_nine_character_key_hides_the_middle():
    # One character above the fully-masked cutoff: the visible ends must not
    # meet, or masking would reveal the whole key.
    assert mask_key("sk-or1234") == "sk-o...1234"
