#!/usr/bin/env python3
"""Sort Herdr projects by their status light: red, teal, green, hollow, dim.

LOCKED CONTRACT (Fred, 2026-10-08, OMW-HERDR-ORDER). Do not change without him.
  1. Needs attention (blocked, red) at the very top, newest first.
  2. Just finished (done, teal check: finished and not looked at yet) directly
     under it, most recent finish first, by Herdr's completion_seq.
  3. Working (green), in existing order.
  4. Idle (hollow: finished and already seen, or waiting), most recent finish first.
  5. Unknown (dim, no agent), in existing order.
This is Herdr's own light priority (workspace/aggregate.rs), so the list
always reads top to bottom in light order. Focus never promotes anything;
looking at a finished project turns teal to hollow and drops it below green.
test_status_lights.LockedContractTest pins every rule above.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import uuid

RANKS = {"blocked": 0, "done": 1, "working": 2, "idle": 3, "unknown": 4}
FINISHED = {"done", "idle"}
EVENTS = {"pane.agent_status_changed", "workspace.focused", "tab.focused",
          "workspace.created", "pane.closed"}
MAX_REPLY = 4 * 1024 * 1024


def request(socket_path, method, params):
    request_id = "status-lights:" + uuid.uuid4().hex
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(3)
        connection.connect(socket_path)
        connection.sendall((json.dumps({"id": request_id, "method": method,
                                        "params": params}) + "\n").encode())
        with connection.makefile("rb") as stream:
            line = stream.readline(MAX_REPLY + 1)
    if len(line) > MAX_REPLY or not line.endswith(b"\n"):
        raise RuntimeError("Incomplete or oversized Herdr response")
    response = json.loads(line)
    if response.get("id") != request_id or "error" in response:
        raise RuntimeError("Herdr API failed: " + str(response.get("error", "wrong response id")))
    return response["result"]


def recency(space, agents):
    # Herdr numbers every state change from one server-wide counter.
    # completion_seq is the number of the working->idle change that finished
    # the current idle state; seeing the pane later leaves it untouched.
    status = space.get("agent_status")
    mine = [agent for agent in agents if agent.get("workspace_id") == space["workspace_id"]]
    if status == "blocked":
        seqs = [agent.get("state_change_seq") for agent in mine
                if agent.get("agent_status") == "blocked"]
    elif status in FINISHED:
        seqs = [agent.get("completion_seq") for agent in mine]
    else:
        return None
    seqs = [seq for seq in seqs if isinstance(seq, int)]
    return max(seqs) if seqs else None


def ordered(workspaces, agents=()):
    # workspace.list carries the same aggregate light as the sidebar.
    # Within red, teal and hollow, newest first; a finished project with no
    # completion number sits at the bottom of its group. Working and unknown
    # keep their order, so selecting a project can never bump it.
    def key(space):
        seq = recency(space, agents)
        return (RANKS.get(space.get("agent_status"), 4), seq is None, -(seq or 0))
    return sorted(workspaces, key=key)


def run(environ, now=False):
    if not now:
        event = json.loads(environ["HERDR_PLUGIN_EVENT_JSON"])
        name = event.get("event", "")
        if name not in EVENTS and name not in {value.replace(".", "_") for value in EVENTS}:
            return False
    socket_path = environ["HERDR_SOCKET_PATH"]
    state_dir = Path(environ["HERDR_PLUGIN_STATE_DIR"])
    state_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(socket_path.encode()).hexdigest()[:24]
    with (state_dir / (key + ".lights.lock")).open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Read after acquiring the lock: concurrent hooks and stale events
        # must always sort the current visible lights, not old event payloads.
        spaces = request(socket_path, "workspace.list", {})["workspaces"]
        agents = request(socket_path, "agent.list", {}).get("agents", [])
        wanted = ordered(spaces, agents)
        ids = [space["workspace_id"] for space in wanted]
        if ids == [space["workspace_id"] for space in spaces]:
            return False
        request(socket_path, "workspace.move_block", {
            "workspace_ids": ids, "before_workspace_id": None,
        })
        print("Status lights: " + ", ".join(
            str(space.get("label", space["workspace_id"])) + " (" +
            str(space.get("agent_status", "unknown")) + ")" for space in wanted
            if RANKS.get(space.get("agent_status"), 4) < 2), flush=True)
        return True


if __name__ == "__main__":
    try:
        run(os.environ, now="--now" in sys.argv or "--sweep" in sys.argv)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print("Status lights: " + str(error), file=sys.stderr)
        sys.exit(1)
