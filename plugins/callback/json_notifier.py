# -*- coding: utf-8 -*-

# (c) 2022, Exotec <jerome.boulmier@exotec.com>
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from typing import Optional

DOCUMENTATION = """
    callback: json_notifier
    short_description: Send event as JSON to a webhook
    description:
        - This callback converts all events into JSON output to stdout
    type: stdout
    requirements:
      - requests
      - Set as stdout in config
    options:
      json_webhook_url:
        required: True
        name: JSON Webhook URL
        description: 'Send the json to the given url'
        env:
          - name: JSON_WEBHOOK_URL
        ini:
          - key: json_webhook_url
            section: callback_json_notifier
      json_verbose:
        required: False
        name: JSON verbose task output
        description: 'When true, send every task to the API with full STDOUT/STDERR, including skipped tasks (debug only)'
        type: bool
        default: False
        env:
          - name: JSON_VERBOSE
        ini:
          - key: json_verbose
            section: callback_json_notifier
"""

from datetime import datetime, timezone
import json
from functools import partial

from ansible.module_utils._text import to_text
from ansible.module_utils.urls import open_url
from ansible.parsing.ajson import AnsibleJSONEncoder
from ansible.plugins.callback import CallbackBase


def current_time():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "exotec.utils.json_notifier"
    CALLBACK_NEEDS_WHITELIST = True

    # Verbose keys (task output / debug details like stdout/stderr, invocation, diff)
    # are removed from the payload sent to the deployer API for OK/SKIPPED tasks.
    # We keep status/state keys (ok, failed, skipped, unreachable, ignored, changed,
    # action) and functional data such as `ansible_facts` and `results` untouched so the deployer logic keeps working.
    _VERBOSE_KEYS = (
        "stdout", "stderr", "stdout_lines", "stderr_lines",
        "module_stdout", "module_stderr", "invocation", "diff",
    )

    def __init__(self, display=None):
        super(CallbackModule, self).__init__(display)
        self._last_play = None
        self._callback_url: Optional[str] = None
        self._verbose = False

    def set_options(self, task_keys=None, var_options=None, direct=None):

        super(CallbackModule, self).set_options(
            task_keys=task_keys, var_options=var_options, direct=direct
        )

        self._callback_url = self.get_option("json_webhook_url")
        self._verbose = bool(self.get_option("json_verbose"))

        if self._callback_url is None:
            self.disabled = True
            self._display.warning(
                "JSON Webhook URL was not provided. The "
                "JSON Webhook URL can be provided using "
                "the `JSON_WEBHOOK_URL` environment "
                "variable."
            )

    def send_msg(self, msg):
        headers = {
            "Content-type": "application/json",
        }

        data = json.dumps(msg, cls=AnsibleJSONEncoder)
        self._display.debug(data)
        self._display.debug(self._callback_url)
        try:
            response = open_url(self._callback_url, data=data, headers=headers)
            return response.read()
        except Exception as e:
            self._display.warning(
                "Could not submit message to webhook: %s" % to_text(e)
            )

    def v2_playbook_on_play_start(self, play):
        self._last_play = to_text(play._uuid)
        event = {
            "type": "play_start",
            "id": to_text(play._uuid),
            "start": current_time(),
            "name": play.get_name(),
        }
        self.send_msg(event)

    def v2_runner_on_start(self, host, task):
        event = {
            "type": "task_host_start",
            "play": self._last_play,
            "id": to_text(task._uuid),
            "path": task.get_path(),
            "name": task.get_name(),
            "start": current_time(),
            "host": host.get_name(),
        }
        self.send_msg(event)

    def v2_playbook_on_task_start(self, task, is_conditional):
        event = {
            "type": "task_start",
            "play": self._last_play,
            "id": to_text(task._uuid),
            "path": task.get_path(),
            "name": task.get_name(),
            "start": current_time(),
        }
        self.send_msg(event)

    def v2_playbook_on_handler_task_start(self, task):
        event = {
            "type": "task__handler_start",
            "play": self._last_play,
            "id": to_text(task._uuid),
            "path": task.get_path(),
            "name": task.get_name(),
            "start": current_time(),
            "notified_host": [host.get_name() for host in task.notified_hosts],
        }
        self.send_msg(event)

    def v2_playbook_on_stats(self, stats):
        hosts = sorted(stats.processed.keys())

        summary = {}
        for h in hosts:
            s = stats.summarize(h)
            summary[h] = s

        event = {"type": "playbook_end", "end": current_time(), "result": summary}
        self.send_msg(event)

    def _record_task_result(self, on_info, result, strip_verbose=False, skip_send=False, **kwargs):
        host = result._host
        task = result._task

        result_copy = result._result.copy()
        result_copy.update(on_info)
        result_copy["action"] = task.action

        if (
            hasattr(task, "ignore_errors")
            and task.ignore_errors
            and on_info.get("failed")
        ):
            result_copy["ignored"] = True

        # For OK/SKIPPED tasks, drop verbose output/debug fields (e.g. stdout/stderr,
        # invocation metadata, diff) from the payload sent to the deployer API while
        # keeping the task name and its state.
        if strip_verbose:
            api_result = {k: v for k, v in result_copy.items() if k not in self._VERBOSE_KEYS}

        event = {
            "type": "task_host_end",
            "play": self._last_play,
            "id": to_text(task._uuid),
            "path": task.get_path(),
            "name": task.get_name(),
            "end": current_time(),
            "host": host.get_name(),
            "result": api_result,
        }
        # Skipped tasks are not sent to the deployer API unless verbose debug
        # mode is enabled.
        if not skip_send:
            self.send_msg(event)

    def __getattribute__(self, name):
        """Return ``_record_task_result`` partial with a dict containing skipped/failed if necessary"""
        if name not in (
            "v2_runner_on_ok",
            "v2_runner_on_failed",
            "v2_runner_on_unreachable",
            "v2_runner_on_skipped",
        ):
            return object.__getattribute__(self, name)

        on = name.rsplit("_", 1)[1]

        on_info = {}
        # unreachable is already in the dict result_copy (method `_record_task_result`).
        if on in ("failed", "skipped", "ok"):
            on_info[on] = True

        verbose = object.__getattribute__(self, "_verbose")
        # Only OK and SKIPPED tasks have their STDOUT/STDERR stripped from the
        # payload sent to the deployer API, unless verbose debug mode is enabled.
        strip_verbose = on in ("ok", "skipped") and not verbose
        # Skipped tasks are not sent to the deployer API at all, unless verbose
        # debug mode is enabled.
        skip_send = on == "skipped" and not verbose

        return partial(self._record_task_result, on_info, strip_verbose=strip_verbose, skip_send=skip_send)
