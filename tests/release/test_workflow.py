from pathlib import Path

import yaml

from scripts.release.reconcile import RELEASE_INPUTS_DIR_DEFAULT


ROOT = Path(__file__).parents[2]


def test_release_workflow_input_directory_matches_reconcile_default():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    assert workflow["env"]["RELEASE_INPUTS_DIR"] == RELEASE_INPUTS_DIR_DEFAULT
