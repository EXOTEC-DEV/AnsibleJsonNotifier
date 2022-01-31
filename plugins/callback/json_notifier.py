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
"""

import datetime
import json
from functools import partial

from ansible.module_utils._text import to_text
from ansible.module_utils.urls import open_url
from ansible.parsing.ajson import AnsibleJSONEncoder
from ansible.plugins.callback import CallbackBase


def current_time():
    return "%sZ" % datetime.datetime.utcnow().isoformat()


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "exotec.utils.json_notifier"
    CALLBACK_NEEDS_WHITELIST = True

    def __init__(self, display=None):
        super(CallbackModule, self).__init__(display)
        self._last_play = None
        self._callback_url: Optional[str] = None

    def set_options(self, task_keys=None, var_options=None, direct=None):

        super(CallbackModule, self).set_options(
            task_keys=task_keys, var_options=var_options, direct=direct
        )

        self._callback_url = self.get_option("json_webhook_url")

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
                u"Could not submit message to webhook: %s" % to_text(e)
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

    def _record_task_result(self, on_info, result, **kwargs):
        host = result._host
        task = result._task

        result_copy = result._result.copy()
        result_copy.update(on_info)
        result_copy["action"] = task.action

        event = {
            "type": "task_host_end",
            "play": self._last_play,
            "id": to_text(task._uuid),
            "path": task.get_path(),
            "name": task.get_name(),
            "end": current_time(),
            "host": host.get_name(),
            "result": result_copy,
        }
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
        # unreachable is already in the dict.
        if on in ("failed", "skipped", "ok"):
            on_info[on] = True

        return partial(self._record_task_result, on_info)
