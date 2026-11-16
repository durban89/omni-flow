from local_worker.config import resolve_llm_settings


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


def test_resolve_llm_settings_defaults_to_disabled() -> None:
    assert resolve_llm_settings({})["enable"] is False


def test_resolve_llm_settings_normalizes_enabled_alias() -> None:
    merged = resolve_llm_settings({"content": {"enabled": True}})

    assert merged["enable"] is True
