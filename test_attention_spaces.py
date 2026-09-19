#!/usr/bin/env python3
"""Unit tests for attention_spaces.py against a fake Herdr socket API."""

import json
import tempfile
import unittest
from pathlib import Path

import attention_spaces


class FakeHerdr:
    """In-memory workspace list; workspace.move mutates order like the server."""

    def __init__(self, statuses, agents=None):
        self.workspaces = [{"workspace_id": wid, "label": wid.upper(), "agent_status": status,
                            "focused": index == 0}
                           for index, (wid, status) in enumerate(statuses)]
        self.agents = agents or []
        self.calls = []

    def request(self, _socket_path, method, params):
        self.calls.append((method, params))
        if method == "workspace.list":
            return {"workspaces": [dict(space) for space in self.workspaces]}
        if method == "agent.list":
            return {"agents": list(self.agents)}
        if method == "workspace.move":
            space = next(s for s in self.workspaces if s["workspace_id"] == params["workspace_id"])
            self.workspaces.remove(space)
            self.workspaces.insert(params["insert_index"], space)
            return {}
        raise AssertionError("unexpected method " + method)

    def order(self):
        return [space["workspace_id"] for space in self.workspaces]

    def moves(self):
        return [params["workspace_id"] for method, params in self.calls if method == "workspace.move"]


def event_env(state_dir, status, workspace_id="w3", pane_id="w3:p1", name="pane_agent_status_changed",
              config_dir=None):
    env = {
        "HERDR_SOCKET_PATH": "/tmp/fake.sock",
        "HERDR_PLUGIN_STATE_DIR": str(state_dir),
        "HERDR_PLUGIN_EVENT": name,
        "HERDR_PLUGIN_EVENT_JSON": json.dumps({"event": name, "data": {
            "type": name, "workspace_id": workspace_id, "pane_id": pane_id,
            "agent_status": status, "agent": "claude"}}),
    }
    if config_dir is not None:
        env["HERDR_PLUGIN_CONFIG_DIR"] = str(config_dir)
    return env


class AttentionSpacesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        self.fake = FakeHerdr([("w1", "working"), ("w2", "idle"), ("w3", "done"),
                               ("w4", "idle"), ("w5", "blocked")])
        attention_spaces.request = self.fake.request

    def tearDown(self):
        self.tmp.cleanup()

    def test_done_event_moves_space_to_top(self):
        self.fake.workspaces[4]["agent_status"] = "idle"
        self.assertTrue(attention_spaces.run(event_env(self.state, "done")))
        self.assertEqual(self.fake.order(), ["w3", "w1", "w2", "w4", "w5"])

    def test_blocked_event_moves_space_to_top(self):
        self.assertTrue(attention_spaces.run(event_env(self.state, "blocked", "w5", "w5:p1")))
        self.assertEqual(self.fake.order()[0], "w5")

    def test_idle_and_working_events_touch_nothing(self):
        for status in ("idle", "working", "unknown"):
            self.assertFalse(attention_spaces.run(event_env(self.state, status, "w2", "w2:p1")))
        self.assertEqual(self.fake.calls, [])

    def test_unrelated_event_ignored(self):
        env = event_env(self.state, "done", name="workspace_focused")
        self.assertFalse(attention_spaces.run(env))
        self.assertEqual(self.fake.calls, [])

    def test_already_on_top_is_not_moved_again(self):
        self.fake.workspaces[0]["agent_status"] = "done"
        self.fake.workspaces[2]["agent_status"] = "idle"
        self.fake.workspaces[4]["agent_status"] = "idle"
        self.assertFalse(attention_spaces.run(event_env(self.state, "done", "w1", "w1:p1")))
        self.assertEqual(self.fake.moves(), [])

    def test_stale_event_after_fred_looked_is_dropped(self):
        # Event said done, but by hook time the space is idle and its pane too.
        self.fake.workspaces[2]["agent_status"] = "idle"
        self.fake.agents = [{"pane_id": "w3:p1", "agent_status": "idle"}]
        self.assertFalse(attention_spaces.run(event_env(self.state, "done")))
        self.assertEqual(self.fake.moves(), [])

    def test_blocked_pane_beside_working_pane_still_promotes(self):
        self.fake.workspaces[2]["agent_status"] = "working"
        self.fake.agents = [{"pane_id": "w3:p1", "agent_status": "blocked"},
                            {"pane_id": "w3:p2", "agent_status": "working"}]
        self.assertTrue(attention_spaces.run(event_env(self.state, "blocked")))
        self.assertEqual(self.fake.order()[0], "w3")

    def test_closed_pane_does_not_promote(self):
        self.fake.workspaces[2]["agent_status"] = "idle"
        self.fake.agents = []
        self.assertFalse(attention_spaces.run(event_env(self.state, "done")))

    def test_missing_workspace_id_fails_loudly(self):
        env = event_env(self.state, "done", workspace_id="")
        with self.assertRaises(ValueError):
            attention_spaces.run(env)

    def test_unknown_workspace_is_ignored(self):
        self.assertFalse(attention_spaces.run(event_env(self.state, "done", "w9", "w9:p1")))
        self.assertEqual(self.fake.moves(), [])

    def test_sweep_moves_all_attention_spaces_keeping_order(self):
        env = event_env(self.state, "idle")
        self.assertTrue(attention_spaces.run(env, do_sweep=True))
        self.assertEqual(self.fake.order(), ["w5", "w3", "w1", "w2", "w4"])

    def test_done_lands_below_blocked(self):
        self.assertTrue(attention_spaces.run(event_env(self.state, "done")))
        self.assertEqual(self.fake.order(), ["w5", "w3", "w1", "w2", "w4"])

    def test_new_blocked_goes_above_existing_blocked_and_done(self):
        self.fake.workspaces = [{"workspace_id": w, "label": w, "agent_status": s, "focused": False}
                                for w, s in [("a", "blocked"), ("b", "done"), ("c", "idle"), ("d", "blocked")]]
        self.assertTrue(attention_spaces.run(event_env(self.state, "blocked", "d", "d:p1")))
        self.assertEqual(self.fake.order(), ["d", "a", "b", "c"])

    def test_new_done_leads_done_tier_under_blocked(self):
        self.fake.workspaces = [{"workspace_id": w, "label": w, "agent_status": s, "focused": False}
                                for w, s in [("a", "blocked"), ("b", "done"), ("c", "idle"), ("d", "done")]]
        self.assertTrue(attention_spaces.run(event_env(self.state, "done", "d", "d:p1")))
        self.assertEqual(self.fake.order(), ["a", "d", "b", "c"])

    def test_sweep_is_noop_when_attention_block_already_on_top(self):
        self.fake.workspaces = [{"workspace_id": w, "label": w, "agent_status": s, "focused": False}
                                for w, s in [("w5", "blocked"), ("w3", "done"), ("w1", "idle")]]
        self.assertFalse(attention_spaces.run(event_env(self.state, "idle"), do_sweep=True))
        self.assertEqual(self.fake.moves(), [])

    def test_config_can_restrict_statuses(self):
        config = Path(self.tmp.name) / "config"
        config.mkdir()
        (config / "attention.json").write_text(json.dumps({"statuses": ["blocked"]}))
        self.assertFalse(attention_spaces.run(event_env(self.state, "done", config_dir=config)))
        self.assertEqual(self.fake.calls, [])
        self.assertTrue(attention_spaces.run(event_env(self.state, "blocked", "w5", "w5:p1",
                                                       config_dir=config)))

    def test_done_event_keeps_hidden_blocked_workspace_above_it(self):
        self.fake.workspaces[4]["agent_status"] = "working"
        self.fake.agents = [{"workspace_id": "w5", "pane_id": "w5:p1", "agent_status": "blocked"},
                            {"workspace_id": "w5", "pane_id": "w5:p2", "agent_status": "working"}]
        attention_spaces.run(event_env(self.state, "done"))
        self.assertEqual(self.fake.order()[:2], ["w5", "w3"])

    def test_sweep_finds_hidden_attention_across_workspaces(self):
        self.fake.workspaces[4]["agent_status"] = "working"
        self.fake.workspaces[2]["agent_status"] = "working"
        self.fake.agents = [{"workspace_id": "w5", "pane_id": "w5:p1", "agent_status": "blocked"},
                            {"workspace_id": "w3", "pane_id": "w3:p1", "agent_status": "done"}]
        attention_spaces.run(event_env(self.state, "idle"), do_sweep=True)
        self.assertEqual(self.fake.order(), ["w5", "w3", "w1", "w2", "w4"])

    def test_finishing_pane_does_not_demote_blocked_sibling(self):
        self.fake.workspaces[2]["agent_status"] = "working"
        self.fake.agents = [{"workspace_id": "w3", "pane_id": "w3:p1", "agent_status": "idle"},
                            {"workspace_id": "w3", "pane_id": "w3:p2", "agent_status": "blocked"}]
        attention_spaces.run(event_env(self.state, "working"))
        attention_spaces.run(event_env(self.state, "idle"))
        self.assertEqual(self.fake.order()[:2], ["w3", "w5"])

    def test_seen_completion_stays_below_hidden_blocked_workspace(self):
        self.fake.workspaces[4]["agent_status"] = "working"
        self.fake.workspaces[2]["agent_status"] = "idle"
        self.fake.agents = [{"workspace_id": "w5", "pane_id": "w5:p1", "agent_status": "blocked"},
                            {"workspace_id": "w3", "pane_id": "w3:p1", "agent_status": "idle"}]
        attention_spaces.run(event_env(self.state, "working"))
        attention_spaces.run(event_env(self.state, "idle"))
        self.assertEqual(self.fake.order()[:2], ["w5", "w3"])

    def test_bad_config_fails_loudly(self):
        config = Path(self.tmp.name) / "config"
        config.mkdir()
        (config / "attention.json").write_text(json.dumps({"statuses": []}))
        with self.assertRaises(ValueError):
            attention_spaces.run(event_env(self.state, "done", config_dir=config))


class FinishedWhileWatchedTest(unittest.TestCase):
    """A pane that finishes in the space Fred is looking at.

    Herdr reports `done` only for an unseen tab, so a watched space reports
    plain `idle` and used to be the one space that never rose.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        self.config = Path(self.tmp.name) / "config"
        self.config.mkdir()
        self.fake = FakeHerdr([("w1", "working"), ("w2", "idle"), ("w3", "idle"),
                               ("w4", "idle"), ("w5", "blocked")])
        self.fake.agents = [{"pane_id": "w3:p1", "agent_status": "idle"}]
        attention_spaces.request = self.fake.request

    def tearDown(self):
        self.tmp.cleanup()

    def working_then(self, status, **kwargs):
        attention_spaces.run(event_env(self.state, "working", **kwargs))
        return attention_spaces.run(event_env(self.state, status, **kwargs))

    def test_working_then_idle_moves_the_watched_space_up(self):
        self.fake.workspaces[4]["agent_status"] = "idle"   # nothing blocked above it
        self.assertTrue(self.working_then("idle"))
        self.assertEqual(self.fake.order()[0], "w3")

    def test_it_lands_below_a_blocked_space(self):
        # w5 is blocked in the fixture: waiting on Fred still outranks finished.
        self.assertTrue(self.working_then("idle"))
        self.assertEqual(self.fake.order()[:2], ["w5", "w3"])

    def test_focusing_a_finished_space_does_not_promote_it_again(self):
        # done -> idle is Herdr marking the tab seen, not new work finishing.
        attention_spaces.run(event_env(self.state, "done"))
        before = self.fake.order()
        self.assertFalse(attention_spaces.run(event_env(self.state, "idle")))
        self.assertEqual(self.fake.order(), before)

    def test_idle_with_no_history_is_ignored(self):
        self.assertFalse(attention_spaces.run(event_env(self.state, "idle")))
        self.assertEqual(self.fake.moves(), [])

    def test_an_agent_working_again_by_hook_time_is_not_promoted(self):
        attention_spaces.run(event_env(self.state, "working"))
        self.fake.agents = [{"pane_id": "w3:p1", "agent_status": "working"}]
        self.assertFalse(attention_spaces.run(event_env(self.state, "idle")))
        self.assertEqual(self.fake.moves(), [])

    def test_a_closed_pane_is_not_promoted(self):
        attention_spaces.run(event_env(self.state, "working"))
        self.fake.agents = []
        self.assertFalse(attention_spaces.run(event_env(self.state, "idle")))

    def test_a_vanished_workspace_is_ignored(self):
        self.assertFalse(self.working_then("idle", workspace_id="w9", pane_id="w9:p1"))

    def test_config_can_turn_completions_off(self):
        (self.config / "attention.json").write_text(
            json.dumps({"promote_focused_completions": False}))
        self.assertFalse(self.working_then("idle", config_dir=self.config))
        self.assertEqual(self.fake.moves(), [])

    def test_a_status_list_without_done_excludes_completions(self):
        (self.config / "attention.json").write_text(json.dumps({"statuses": ["blocked"]}))
        self.assertFalse(self.working_then("idle", config_dir=self.config))

    def test_bad_completion_setting_fails_loudly(self):
        (self.config / "attention.json").write_text(
            json.dumps({"promote_focused_completions": "yes"}))
        with self.assertRaises(ValueError):
            self.working_then("idle", config_dir=self.config)

    def test_each_pane_is_remembered_separately(self):
        self.fake.agents = [{"pane_id": "w3:p1", "agent_status": "idle"},
                            {"pane_id": "w4:p1", "agent_status": "idle"}]
        attention_spaces.run(event_env(self.state, "working", workspace_id="w4", pane_id="w4:p1"))
        # w3 has no working history of its own, so its idle is still ignored.
        self.assertFalse(attention_spaces.run(event_env(self.state, "idle")))
        self.assertTrue(attention_spaces.run(
            event_env(self.state, "idle", workspace_id="w4", pane_id="w4:p1")))
        self.assertEqual(self.fake.order()[:2], ["w5", "w4"])

    def test_history_survives_between_runs_and_is_written_once(self):
        attention_spaces.run(event_env(self.state, "working"))
        stored = json.loads(next(self.state.glob("*.status.json")).read_text())
        self.assertEqual(stored["w3:p1"], "working")

    def test_unreadable_history_is_treated_as_empty(self):
        self.state.mkdir(parents=True, exist_ok=True)
        attention_spaces.run(event_env(self.state, "working"))
        path = next(self.state.glob("*.status.json"))
        path.write_text("{ not json")
        self.assertFalse(attention_spaces.run(event_env(self.state, "idle")))


if __name__ == "__main__":
    unittest.main()
