import json
from pathlib import Path

import pytest

from scripts.release.contracts import (
    APPLICATION_FIELDS,
    MANIFEST_FIELDS,
    PROBE_RESULT_FIELDS,
    RELEASE_RECORD_FIELDS,
    SOURCES_FIELDS,
    ValidationError,
    ValidationErrors,
    load_application_manifest,
    load_probe_result,
    load_release_record,
    parse_json,
    validate_digest,
    validate_application_manifest,
)


ROOT = Path(__file__).parents[2]
IMAGE = "ghcr.io/example/app@sha256:" + "a" * 64


def manifest(**overrides):
    value = {
        "schema": 1,
        "applications": [{
            "name": "web",
            "image": IMAGE,
            "rollback": "manual",
            "depends_on": [],
            "secrets": ["WEB_TOKEN"],
            "health_probe": "web-health",
            "semantic_probes": ["web-route"],
        }],
    }
    value.update(overrides)
    return value


GIT_SHA = "a" * 40


def release_record(**overrides):
    value = {
        "schema": 1,
        "site_id": "home",
        "sequence": 3,
        "tooling_git_sha": GIT_SHA,
        "sources": {"homelab": GIT_SHA, "homelab-site": "b" * 40},
        "images": {"web": IMAGE},
        "bundle_sha256": "sha256:" + "c" * 64,
        "rollback_floor": 2,
    }
    value.update(overrides)
    return value


def probe_result(**overrides):
    value = {
        "schema": 1,
        "probe": "web-health",
        "target": "web",
        "release_sequence": 3,
        "observed_image_id": "sha256:" + "d" * 64,
        "result": "PASS",
        "observed_at": "2026-09-27T00:00:00Z",
        "duration_ms": 25,
        "detail": "ok",
    }
    value.update(overrides)
    return value


def test_valid_fixture_accepted():
    assert load_application_manifest(json.dumps(manifest()))["schema"] == 1


@pytest.mark.parametrize(
    "document,path",
    [
        (manifest(extra=True), "$.extra"),
        ({**manifest(), "schema": 2}, "$.schema"),
        ({**manifest(), "applications": [{**manifest()["applications"][0], "name": "Bad"}]}, "$.applications[0].name"),
        ({**manifest(), "applications": [manifest()["applications"][0], manifest()["applications"][0]]}, "$.applications[1].name"),
        ({**manifest(), "applications": [{**manifest()["applications"][0], "image": "ghcr.io/example/app:latest"}]}, "$.applications[0].image"),
        ({**manifest(), "applications": [{k: v for k, v in manifest()["applications"][0].items() if k != "image"}]}, "$.applications[0].image"),
        ({**manifest(), "applications": [{k: v for k, v in manifest()["applications"][0].items() if k != "rollback"}]}, "$.applications[0].rollback"),
    ],
)
def test_invalid_manifest_has_path(document, path):
    escaped_path = path.replace("$", r"\$").replace("[", r"\[").replace("]", r"\]")
    with pytest.raises(ValidationError, match=r"^" + escaped_path):
        load_application_manifest(json.dumps(document))


def test_duplicate_json_key_rejected_with_path():
    with pytest.raises(ValidationError, match=r"^\$: duplicate"):
        parse_json('{"schema": 1, "schema": 1, "applications": []}')


def test_invalid_digest_rejected_with_path():
    with pytest.raises(ValidationError, match=r"^\$\.digest: expected Digest"):
        validate_digest("sha256:" + "a" * 63, "$.digest")


def test_schema_field_table_matches_validator():
    schema = json.loads((ROOT / "schemas/applications.schema.json").read_text())
    assert set(schema["properties"]) == set(MANIFEST_FIELDS)
    assert set(schema["required"]) == {k for k, (required, _) in MANIFEST_FIELDS.items() if required}
    application = schema["$defs"]["application"]
    assert set(application["properties"]) == set(APPLICATION_FIELDS)
    assert set(application["required"]) == {k for k, (required, _) in APPLICATION_FIELDS.items() if required}

    release = json.loads((ROOT / "schemas/release.schema.json").read_text())
    assert set(release["properties"]) == set(RELEASE_RECORD_FIELDS)
    assert set(release["required"]) == {k for k, (required, _) in RELEASE_RECORD_FIELDS.items() if required}
    sources = release["$defs"]["sources"]
    assert set(sources["properties"]) == set(SOURCES_FIELDS)
    assert set(sources["required"]) == {k for k, (required, _) in SOURCES_FIELDS.items() if required}

    probe = json.loads((ROOT / "schemas/probe.schema.json").read_text())
    assert set(probe["properties"]) == set(PROBE_RESULT_FIELDS)
    assert set(probe["required"]) == {k for k, (required, _) in PROBE_RESULT_FIELDS.items() if required}



def test_valid_release_record_accepted():
    assert load_release_record(json.dumps(release_record()))["sequence"] == 3


@pytest.mark.parametrize("field", list(RELEASE_RECORD_FIELDS))
def test_release_record_required_fields(field):
    document = release_record()
    document.pop(field)
    with pytest.raises(ValidationError, match=rf"^\$\.{field}"):
        load_release_record(json.dumps(document))


def test_release_record_rejects_unknown_field_and_invalid_image():
    with pytest.raises(ValidationError, match=r"^\$\.extra"):
        load_release_record(json.dumps(release_record(extra=True)))
    with pytest.raises(ValidationError, match=r"^\$\.images\.web"):
        load_release_record(json.dumps(release_record(images={"web": "latest"})))


@pytest.mark.parametrize("rollback_floor", [0, 3])
def test_release_record_accepts_rollback_floor_boundaries(rollback_floor):
    assert load_release_record(json.dumps(release_record(rollback_floor=rollback_floor)))


@pytest.mark.parametrize("rollback_floor", [-1, 4])
def test_release_record_rejects_invalid_rollback_floor(rollback_floor):
    with pytest.raises(ValidationError, match=r"^\$\.rollback_floor"):
        load_release_record(json.dumps(release_record(rollback_floor=rollback_floor)))


def test_release_record_sources_require_homelab_and_allow_optional_tracker_source():
    with pytest.raises(ValidationError, match=r"^\$\.sources\.homelab"):
        load_release_record(json.dumps(release_record(sources={"homelab-site": GIT_SHA})))
    document = release_record(sources={"homelab": GIT_SHA, "homelab-site": GIT_SHA, "tracker-integrations": GIT_SHA})
    assert load_release_record(json.dumps(document))["sources"]["tracker-integrations"] == GIT_SHA


def test_valid_probe_result_accepted():
    assert load_probe_result(json.dumps(probe_result()))["result"] == "PASS"


@pytest.mark.parametrize("field", list(PROBE_RESULT_FIELDS))
def test_probe_result_required_fields(field):
    document = probe_result()
    document.pop(field)
    with pytest.raises(ValidationError, match=rf"^\$\.{field}"):
        load_probe_result(json.dumps(document))


def test_probe_result_accepts_null_sequence_and_detail_limit():
    assert load_probe_result(json.dumps(probe_result(release_sequence=None, detail="x" * 1024)))


@pytest.mark.parametrize("document", [probe_result(extra=True), probe_result(detail="x" * 1025)])
def test_probe_result_rejects_unknown_field_and_oversized_detail(document):
    with pytest.raises(ValidationError):
        load_probe_result(json.dumps(document))


def applications(*items):
    return {"schema": 1, "applications": list(items)}


def app(name, **overrides):
    value = {
        "name": name,
        "image": IMAGE,
        "rollback": "manual",
        "depends_on": [],
        "secrets": [],
        "health_probe": f"{name}-health",
        "semantic_probes": [f"{name}-route"],
    }
    value.update(overrides)
    return value


def test_dependency_must_refer_to_a_listed_application():
    with pytest.raises(ValidationError, match="dependency is not an enabled application"):
        validate_application_manifest(applications(app("web", depends_on=["missing"])))


def test_dependency_cycles_are_reported():
    document = applications(app("web", depends_on=["db"]), app("db", depends_on=["web"]))
    with pytest.raises(ValidationError, match=r"dependency cycle: web -> db -> web"):
        validate_application_manifest(document)


def test_probe_ids_must_be_installed():
    with pytest.raises(ValidationError, match="probe is not installed: web-health"):
        validate_application_manifest(applications(app("web")), installed_probes={"other"})


def test_gpu_profiles_must_be_installed():
    document = applications(app("web", gpu={"profile": "rtx"}))
    with pytest.raises(ValidationError, match="GPU profile is not installed: rtx"):
        validate_application_manifest(document, gpu_profiles={"integrated"})


@pytest.mark.parametrize("field", ["health_probe", "semantic_probes"])
def test_health_and_semantic_probes_are_required(field):
    application = app("web")
    application.pop(field)
    with pytest.raises(ValidationError, match=rf"\$\.applications\[0\]\.{field}"):
        validate_application_manifest(applications(application))


def test_at_least_one_semantic_probe_is_required():
    with pytest.raises(ValidationError, match="must contain at least one probe"):
        validate_application_manifest(applications(app("web", semantic_probes=[])))


def test_policy_required_probe_cannot_be_dropped():
    document = applications(app("web", semantic_probes=["web-route"]))
    with pytest.raises(ValidationError, match="missing policy-required probe: web-api"):
        validate_application_manifest(
            document,
            site_policy={"minimum_rollback": {}, "required_probes": {"web": ["web-api"]}},
        )


def test_policy_cannot_be_downgraded_to_automatic_rollback():
    document = applications(app("web", rollback="automatic"))
    with pytest.raises(ValidationError, match="below the site policy minimum manual"):
        validate_application_manifest(
            document,
            site_policy={"minimum_rollback": {"web": "manual"}, "required_probes": {}},
        )


def test_semantic_validation_reports_all_violations():
    document = applications(
        app("web", depends_on=["missing"], rollback="automatic", gpu={"profile": "rtx"}),
        app("db", depends_on=["web"]),
    )
    with pytest.raises(ValidationErrors) as caught:
        validate_application_manifest(
            document,
            installed_probes=set(),
            gpu_profiles=set(),
            site_policy={
                "minimum_rollback": {"web": "manual"},
                "required_probes": {"web": ["web-api"]},
            },
        )
    message = str(caught.value)
    assert "dependency is not an enabled application" in message
    assert "dependency cycle" not in message
    assert "probe is not installed" in message
    assert "GPU profile is not installed" in message
    assert "below the site policy minimum" in message
    assert "missing policy-required probe" in message
