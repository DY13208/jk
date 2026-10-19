import pytest


@pytest.fixture(autouse=True)
def _no_site_seed(monkeypatch):
    """Tests start from the built-in defaults; the deployment's SITES_SEED must not leak into test databases."""
    monkeypatch.delenv('SITES_SEED', raising=False)