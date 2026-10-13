import pytest

from scripts.release import reconcile
from scripts.release.verify import VerificationError


def _ruleset(**overrides):
    detail = {
        "id": 7,
        "name": "main",
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
        "rules": [{"type": "pull_request"}, {"type": "non_fast_forward"}, {"type": "deletion"}],
    }
    detail.update(overrides)
    return detail


class RulesetClient:
    def __init__(self, detail, default_branch="main"):
        self.detail = detail
        self.default_branch = default_branch

    def repository(self, repository):
        return {"default_branch": self.default_branch}

    def rulesets(self, repository):
        return [{"id": 7, "name": "main"}]

    def ruleset(self, repository, ruleset_id):
        assert ruleset_id == 7
        return self.detail


def test_restrictive_ruleset_passes():
    reconcile._check_homelab_ruleset(RulesetClient(_ruleset()), "jon-robbins/homelab")


def test_hidden_bypass_actors_fail_closed_with_token_hint():
    # GitHub omits bypass_actors when the caller lacks Administration read.
    detail = _ruleset()
    del detail["bypass_actors"]
    with pytest.raises(VerificationError, match="Administration: Read"):
        reconcile._check_homelab_ruleset(RulesetClient(detail), "jon-robbins/homelab")


@pytest.mark.parametrize("overrides", [
    {"bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]},
    {"enforcement": "evaluate"},
    {"enforcement": "disabled"},
])
def test_weakened_ruleset_fails(overrides):
    with pytest.raises(VerificationError, match="weakened"):
        reconcile._check_homelab_ruleset(RulesetClient(_ruleset(**overrides)), "jon-robbins/homelab")


def test_missing_required_rule_fails():
    with pytest.raises(VerificationError, match="require PR"):
        reconcile._check_homelab_ruleset(RulesetClient(_ruleset(rules=[{"type": "pull_request"}])), "jon-robbins/homelab")


def _targets(include, exclude=()):
    return {"ref_name": {"include": list(include), "exclude": list(exclude)}}


@pytest.mark.parametrize("include", [["refs/heads/main"], ["~DEFAULT_BRANCH"], ["~ALL"]])
def test_ruleset_targeting_main_passes(include):
    reconcile._check_homelab_ruleset(RulesetClient(_ruleset(conditions=_targets(include))), "jon-robbins/homelab")


@pytest.mark.parametrize("conditions,default_branch", [
    (_targets(["~DEFAULT_BRANCH"]), "trunk"),
    (_targets(["refs/heads/release"]), "main"),
    (_targets(["~ALL"], ["refs/heads/main"]), "main"),
    (_targets(["~ALL"], ["~DEFAULT_BRANCH"]), "main"),
])
def test_ruleset_not_covering_main_fails(conditions, default_branch):
    client = RulesetClient(_ruleset(conditions=conditions), default_branch=default_branch)
    with pytest.raises(VerificationError, match="does not target main"):
        reconcile._check_homelab_ruleset(client, "jon-robbins/homelab")


class CheckReadClient(RulesetClient):
    def ref(self, repository, branch):
        return "a" * 40

    def artifacts(self, repository):
        return {"artifacts": [{"name": "agent"}]}


def test_check_read_reports_ruleset(capsys):
    assert all(reconcile.check_read(CheckReadClient(_ruleset())))
    assert "PASS read homelab main ruleset" in capsys.readouterr().out


def test_check_read_fails_when_token_cannot_see_bypass_actors(capsys):
    detail = _ruleset()
    del detail["bypass_actors"]
    assert reconcile.check_read(CheckReadClient(detail)) == [True, True, False, True]
    assert "FAIL read homelab main ruleset" in capsys.readouterr().out
