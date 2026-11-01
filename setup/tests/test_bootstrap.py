import os
import re
import subprocess
from pathlib import Path

import pytest

from setup.hostprep import artifact_pin

SCRIPT = Path(__file__).parents[1] / "bootstrap.sh"


def test_launcher_pins_the_same_cosign_as_host_preparation():
    text = SCRIPT.read_text()
    pin = artifact_pin("cosign")

    assert re.search(rf"^COSIGN_URL={re.escape(pin['url'])}$", text, re.M)
    assert re.search(rf"^COSIGN_SHA256={pin['sha256']}$", text, re.M)


@pytest.mark.skipif(os.geteuid() == 0, reason="the refusal is for non-root users")
def test_launcher_refuses_a_non_root_user_before_creating_anything(tmp_path):
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "TMPDIR": str(tmp_path)},
    )

    assert result.returncode == 1
    assert "run as root" in result.stderr
    assert list(tmp_path.iterdir()) == []
