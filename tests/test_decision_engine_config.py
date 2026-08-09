from src.decision_engine import _chat_completions_url, _load_project_provider


def test_chat_completions_url_normalization():
    assert _chat_completions_url("https://api.example.test/v1") == "https://api.example.test/v1/chat/completions"
    assert (
        _chat_completions_url("https://api.example.test/v1/chat/completions/")
        == "https://api.example.test/v1/chat/completions"
    )


def test_project_provider_uses_environment(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_PROVIDER", "custom")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    provider = _load_project_provider()

    assert provider == {
        "api_key": "test-key",
        "base_url": "https://api.example.test/v1",
        "model": "test-model",
        "provider": "custom",
    }


def test_project_provider_requires_explicit_enable(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.test/v1")
    assert _load_project_provider() is None
