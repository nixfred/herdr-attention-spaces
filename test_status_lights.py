import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import status_lights


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"HERDR_SOCKET_PATH": "/tmp/test-herdr.sock",
                    "HERDR_PLUGIN_STATE_DIR": self.tmp.name}
        self.spaces = []
        self.agents = []
        self.moves = []
        self.focus = "pulse"
        self.mock = patch.object(status_lights, "request", self.request)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def populate(self, entries):
        # entries: (id, status) or (id, status, seq). seq is the completion_seq
        # of a finished agent, or the state_change_seq of a blocked one.
        self.spaces, self.agents = [], []
        for entry in entries:
            wid, state, seq = (entry + (None,))[:3]
            self.spaces.append({"workspace_id": wid, "label": wid, "agent_status": state})
            if state != "unknown":
                agent = {"workspace_id": wid, "pane_id": wid + ":p1", "agent_status": state,
                         "state_change_seq": seq or 0}
                if state in ("idle", "done") and seq is not None:
                    agent["completion_seq"] = seq
                self.agents.append(agent)

    def finish(self, wid, seq):
        """Agent in wid goes working -> idle at server seq number seq."""
        for space in self.spaces:
            if space["workspace_id"] == wid:
                space["agent_status"] = "done"
        for agent in self.agents:
            if agent["workspace_id"] == wid:
                agent.update(agent_status="idle", state_change_seq=seq, completion_seq=seq)

    def request(self, socket_path, method, params):
        if method == "workspace.list":
            return {"workspaces": list(self.spaces)}
        if method == "agent.list":
            return {"agents": list(self.agents)}
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


class StatusLightsTest(Harness):
    def test_completed_projects_above_running_projects(self):
        self.populate([(name, "idle") for name in ("info", "spaces", "govee", "burnbar", "sonos")]
                      + [("vm", "working"), ("pulse", "working"), ("shell", "unknown")])
        self.assertFalse(self.event())
        self.assertEqual(self.ids(), ["info", "spaces", "govee", "burnbar", "sonos", "vm", "pulse", "shell"])
        self.assertEqual(len(self.moves), 0)

    def test_all_colors_in_priority_order(self):
        self.populate([("dim", "unknown"), ("hollow", "idle", 10), ("yellow", "working"),
                       ("teal", "done", 20), ("red", "blocked", 5)])
        status_lights.run(self.env, now=True)
        self.assertEqual(self.ids(), ["red", "teal", "hollow", "yellow", "dim"])

    def test_viewed_completion_stays_above_working(self):
        self.populate([("seen", "idle", 10), ("yellow", "working"), ("teal", "done", 20)])
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


class LockedContractTest(Harness):
    """Fred, 2026-10-08: "projects that need attn OR just finished running should
    sort to the top. Attn needed at the top and just finished sort just under
    attn needed. Lock that in, we have had to fix this several times."
    Each test is one rule of that contract. A failure here is a regression."""

    def test_just_finished_lands_directly_under_attention(self):
        # The 2026-10-08 bug: stable sort dropped a fresh finish to the bottom
        # of the finished group, under every older completion.
        self.populate([("old1", "idle", 10), ("old2", "idle", 20), ("old3", "idle", 30),
                       ("red", "blocked", 5), ("clarity", "working"), ("shell", "unknown")])
        status_lights.run(self.env, now=True)
        self.finish("clarity", 40)
        self.event(status="idle")
        self.assertEqual(self.ids(), ["red", "clarity", "old3", "old2", "old1", "shell"])

    def test_screenshot_2026_10_08(self):
        # Fred's sidebar: six idle Claudes in list order, three working.
        # blip finished most recently, so it belongs first.
        self.populate([("Larry", "idle", 210), ("infomarchy", "idle", 187),
                       ("districts", "idle", 140), ("harness", "idle", 130),
                       ("pulse", "idle", 106), ("blip", "idle", 209),
                       ("herdr", "working"), ("Daniel", "working"), ("clarity", "working"),
                       ("flea", "unknown"), ("atmos", "unknown")])
        status_lights.run(self.env, now=True)
        self.assertEqual(self.ids(), ["Larry", "blip", "infomarchy", "districts", "harness",
                                      "pulse", "herdr", "Daniel", "clarity", "flea", "atmos"])

    def test_attention_always_above_a_newer_finish(self):
        self.populate([("fresh", "done", 99), ("waiting", "blocked", 1)])
        status_lights.run(self.env, now=True)
        self.assertEqual(self.ids(), ["waiting", "fresh"])

    def test_newest_attention_first(self):
        self.populate([("asked_earlier", "blocked", 10), ("asked_now", "blocked", 50)])
        status_lights.run(self.env, now=True)
        self.assertEqual(self.ids(), ["asked_now", "asked_earlier"])

    def test_viewing_a_finished_project_does_not_move_it(self):
        self.populate([("a", "done", 30), ("b", "done", 20), ("c", "working")])
        status_lights.run(self.env, now=True)
        self.spaces[1]["agent_status"] = "idle"      # Fred opens b: done -> idle (seen)
        self.assertFalse(self.event(name="workspace_focused"))
        self.assertEqual(self.ids(), ["a", "b", "c"])

    def test_unseen_and_viewed_finishes_share_one_group_by_recency(self):
        self.populate([("unseen_old", "done", 10), ("viewed_new", "idle", 40)])
        status_lights.run(self.env, now=True)
        self.assertEqual(self.ids(), ["viewed_new", "unseen_old"])

    def test_finished_without_completion_number_sits_at_bottom_of_group(self):
        self.populate([("ready", "idle"), ("done_old", "idle", 5), ("yellow", "working")])
        status_lights.run(self.env, now=True)
        self.assertEqual(self.ids(), ["done_old", "ready", "yellow"])

    def test_multi_agent_workspace_uses_its_latest_finish(self):
        self.populate([("solo", "idle", 30), ("pair", "idle", 10)])
        self.agents.append({"workspace_id": "pair", "pane_id": "pair:p2", "agent_status": "idle",
                            "state_change_seq": 45, "completion_seq": 45})
        status_lights.run(self.env, now=True)
        self.assertEqual(self.ids(), ["pair", "solo"])

    def test_working_and_unknown_keep_their_order(self):
        self.populate([("w2", "working"), ("u1", "unknown"), ("w1", "working"), ("u2", "unknown")])
        status_lights.run(self.env, now=True)
        self.assertEqual(self.ids(), ["w2", "w1", "u1", "u2"])


if __name__ == "__main__":
    unittest.main()
