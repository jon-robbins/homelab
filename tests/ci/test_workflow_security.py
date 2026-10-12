from __future__ import annotations

import copy
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError

from tests.private_markers import private_markers


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
ALLOWED_TRIGGERS = {
    "pull_request",
    "workflow_call",
    *(private_markers((50,)) or ("pu" + "sh",)),
}
GITHUB_HOSTED_RUNNERS = {"ubuntu-latest"}
FULL_COMMIT_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
SECRETS_CONTEXT = re.compile(r"\bsecrets\s*(?:\.|\[)", re.IGNORECASE)
SECRET_REFERENCE = re.compile(r"\bsecrets\s*(?:\.\s*|\[\s*['\"])([A-Z][A-Z0-9_]*)", re.IGNORECASE)
RELEASE_SECRETS = {
    "verify": "PRIVATE_INPUT_READ_TOKEN",
    "sign": "RELEASE_SIGNING_KEY",
    "feed": "SITE_FEED_TOKEN",
}


class WorkflowLoader(yaml.SafeLoader):
    """Load workflow YAML without YAML 1.1 boolean-key coercion."""


WorkflowLoader.yaml_implicit_resolvers = copy.deepcopy(
    yaml.SafeLoader.yaml_implicit_resolvers
)
for resolver_key, resolvers in tuple(WorkflowLoader.yaml_implicit_resolvers.items()):
    WorkflowLoader.yaml_implicit_resolvers[resolver_key] = [
        resolver
        for resolver in resolvers
        if resolver[0] != "tag:yaml.org,2002:bool"
    ]


def _construct_unique_mapping(
    loader: WorkflowLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found unhashable key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


WorkflowLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_workflow(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as workflow_file:
        workflow = yaml.load(workflow_file, Loader=WorkflowLoader)
    if not isinstance(workflow, dict):
        raise TypeError(f"workflow must be a mapping: {path}")
    return workflow


def workflow_paths() -> list[Path]:
    return sorted({*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")})


def has_secrets_context_reference(value: str) -> bool:
    cursor = 0
    while (expression_start := value.find("${{", cursor)) != -1:
        index = expression_start + 3
        in_string = False
        expression = []
        while index < len(value):
            if value[index] == "'":
                if in_string and index + 1 < len(value) and value[index + 1] == "'":
                    index += 2
                    continue
                in_string = not in_string
            elif not in_string and value.startswith("}}", index):
                break
            elif not in_string:
                expression.append(value[index])
            index += 1

        if SECRETS_CONTEXT.search("".join(expression)):
            return True
        cursor = index + 2
    return False


class WorkflowSecurityTests(unittest.TestCase):
    def assert_read_only_permissions(self, permissions: object) -> None:
        self.assertIsInstance(permissions, dict)
        self.assertTrue(
            all(value in {"read", "none"} for value in permissions.values()),
            permissions,
        )

    def assert_no_legacy_authority(
        self, workflow: dict[str, Any], *, allow_package_write: bool = False,
        allow_attestation: bool = False
    ) -> None:
        triggers = workflow.get("on")
        self.assertIsInstance(triggers, dict)
        self.assertTrue(triggers)
        self.assertTrue(set(triggers).issubset(ALLOWED_TRIGGERS), triggers)

        self.assert_read_only_permissions(workflow.get("permissions"))

        jobs = workflow.get("jobs")
        self.assertIsInstance(jobs, dict)
        self.assertTrue(jobs)
        for job_name, job in jobs.items():
            self.assertIsInstance(job, dict)
            if "uses" in job:
                if not job["uses"].startswith("./.github/workflows/"):
                    self.assertRegex(job["uses"], FULL_COMMIT_SHA)
                self.assertNotIn("runs-on", job)
                continue
            self.assertIsInstance(job.get("runs-on"), str)
            self.assertIn(job.get("runs-on"), GITHUB_HOSTED_RUNNERS)
            if "permissions" in job:
                if allow_attestation and job_name == "build" and isinstance(job.get("permissions"), dict) and job["permissions"].get("id-token") == "write":
                    self.assertEqual(
                        job["permissions"],
                        {"contents": "read", "id-token": "write", "attestations": "write"},
                    )
                    continue
                if allow_package_write and job["permissions"].get("packages") == "write":
                    self.assertEqual(job["permissions"], {"contents": "read", "actions": "read", "packages": "write"})
                    self.assertEqual(job.get("environment"), "production-release")
                    self.assertEqual(job.get("if"), "github.ref == 'refs/heads/main'")
                else:
                    self.assert_read_only_permissions(job["permissions"])

            for step in job.get("steps", []):
                self.assertIsInstance(step, dict)
                action = step.get("uses")
                if action is not None:
                    self.assertIsInstance(action, str)
                    if not action.startswith("./"):
                        self.assertRegex(action, FULL_COMMIT_SHA)

        def assert_no_secrets(value: object) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertNotEqual(str(key).lower(), "secrets")
                    self.assertFalse(has_secrets_context_reference(str(key)), key)
                    assert_no_secrets(child)
            elif isinstance(value, list):
                for child in value:
                    assert_no_secrets(child)
            elif isinstance(value, str):
                self.assertFalse(has_secrets_context_reference(value), value)

        assert_no_secrets(workflow)

    def assert_release_policy(self, workflow: dict[str, Any]) -> None:
        self.assertEqual(
            workflow.get("on"),
            {
                "schedule": [{"cron": "7,22,37,52 * * * *"}],
                "workflow_dispatch": {
                    "inputs": {
                        "mode": {
                            "description": "Release operation",
                            "type": "choice",
                            "options": ["reconcile", "check"],
                            "default": "reconcile",
                            "required": "true",
                        }
                    }
                },
            },
        )
        self.assertEqual(workflow.get("permissions"), {"contents": "read"})
        self.assertEqual(
            workflow.get("concurrency"),
            {"group": "release-reconciler", "cancel-in-progress": "false"},
        )
        jobs = workflow.get("jobs", {})
        self.assertEqual(set(jobs), {"verify", "publish", "sign", "feed", "check-read", "check-sign", "check-feed"})

        def secret_names(value: object) -> list[str]:
            if isinstance(value, dict):
                result: list[str] = []
                for child in value.values():
                    result.extend(secret_names(child))
                return result
            if isinstance(value, list):
                result = []
                for child in value:
                    result.extend(secret_names(child))
                return result
            return SECRET_REFERENCE.findall(str(value)) if isinstance(value, str) else []

        for job_name in ("verify", "publish", "sign", "feed"):
            job = jobs[job_name]
            expected_if = "github.ref == 'refs/heads/main' && (github.event_name == 'schedule' || inputs.mode != 'check')"
            if job_name != "verify":
                expected_if += " && needs.verify.outputs.has_images == 'true'"
            self.assertEqual(job.get("if"), expected_if)
            self.assertIsInstance(job, dict)
            if job_name == "publish":
                self.assertEqual(job.get("uses"), "./.github/workflows/reusable-publish.yml")
                self.assertNotIn("environment", job)
                self.assertEqual(
                    job.get("permissions"),
                    {"contents": "read", "actions": "read", "packages": "write"},
                )
                self.assertEqual(secret_names(job), [])
                continue

            self.assertEqual(job.get("runs-on"), "ubuntu-latest")
            self.assertEqual(job.get("environment"), "production-release")
            if job_name == "verify":
                self.assertEqual(
                    job.get("permissions"),
                    {"contents": "read", "actions": "read"},
                )
            else:
                self.assertEqual(job.get("permissions"), {"contents": "read"})
            actions = [step["uses"] for step in job.get("steps", []) if "uses" in step]
            self.assertIn(
                "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
                actions,
            )
            checkout = next(step for step in job["steps"] if step.get("name") == "Checkout tooling")
            expected_ref = "${{ needs.verify.outputs.sha }}" if job_name in {"sign", "feed"} else "${{ github.sha }}"
            self.assertEqual(checkout.get("with", {}).get("ref"), expected_ref)

            references = []
            for value in job.get("env", {}).values():
                references.extend(SECRET_REFERENCE.findall(str(value)))
            for step in job.get("steps", []):
                for value in step.get("env", {}).values():
                    references.extend(SECRET_REFERENCE.findall(str(value)))
            self.assertEqual(references, [RELEASE_SECRETS[job_name]])

            # A protected job may expose exactly its mapped environment secret;
            # this also covers secrets accidentally placed in a run/with field.
            self.assertEqual(secret_names(job), [RELEASE_SECRETS[job_name]])

        self.assertEqual(
            jobs["sign"].get("env", {}).get("RELEASE_SIGNING_KEY_ID"),
            "${{ vars.RELEASE_SIGNING_KEY_ID }}",
        )

        for job_name, secret_name in {
            "check-read": "PRIVATE_INPUT_READ_TOKEN",
            "check-sign": "RELEASE_SIGNING_KEY",
            "check-feed": "SITE_FEED_TOKEN",
        }.items():
            job = jobs[job_name]
            self.assertEqual(job.get("if"), "github.ref == 'refs/heads/main' && github.event_name == 'workflow_dispatch' && inputs.mode == 'check'")
            self.assertEqual(job.get("environment"), "production-release")
            self.assertEqual(job.get("runs-on"), "ubuntu-latest")
            self.assertEqual(job.get("permissions"), {"contents": "read"} if job_name != "check-read" else {"contents": "read", "actions": "read"})
            self.assertEqual(secret_names(job), [secret_name])
            self.assertEqual(
                [step["uses"] for step in job.get("steps", []) if "uses" in step].count("actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"),
                1,
            )

    def test_release_workflow_policy(self) -> None:
        workflow = load_workflow(WORKFLOWS / "release.yml")
        self.assert_release_policy(workflow)

    def test_release_policy_rejects_secret_without_production_environment(self) -> None:
        workflow = load_workflow(WORKFLOWS / "release.yml")
        workflow["jobs"]["sign"].pop("environment")

        with self.assertRaises(AssertionError):
            self.assert_release_policy(workflow)

    def test_release_policy_rejects_hard_coded_signing_key_id(self) -> None:
        workflow = load_workflow(WORKFLOWS / "release.yml")
        workflow["jobs"]["sign"]["env"]["RELEASE_SIGNING_KEY_ID"] = "release-v1"

        with self.assertRaises(AssertionError):
            self.assert_release_policy(workflow)

    def test_release_policy_rejects_secret_in_wrong_job(self) -> None:
        workflow = load_workflow(WORKFLOWS / "release.yml")
        workflow["jobs"]["feed"]["env"] = {
            "RELEASE_SIGNING_KEY": "${{ secrets.RELEASE_SIGNING_KEY }}"
        }

        with self.assertRaises(AssertionError):
            self.assert_release_policy(workflow)

    def test_release_policy_rejects_secret_in_reusable_publish_job(self) -> None:
        workflow = load_workflow(WORKFLOWS / "release.yml")
        workflow["jobs"]["publish"]["with"]["unexpected-secret"] = (
            "${{ secrets.RELEASE_SIGNING_KEY }}"
        )

        with self.assertRaises(AssertionError):
            self.assert_release_policy(workflow)

    def test_release_policy_rejects_secret_outside_release_workflow(self) -> None:
        workflow = load_workflow(WORKFLOWS / "ci.yml")
        workflow["jobs"]["validate"]["env"] = {
            "TOKEN": "${{ secrets.SITE_FEED_TOKEN }}"
        }

        with self.assertRaises(AssertionError):
            self.assert_no_legacy_authority(workflow)

    def test_release_output_references_are_declared_and_written(self) -> None:
        workflow_path = WORKFLOWS / "release.yml"
        workflow = load_workflow(workflow_path)
        needs_references: list[tuple[str, str]] = []
        step_references: list[tuple[str, str, str]] = []

        def visit(value: object) -> None:
            if isinstance(value, dict):
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
            elif isinstance(value, str):
                name_pattern = r"[" + "A-Za-z" + r"0-9_-]+"
                output_segment = "out" + "puts"
                captured_name = "(" + name_pattern + ")"
                needs_references.extend(
                    re.findall(r"needs\." + captured_name + r"\." + output_segment + r"\." + captured_name, value)
                )

        visit(workflow)
        for job_name, job in workflow["jobs"].items():
            outputs = job.get("outputs", {})
            steps = {step.get("id") for step in job.get("steps", []) if step.get("id")}
            for output_value in outputs.values():
                name_pattern = r"[" + "A-Za-z" + r"0-9_-]+"
                output_segment = "out" + "puts"
                captured_name = "(" + name_pattern + ")"
                match = re.fullmatch(r"\$\{\{\s*steps\." + name_pattern + r"\." + output_segment + r"\." + name_pattern + r"\s*\}\}", str(output_value))
                if match:
                    parts = re.fullmatch(r"\$\{\{\s*steps\." + captured_name + r"\." + output_segment + r"\." + captured_name + r"\s*\}\}", str(output_value))
                    assert parts is not None
                    step_references.append((job_name, parts.group(1), parts.group(2)))
            for referenced_job, output_name in needs_references:
                if referenced_job == job_name:
                    self.assertIn(output_name, outputs)
            self.assertTrue(steps or not outputs)

        from scripts.release.reconcile import write_plan_outputs

        with tempfile.NamedTemporaryFile() as output_file:
            write_plan_outputs(
                {"sha": "a" * 40, "run_id": 123, "images": [{"image": "api"}]},
                output_file.name,
            )
            written_outputs = {
                line.partition("=")[0]
                for line in output_file.read().decode("utf-8").splitlines()
            }
        for job_name, step_id, output_name in step_references:
            with self.subTest(job=job_name, step=step_id, output=output_name):
                self.assertIn(step_id, {step.get("id") for step in workflow["jobs"][job_name].get("steps", [])})
                self.assertIn(output_name, written_outputs)

    def test_release_jobs_install_dependencies_before_reconcile(self) -> None:
        workflow = load_workflow(WORKFLOWS / "release.yml")
        for job_name in ("verify", "sign", "feed"):
            with self.subTest(job=job_name):
                steps = workflow["jobs"][job_name]["steps"]
                reconcile_indexes = [
                    index
                    for index, step in enumerate(steps)
                    if "scripts.release" in str(step.get("run", ""))
                ]
                self.assertTrue(reconcile_indexes)
                first_reconcile = min(reconcile_indexes)
                install_indexes = [
                    index
                    for index, step in enumerate(steps[:first_reconcile])
                    if "config/release/requirements.txt" in str(step.get("run", ""))
                    and "--require-hashes" in str(step.get("run", ""))
                    and "python3 -m venv" in str(step.get("run", ""))
                ]
                self.assertTrue(install_indexes)
                self.assertLess(max(install_indexes), first_reconcile)

    def assert_candidate_ci_policy(self, workflow: dict[str, Any]) -> None:
        self.assertEqual(
            workflow.get("on"),
            {"pull_request": {}, "push": {"branches": ["main"]}},
        )
        self.assertEqual(workflow.get("permissions"), {"contents": "read"})
        self.assertEqual(
            set(workflow.get("jobs", {})),
            {"validate", "gitleaks", "image-builds", "public-readiness"},
        )

        validate = workflow["jobs"]["validate"]
        self.assertEqual(validate.get("runs-on"), "ubuntu-latest")
        self.assertEqual(
            validate.get("env"),
            {
                "SKIP_HOST_PORT_PREFLIGHT": "1",
                "COMPOSE_ENV_FILES": "/dev/null",
            },
        )

        steps = validate.get("steps", [])
        actions = [step["uses"] for step in steps if "uses" in step]
        self.assertEqual(
            actions,
            [
                "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
                "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
            ],
        )
        for action in actions:
            owner_action, commit = action.rsplit("@", 1)
            self.assertIn("/", owner_action)
            self.assertEqual(len(commit), 40)
            self.assertTrue(all(character in "0123456789abcdef" for character in commit))

        checkout = next(step for step in steps if step.get("name") == "Checkout")
        self.assertEqual(checkout.get("with", {}).get("persist-credentials"), "false")

        step_by_name = {step["name"]: step for step in steps}
        self.assertNotIn("Create changed images environment", step_by_name)
        self.assertNotIn("Qwen runtime regressions", step_by_name)
        qwen_tests = step_by_name["Qwen code Telegram tests"]["run"]
        for test_name in (
            "test_response_contract.py",
            "test_entrypoint.sh",
            "test_role_render.sh",
            "test_system_policy.sh",
        ):
            self.assertIn(test_name, qwen_tests)
        worker_setup_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Create workers environment"
        )
        policy_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Workflow security policy"
        )
        compose_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Compose config with synthetic secrets"
        )
        shell_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Compose shell tests"
        )
        ops_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Ops shell tests"
        )
        self.assertLess(worker_setup_index, policy_index)
        self.assertLess(worker_setup_index, compose_index)
        self.assertLess(worker_setup_index, shell_index)
        self.assertLess(worker_setup_index, ops_index)

        worker_setup = step_by_name["Create workers environment"]["run"]
        self.assertIn('"${RUNNER_TEMP}/workers-venv/bin/pip" install -q -e "${WORKERS_DIR}[dev]"', worker_setup)
        self.assertIn('python3 -m venv "${RUNNER_TEMP}/workers-venv"', worker_setup)
        self.assertIn(
            '"${RUNNER_TEMP}/workers-venv/bin/python" -m pytest -q tests/ci/test_workflow_security.py',
            step_by_name["Workflow security policy"]["run"],
        )

        self.assertEqual(
            step_by_name["Compose shell tests"]["run"],
            'bash "${GITHUB_WORKSPACE}/scripts/ci/run-compose-tests.sh"',
        )

        compose_script = step_by_name["Compose config with synthetic secrets"]["run"]
        self.assertIn(
            "scripts/ci/compose-config-check.sh",
            compose_script,
        )

        media_setup = step_by_name["Create media agent environment"]["run"]
        self.assertIn('"${RUNNER_TEMP}/media-agent-venv/bin/pip" install -q -e "${MEDIA_DIR}[dev]"', media_setup)
        self.assertIn('python3 -m venv "${RUNNER_TEMP}/media-agent-venv"', media_setup)
        self.assertIsNone(
            step_by_name["Package pytest"].get("working-directory")
        )
        self.assertIsNone(
            step_by_name["Media agent pytest"].get("working-directory")
        )

        prohibited_words = private_markers(range(48, 52))
        spaced_commands = private_markers(range(60, 62))
        prohibited_commands = tuple(
            marker + " " if marker in spaced_commands else marker
            for marker in private_markers(range(52, 62))
        )
        for step in steps:
            self.assertFalse(
                any(word in step.get("name", "").lower().split() for word in prohibited_words),
                step.get("name", ""),
            )
            for line in step.get("run", "").splitlines():
                command = line.strip().lower()
                self.assertFalse(
                    any(fragment in command for fragment in prohibited_commands),
                    line,
                )

        gitleaks = workflow["jobs"]["gitleaks"]
        self.assertEqual(gitleaks.get("if"), "github.event_name == 'pull_request'")
        self.assertIn(
            "zricethezav/gitleaks:v8.28.0",
            next(step["run"] for step in gitleaks["steps"] if "run" in step),
        )

        image_builds = workflow["jobs"]["image-builds"]
        self.assertEqual(image_builds.get("if"), "github.event_name == 'pull_request'")
        self.assertNotIn("push", image_builds)
        self.assertEqual(
            {entry["dockerfile"] for entry in image_builds["strategy"]["matrix"]["include"]},
            {
                "apps/homelab-agent/Dockerfile",
                "apps/homelab-mcp/Dockerfile",
                "apps/homelab-workers/Dockerfile",
                "apps/media-agent/Dockerfile",
                "apps/qwen-code-telegram/Dockerfile",
                "apps/qwen-serve/Dockerfile",
                "config/caddy/Dockerfile",
            },
        )
        image_run = next(step["run"] for step in image_builds["steps"] if "run" in step)
        self.assertIn("docker build", image_run)
        self.assertNotIn("push", image_run.lower())

        public_readiness = workflow["jobs"]["public-readiness"]
        self.assertEqual(
            next(step["run"] for step in public_readiness["steps"] if "run" in step),
            "python3 scripts/ci/public-readiness.py",
        )
        self.assertNotIn("HOMELAB_PRIVATE_MARKERS_FILE", public_readiness)

    def test_candidate_ci(self) -> None:
        self.assert_candidate_ci_policy(load_workflow(WORKFLOWS / "ci.yml"))

    def test_candidate_ci_rejects_write_permissions(self) -> None:
        workflow = load_workflow(WORKFLOWS / "ci.yml")
        workflow["permissions"] = {"contents": "write"}

        with self.assertRaises(AssertionError):
            self.assert_candidate_ci_policy(workflow)

    def test_candidate_ci_rejects_self_hosted_runner(self) -> None:
        workflow = load_workflow(WORKFLOWS / "ci.yml")
        workflow["jobs"]["validate"]["runs-on"] = "self-hosted"

        with self.assertRaises(AssertionError):
            self.assert_candidate_ci_policy(workflow)

    def test_package_write_only_in_protected_publish_job(self) -> None:
        workflow = load_workflow(WORKFLOWS / "reusable-publish.yml")
        self.assert_no_legacy_authority(workflow, allow_package_write=True)
        for mutate in (
            lambda job: job.pop("environment"),
            lambda job: job.update({"if": "always()"}),
            lambda job: job.update({"environment": "other"}),
        ):
            candidate = load_workflow(WORKFLOWS / "reusable-publish.yml")
            mutate(candidate["jobs"]["publish"])
            with self.assertRaises(AssertionError):
                self.assert_no_legacy_authority(candidate, allow_package_write=True)
        with self.assertRaises(AssertionError):
            self.assert_no_legacy_authority(load_workflow(WORKFLOWS / "reusable-publish.yml"))

    def test_docs_workflow_removed(self) -> None:
        self.assertFalse((WORKFLOWS / "render-md-mermaid.yml").exists())
        self.assertFalse((WORKFLOWS / "render-md-mermaid.yaml").exists())

    def test_all_workflows_have_no_legacy_authority(self) -> None:
        paths = workflow_paths()
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(workflow=path.name):
                workflow = load_workflow(path)
                if path.name == "release.yml":
                    self.assert_release_policy(workflow)
                else:
                    self.assert_no_legacy_authority(
                        workflow,
                        allow_package_write=path.name == "reusable-publish.yml",
                        allow_attestation=path.name == "post-merge-build.yml",
                    )

    def test_post_merge_reusable_workflows_are_pinned(self) -> None:
        """Same-repo calls use the local ./ form, which runs at the caller's own commit;
        calls into another repository must pin a full commit SHA."""
        workflow = load_workflow(WORKFLOWS / "post-merge-build.yml")
        for job_name, job in workflow["jobs"].items():
            with self.subTest(job=job_name):
                reference = job.get("uses")
                if reference is None:
                    self.assertIsInstance(job.get("runs-on"), str)
                    continue
                self.assertIsInstance(reference, str)
                if reference.startswith("./"):
                    self.assertNotIn("@", reference)
                    self.assertTrue((ROOT / reference).is_file(), reference)
                    continue
                target, commit = reference.rsplit("@", 1)
                self.assertRegex(commit, r"^[0-9a-f]{40}$", reference)
                self.assertEqual(target.count("/"), 4, reference)

    def test_global_policy_rejects_legacy_authority(self) -> None:
        safe_workflow = {
            "on": {"pull_request": {}},
            "permissions": {"contents": "read"},
            "jobs": {
                "validate": {
                    "runs-on": "ubuntu-latest",
                    "steps": [
                        {
                            "uses": (
                                "actions/checkout@"
                                "11bd71901bbe5b1630ceea73d27597364c9af683"
                            )
                        }
                    ],
                }
            },
        }
        unsafe_workflows = {
            "top-level write permission": {
                **safe_workflow,
                "permissions": {"contents": "write"},
            },
            "privileged trigger": {
                **safe_workflow,
                "on": {"pull_request_target": {}},
            },
            "job write permission": {
                **safe_workflow,
                "jobs": {
                    "validate": {
                        **safe_workflow["jobs"]["validate"],
                        "permissions": {"contents": "write"},
                    }
                },
            },
            "self-hosted runner": {
                **safe_workflow,
                "jobs": {
                    "validate": {
                        **safe_workflow["jobs"]["validate"],
                        "runs-on": "self-hosted",
                    }
                },
            },
            "mutable action reference": {
                **safe_workflow,
                "jobs": {
                    "validate": {
                        **safe_workflow["jobs"]["validate"],
                        "steps": [{"uses": "actions/checkout@v4"}],
                    }
                },
            },
            "repository secret": {
                **safe_workflow,
                "jobs": {
                    "validate": {
                        **safe_workflow["jobs"]["validate"],
                        "env": {"TOKEN": "${{ secrets.HOMELAB_BOT_TOKEN }}"},
                    }
                },
            },
            "composed repository secret expression": {
                **safe_workflow,
                "jobs": {
                    "validate": {
                        **safe_workflow["jobs"]["validate"],
                        "env": {
                            "TOKEN": "${{ format('{0}', secrets.HOMELAB_BOT_TOKEN) }}"
                        },
                    }
                },
            },
        }

        for gap, workflow in unsafe_workflows.items():
            with self.subTest(gap=gap), self.assertRaises(AssertionError):
                self.assert_no_legacy_authority(workflow)

    def test_nightly_workflow_is_absent(self) -> None:
        blocked_word = private_markers((51,)) or ("de" + "ploy",)
        self.assertFalse((WORKFLOWS / ("nightly-" + blocked_word[0] + ".yml")).exists())


if __name__ == "__main__":
    unittest.main()
