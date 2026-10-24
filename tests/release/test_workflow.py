from pathlib import Path

import yaml

from scripts.release.reconcile import BUILD_INPUT_PREFIXES, RELEASE_INPUTS_DIR_DEFAULT


ROOT = Path(__file__).parents[2]


def test_release_workflow_input_directory_matches_reconcile_default():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    assert workflow["env"]["RELEASE_INPUTS_DIR"] == RELEASE_INPUTS_DIR_DEFAULT


def test_post_merge_build_rebuilds_every_image_on_recipe_change():
    workflow = yaml.safe_load((ROOT / ".github/workflows/post-merge-build.yml").read_text(encoding="utf-8"))
    steps = {step.get("id"): step for step in workflow["jobs"]["changed-images"]["steps"]}
    filters = yaml.safe_load(steps["filter"]["with"]["filters"])
    assert set(filters["recipe"]) == {
        ".github/workflows/post-merge-build.yml",
        "config/release/approved-ghcr-destinations.json",
    }
    assert steps["matrix"]["env"]["FILTER_RECIPE"] == "${{ steps.filter.outputs.recipe }}"
    assert 'select(.changed == "true" or env.FILTER_RECIPE == "true")' in steps["matrix"]["run"]


def test_reconcile_build_inputs_cover_every_post_merge_filter():
    workflow = yaml.safe_load((ROOT / ".github/workflows/post-merge-build.yml").read_text(encoding="utf-8"))
    steps = {step.get("id"): step for step in workflow["jobs"]["changed-images"]["steps"]}
    filters = yaml.safe_load(steps["filter"]["with"]["filters"])
    patterns = [pattern for paths in filters.values() for pattern in paths]
    uncovered = [pattern for pattern in patterns if not pattern.removesuffix("**").startswith(BUILD_INPUT_PREFIXES)]
    assert uncovered == []
