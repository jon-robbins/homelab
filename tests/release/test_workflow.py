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


def test_seerr_preparation_and_build_matrix_parity():
    import json
    import subprocess

    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["image-builds"]
    post = yaml.safe_load((ROOT / ".github/workflows/post-merge-build.yml").read_text())["jobs"]
    steps = {step.get("id"): step for step in post["changed-images"]["steps"]}
    filters = yaml.safe_load(steps["filter"]["with"]["filters"])
    assert filters["seerr-oidc"] == ["config/seerr-oidc/**"]
    assert steps["matrix"]["env"]["FILTER_SEERR_OIDC"] == "${{ steps.filter.outputs.seerr-oidc }}"
    # jq command spans multiple lines; run the workflow exactly, with all filters set.
    command = steps["matrix"]["run"].replace('echo "matrix=$matrix" >> "$GITHUB_OUTPUT"', 'echo "$matrix"')
    result = subprocess.check_output(["bash", "-c", command], env={"FILTER_CADDY": "false", "FILTER_SEERR_OIDC": "true", "FILTER_RECIPE": "false"}, text=True)
    selected = json.loads(result)["include"]
    assert len(selected) == 1 and selected[0]["image"] == "seerr-oidc"
    result = subprocess.check_output(["bash", "-c", command], env={"FILTER_CADDY": "false", "FILTER_SEERR_OIDC": "false", "FILTER_RECIPE": "true"}, text=True)
    published = json.loads(result)["include"]
    assert {item["image"] for item in published} == {"caddy", "seerr-oidc"}
    keys = ("dockerfile", "context", "prepare", "prepare_destination", "source_lock")
    assert [{key: item.get(key) for key in keys} for item in published] == [
        {key: item.get(key) for key in keys} for item in ci["strategy"]["matrix"]["include"]
    ]
    for job in (ci, post["build"]):
        prepare = next(step for step in job["steps"] if step.get("if") == "matrix.prepare")
        assert prepare["env"]["PREPARE_SCRIPT"] == "${{ matrix.prepare }}"
        assert prepare["env"]["PREPARE_DESTINATION"] == "${{ matrix.prepare_destination }}"
        assert prepare["run"] == 'python3 "$PREPARE_SCRIPT" "$PREPARE_DESTINATION" --prepare-only'
        assert job["steps"].index(prepare) < next(i for i, step in enumerate(job["steps"]) if "docker build" in step.get("run", ""))
        build = next(step for step in job["steps"] if "docker build" in step.get("run", ""))
        assert build["env"]["SOURCE_LOCK"] == "${{ matrix.source_lock }}"
        assert 'COMMIT_TAG=$(jq -r .commit "$SOURCE_LOCK")' in build["run"]
        assert 'org.opencontainers.image.revision=$(jq -r .commit "$SOURCE_LOCK")' in build["run"]
        assert '"${build_args[@]}"' in build["run"]
    assert selected[0]["source_lock"] == "config/seerr-oidc/source.json"
    approved = json.loads((ROOT / "config/release/approved-ghcr-destinations.json").read_text())
    assert "ghcr.io/jon-robbins/homelab/seerr-oidc" in approved["destinations"]
