import os

import pytest


@pytest.fixture(autouse=True)
def clear_ci_environment(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith(("GITHUB_", "RUNNER_")):
            monkeypatch.delenv(name, raising=False)
