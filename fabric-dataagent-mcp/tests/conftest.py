import os

import pytest


@pytest.fixture(autouse=True)
def _clean_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [key for key in os.environ if key.startswith("FABRIC_PROXY_")]:
        monkeypatch.delenv(name)
