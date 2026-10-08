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
    def test_working_projects_above_viewed_idle_projects(self):
        # Fred's 2026-09-19 screenshot: yellow VM-control and Pulse-merge sat
        # below five idle agents. Reported wrong then and again 2026-10-08.
        self.populate([(name, "idle") for name in ("info", "spaces", "govee", "burnbar", "sonos")]
                      + [("vm", "working"), ("pulse", "working"), ("shell", "unknown")])
        self.event()
        self.assertEqual(self.ids(), ["vm", "pulse", "info", "spaces", "govee", "burnbar", "sonos", "shell"])

    def test_all_colors_in_priority_order(self):
        self.populate([("dim", "unknown"), ("hollow", "idle", 10), ("yellow", "working"),
                       ("teal", "done", 20), ("red", "blocked", 5)])
        status_lights.run(self.env, now=True)
        self.assertEqual(self.ids(), ["red", "teal", "yellow", "hollow", "dim"])

    def test_viewed_completion_drops_below_working(self):
        self.populate([("seen", "idle", 10), ("yellow", "working"), ("teal", "done", 20)])
        self.event(status="idle")
        self.assertEqual(self.ids(), ["teal", "yellow", "seen"])

    def test_stale_blocked_event_uses_current_light(self):
        self.populate([("pulse", "idle"), ("yellow", "working")])
        self.event(status="blocked")
        self.assertEqual(self.ids(), ["yellow", "pulse"])

    def test_focus_preserves_same_color_order_and_does_not_move_again(self):
        self.populate([("vm", "working"), ("pulse", "working"), ("seen", "idle")])
        self.assertFalse(self.event(name="workspace_focused"))
        self.assertEqual(self.ids(), ["vm", "pulse", "seen"])
        self.assertEqual(self.moves, [])

    def test_focus_that_extinguishes_done_light_reconciles_order(self):
        self.populate([("pulse", "idle"), ("vm", "working")])
        self.event(name="tab.focused")
        self.assertEqual(self.ids(), ["vm", "pulse"])

    def test_new_shell_cannot_displace_lit_project(self):
        self.populate([("shell", "unknown"), ("pulse", "working")])
        self.event(name="workspace.created")
        self.assertEqual(self.ids(), ["pulse", "shell"])

    def test_closing_attention_pane_uses_new_workspace_light(self):
        self.populate([("pulse", "idle"), ("vm", "working")])
        self.event(name="pane_closed")
        self.assertEqual(self.ids(), ["vm", "pulse"])

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
    attn needed. Lock that in, we have had to fix this several times." Then,
    same night: "The green should be above the idle projects correct?"
    The list reads in light order: red, teal, green, hollow, dim.
    Each test is one rule of that contract. A failure here is a regression."""

    def test_screenshot_2026_10_08_green_above_hollow(self):
        # Second screenshot: eight hollow idle rings sat above green
        # omaspotify and clarity. Green belongs above every hollow ring.
        self.populate([("Daniel", "idle", 233), ("herdr", "idle", 220), ("Larry", "idle", 210),
                       ("infomarchy", "idle", 187), ("districts", "idle", 140),
                       ("harness", "idle", 130), ("pulse", "idle", 106), ("blip", "idle"),
                       ("omaspotify", "working"), ("clarity", "working"),
                       ("flea", "unknown"), ("atmos", "unknown")])
        status_lights.run(self.env, now=True)
        self.assertEqual(self.ids(), ["omaspotify", "clarity", "Daniel", "herdr", "Larry",
                                      "infomarchy", "districts", "harness", "pulse", "blip",
                                      "flea", "atmos"])

    def test_just_finished_lands_directly_under_attention(self):
        # First 2026-10-08 bug: a stable sort left a fresh finish below every
        # older finish. Now it jumps over working and old idle projects.
        self.populate([("old1", "idle", 10), ("old2", "idle", 20), ("red", "blocked", 5),
                       ("busy", "working"), ("clarity", "working"), ("shell", "unknown")])
        status_lights.run(self.env, now=True)
        self.finish("clarity", 40)
        self.event(status="idle")
        self.assertEqual(self.ids(), ["red", "clarity", "busy", "old2", "old1", "shell"])

    def test_attention_always_above_a_newer_finish(self):
        self.populate([("fresh", "done", 99), ("waiting", "blocked", 1)])
        status_lights.run(self.env, now=True)
        self.assertEqual(self.ids(), ["waiting", "fresh"])

    def test_newest_attention_first(self):
        self.populate([("asked_earlier", "blocked", 10), ("asked_now", "blocked", 50)])
        status_lights.run(self.env, now=True)
        self.assertEqual(self.ids(), ["asked_now", "asked_earlier"])

    def test_newest_unseen_finish_first(self):
        self.populate([("done_earlier", "done", 10), ("done_now", "done", 50)])
        status_lights.run(self.env, now=True)
        self.assertEqual(self.ids(), ["done_now", "done_earlier"])

    def test_unseen_finish_above_working_above_viewed_finish(self):
        self.populate([("viewed_new", "idle", 40), ("busy", "working"), ("unseen_old", "done", 10)])
        status_lights.run(self.env, now=True)
        self.assertEqual(self.ids(), ["unseen_old", "busy", "viewed_new"])

    def test_looking_at_a_finished_project_drops_it_below_working(self):
        self.populate([("a", "done", 30), ("b", "done", 20), ("c", "working")])
        status_lights.run(self.env, now=True)
        self.spaces[1]["agent_status"] = "idle"      # Fred opens b: teal -> hollow
        self.event(name="workspace_focused")
        self.assertEqual(self.ids(), ["a", "c", "b"])

    def test_hollow_rings_newest_finish_first_no_number_last(self):
        self.populate([("ready", "idle"), ("old", "idle", 5), ("new", "idle", 50)])
        status_lights.run(self.env, now=True)
        self.assertEqual(self.ids(), ["new", "old", "ready"])

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
