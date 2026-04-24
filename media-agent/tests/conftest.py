import os
import sys
from pathlib import Path

# Package root: media-agent/ (not media-agent/app/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

# Must be set before importing the app (settings are cached on first get_settings()).
# Force (not setdefault): host environment may set MEDIA_AGENT_TOKEN and break auth.
os.environ["MEDIA_AGENT_TOKEN"] = "test-bearer-secret"
os.environ["SONARR_URL"] = "http://sonarr.test/son"
os.environ["SONARR_API_KEY"] = "sonarr-key"
os.environ["RADARR_URL"] = "http://radarr.test/rad"
os.environ["RADARR_API_KEY"] = "radarr-key"
# Keep Prowlarr off so existing tests don't require it; opt-in in tests with monkeypatch.
os.environ["PROWLARR_URL"] = ""
os.environ["PROWLARR_API_KEY"] = ""

from app.core.config import reset_settings  # noqa: E402

pytest_plugins = []


@pytest.fixture(autouse=True)
def _reset_settings() -> None:
    reset_settings()
    yield
    reset_settings()
