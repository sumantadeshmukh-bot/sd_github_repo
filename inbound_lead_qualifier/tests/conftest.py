import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Tests must not depend on the caller's shell (run_demo.sh exports a demo CRM key)."""
    for var in ("CRM_ENCRYPTION_KEY", "WEBHOOK_TOKEN", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
