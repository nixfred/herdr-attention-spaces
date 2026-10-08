import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import status_lights


class StatusLightsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"HERDR_SOCKET_PATH": "/tmp/test-herdr.sock",
                    "HERDR_PLUGIN_STATE_DIR": self.tmp.name}
        self.spaces = []
        self.moves = []
        self.focus = "pulse"
        self.mock = patch.object(status_lights, "request", self.request)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def populate(self, entries):
        self.spaces = [{"workspace_id": wid, "label": wid, "agent_status": state}
                       for wid, state in entries]

    def request(self, socket_path, method, params):
        if method == "workspace.list":
            return {"workspaces": list(self.spaces)}
        self.assertEqual(method, "workspace.move_block")
        self.assertIsNone(params["before_workspace_id"])
        self.moves.append(params)
        by_id = {space["workspace_id"]: space for space in self.spaces}
        self.spaces = [by_id[wid] for wid in params["workspace_ids"]]
        return {"workspaces": self.spaces}

    def event(self, name="pane_agent_status_changed", status="working"):
        self.env["HERDR_PLUGIN_EVENT_JSON"] = json.dumps({
            "event": name, "data": {"workspace_id": "pulse", "agent_status": status}})
        return status_lights.run(self.env)

    def ids(self):
        return [space["workspace_id"] for space in self.spaces]

    def test_completed_projects_above_running_projects(self):
        self.populate([(name, "idle") for name in ("info", "spaces", "govee", "burnbar", "sonos")]
                      + [("vm", "working"), ("pulse", "working"), ("shell", "unknown")])
        self.assertFalse(self.event())
        self.assertEqual(self.ids(), ["info", "spaces", "govee", "burnbar", "sonos", "vm", "pulse", "shell"])
        self.assertEqual(len(self.moves), 0)

    def test_all_colors_in_priority_order(self):
        self.populate([("dim", "unknown"), ("hollow", "idle"), ("yellow", "working"),
                       ("teal", "done"), ("red", "blocked")])
        status_lights.run(self.env, now=True)
        self.assertEqual(self.ids(), ["red", "teal", "hollow", "yellow", "dim"])

    def test_viewed_completion_stays_above_working(self):
        self.populate([("seen", "idle"), ("yellow", "working"), ("teal", "done")])
        self.event(status="idle")
        self.assertEqual(self.ids(), ["teal", "seen", "yellow"])

    def test_stale_blocked_event_uses_current_light(self):
        self.populate([("pulse", "idle"), ("yellow", "working")])
        self.event(status="blocked")
        self.assertEqual(self.ids(), ["pulse", "yellow"])

    def test_focus_preserves_same_color_order_and_does_not_move_again(self):
        self.populate([("seen", "idle"), ("vm", "working"), ("pulse", "working")])
        self.assertFalse(self.event(name="workspace_focused"))
        self.assertEqual(self.ids(), ["seen", "vm", "pulse"])
        self.assertEqual(self.moves, [])

    def test_focus_that_extinguishes_done_light_reconciles_order(self):
        self.populate([("pulse", "idle"), ("vm", "working")])
        self.event(name="tab.focused")
        self.assertEqual(self.ids(), ["pulse", "vm"])

    def test_new_shell_cannot_displace_lit_project(self):
        self.populate([("shell", "unknown"), ("pulse", "working")])
        self.event(name="workspace.created")
        self.assertEqual(self.ids(), ["pulse", "shell"])

    def test_closing_attention_pane_uses_new_workspace_light(self):
        self.populate([("pulse", "idle"), ("vm", "working")])
        self.event(name="pane_closed")
        self.assertEqual(self.ids(), ["pulse", "vm"])

    def test_reorder_event_ignored_to_avoid_feedback_loop(self):
        self.populate([("pulse", "idle"), ("vm", "working")])
        self.assertFalse(self.event(name="workspace_reordered"))
        self.assertEqual(self.moves, [])

    def test_empty_session_is_noop(self):
        self.assertFalse(status_lights.run(self.env, now=True))
        self.assertEqual(self.moves, [])


if __name__ == "__main__":
    unittest.main()
