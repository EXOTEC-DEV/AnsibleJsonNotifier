import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "callback"))

from json_notifier import CallbackModule


class FakeTask:
    action = "ansible.builtin.command"
    _uuid = "task-uuid"
    ignore_errors = False

    def get_path(self):
        return "/playbooks/play.yml:12"

    def get_name(self):
        return "my task"


class FakeHost:
    def get_name(self):
        return "host1"


class FakeResult:
    def __init__(self, result):
        self._result = result
        self._host = FakeHost()
        self._task = FakeTask()


def make_callback(verbose=False):
    cb = CallbackModule()
    cb._callback_url = "http://127.0.0.1/unused"
    cb._verbose = verbose
    sent = []
    cb.send_msg = sent.append
    return cb, sent


def test_failed_task_is_sent_with_its_error_output():
    cb, sent = make_callback()
    cb.v2_runner_on_failed(FakeResult({"msg": "boom", "stderr": "err", "stdout": "out"}))
    assert len(sent) == 1, "the failure event must reach the deployer"
    result = sent[0]["result"]
    assert result["failed"] is True
    assert result["stderr"] == "err"
    assert result["msg"] == "boom"
    assert "ok" not in result


def test_unreachable_task_is_sent():
    cb, sent = make_callback()
    cb.v2_runner_on_unreachable(FakeResult({"unreachable": True, "msg": "no route"}))
    assert len(sent) == 1
    assert sent[0]["result"]["unreachable"] is True


def test_ok_task_keeps_its_state_and_drops_verbose_output():
    cb, sent = make_callback()
    cb.v2_runner_on_ok(FakeResult({"changed": False, "stdout": "hello", "invocation": {}}))
    assert len(sent) == 1
    result = sent[0]["result"]
    assert result["ok"] is True
    assert "stdout" not in result
    assert "invocation" not in result
