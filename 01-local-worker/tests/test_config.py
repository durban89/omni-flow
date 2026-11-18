import pytest

from local_worker.config import apply_mode, resolve_llm_settings
from local_worker.errors import ConfigError


def test_resolve_llm_settings_merges_content_and_llm_sections() -> None:
    settings = {
        "content": {"model": "gpt-custom", "temperature": 0.6, "max_words": 900},
        "llm": {"enable": True, "timeout_seconds": 12},
    }

    merged = resolve_llm_settings(settings)

    assert merged["model"] == "gpt-custom"
    assert merged["temperature"] == 0.6
    assert merged["max_words"] == 900
    assert merged["timeout_seconds"] == 12
    assert merged["enable"] is True


def test_resolve_llm_settings_llm_section_overrides_content() -> None:
    settings = {
        "content": {"model": "legacy-model", "temperature": 0.9},
        "llm": {"model": "canonical-model"},
    }

    merged = resolve_llm_settings(settings)

    assert merged["model"] == "canonical-model"
    assert merged["temperature"] == 0.9


def _modes_settings() -> dict:
    return {
        "app": {"mode": "test"},
        "pipeline": {"max_items_per_run": 50, "require_human_review": True},
        "scraper": {"max_items_per_run": 200, "request_timeout_seconds": 20},
        "llm": {"model": "base-model", "temperature": 0.3},
        "modes": {
            "test": {
                "pipeline": {"max_items_per_run": 1},
                "scraper": {"max_items_per_run": 1},
            },
            "production": {
                "pipeline": {"max_items_per_run": 20},
                "scraper": {"max_items_per_run": 20},
                "llm": {"model": "strong-model"},
            },
        },
    }


def test_apply_mode_uses_app_mode_and_keeps_other_keys() -> None:
    merged = apply_mode(_modes_settings())

    assert merged["app"]["mode"] == "test"
    assert merged["pipeline"]["max_items_per_run"] == 1
    assert merged["scraper"]["max_items_per_run"] == 1
    assert merged["pipeline"]["require_human_review"] is True
    assert merged["scraper"]["request_timeout_seconds"] == 20
    assert merged["llm"]["model"] == "base-model"
    assert "modes" not in merged


def test_apply_mode_explicit_mode_wins_and_overrides_model() -> None:
    merged = apply_mode(_modes_settings(), mode="production")

    assert merged["app"]["mode"] == "production"
    assert merged["pipeline"]["max_items_per_run"] == 20
    assert merged["llm"]["model"] == "strong-model"
    assert merged["llm"]["temperature"] == 0.3


def test_apply_mode_rejects_unknown_mode() -> None:
    with pytest.raises(ConfigError, match="unknown mode 'staging'"):
        apply_mode(_modes_settings(), mode="staging")


def test_apply_mode_without_modes_block_is_a_no_op() -> None:
    settings = {"pipeline": {"max_items_per_run": 50}}

    assert apply_mode(settings) == settings


def test_resolve_llm_settings_defaults_to_disabled() -> None:
    assert resolve_llm_settings({})["enable"] is False


def test_resolve_llm_settings_normalizes_enabled_alias() -> None:
    merged = resolve_llm_settings({"content": {"enabled": True}})

    assert merged["enable"] is True
