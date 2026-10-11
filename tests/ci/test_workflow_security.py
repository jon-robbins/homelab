from __future__ import annotations

import copy
import re
import subprocess
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
        self, workflow: dict[str, Any], *, allow_publish_secret: bool = False
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
                    if allow_publish_secret and key == "secrets":
                        self.assertEqual(child, {"GHCR_PUBLISH_TOKEN": {"required": "true"}})
                        continue
                    self.assertNotEqual(str(key).lower(), "secrets")
                    self.assertFalse(has_secrets_context_reference(str(key)), key)
                    assert_no_secrets(child)
            elif isinstance(value, list):
                for child in value:
                    assert_no_secrets(child)
            elif isinstance(value, str):
                if allow_publish_secret and value == "${{ secrets.GHCR_PUBLISH_TOKEN }}":
                    return
                self.assertFalse(has_secrets_context_reference(value), value)

        assert_no_secrets(workflow)

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
        self.assertIn('apps/homelab-workers/.venv/bin" >> "${GITHUB_PATH}"', worker_setup)
        self.assertIn('pip" install -e "${WORKERS_DIR}[dev]"', worker_setup)
        self.assertEqual(
            step_by_name["Workflow security policy"]["run"],
            "python3 -m pytest -q tests/ci/test_workflow_security.py",
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
        self.assertIn('apps/media-agent/.venv/bin" >> "${GITHUB_PATH}"', media_setup)
        self.assertIn('pip" install -e "${MEDIA_DIR}[dev]"', media_setup)
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

    def test_docs_workflow_removed(self) -> None:
        self.assertFalse((WORKFLOWS / "render-md-mermaid.yml").exists())
        self.assertFalse((WORKFLOWS / "render-md-mermaid.yaml").exists())

    def test_all_workflows_have_no_legacy_authority(self) -> None:
        paths = workflow_paths()
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(workflow=path.name):
                self.assert_no_legacy_authority(
                    load_workflow(path), allow_publish_secret=path.name == "reusable-publish.yml"
                )

    def test_post_merge_reusable_workflows_are_pinned(self) -> None:
        """Same-repo calls use the local ./ form, which runs at the caller's own commit;
        calls into another repository must pin a full commit SHA."""
        workflow = load_workflow(WORKFLOWS / "post-merge-build.yml")
        for job_name, job in workflow["jobs"].items():
            with self.subTest(job=job_name):
                reference = job.get("uses")
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
