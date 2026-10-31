import json

import pytest

from setup.engine.checkpoints import AlreadyRunning, Checkpoints


def test_state_survives_reopening(tmp_path):
    first = Checkpoints(tmp_path)
    first.begin("A-1")
    first.done("A-1", "ok")
    first.set_fact("tailscale_ip", "192.0.2.1")
    first.set_answer("domain", "example.test")
    second = Checkpoints(tmp_path)
    assert second.status("A-1") == "done"
    assert second.facts() == {"tailscale_ip": "192.0.2.1"}
    assert second.answers() == {"domain": "example.test"}


def test_begin_without_done_reads_as_interrupted(tmp_path):
    Checkpoints(tmp_path).begin("A-2")
    assert Checkpoints(tmp_path).status("A-2") == "started"
    assert Checkpoints(tmp_path).interrupted() == ("A-2",)


def test_failure_is_recorded_with_its_message(tmp_path):
    checkpoints = Checkpoints(tmp_path)
    checkpoints.failed("A-3", "docker said no")
    assert Checkpoints(tmp_path).message("A-3") == "docker said no"


def test_a_corrupt_file_is_an_error_not_a_silent_reset(tmp_path):
    (tmp_path / "checkpoints.json").write_text("{not json")
    with pytest.raises(ValueError, match="checkpoints.json"):
        Checkpoints(tmp_path)


def test_a_second_run_cannot_start_while_one_holds_the_lock(tmp_path):
    with Checkpoints(tmp_path).lock():
        with pytest.raises(AlreadyRunning):
            with Checkpoints(tmp_path).lock():
                pass
    with Checkpoints(tmp_path).lock():
        pass


def test_file_is_plain_json_with_a_version(tmp_path):
    Checkpoints(tmp_path).done("A-1", "ok")
    assert json.loads((tmp_path / "checkpoints.json").read_text())["version"] == 1


def test_lock_refreshes_history_written_since_context_creation(tmp_path):
    waiting = Checkpoints(tmp_path)
    Checkpoints(tmp_path).done("previous")
    with waiting.lock():
        waiting.begin("next")
    assert Checkpoints(tmp_path).completed("previous")
