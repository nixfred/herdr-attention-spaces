#!/usr/bin/env python3
"""Sort Herdr projects: needs attention, completed, working, unknown."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import uuid

RANKS = {"blocked": 0, "done": 1, "idle": 2, "working": 3, "unknown": 4}
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


def ordered(workspaces):
    # workspace.list uses the same aggregate state/seen flag as the sidebar
    # project light. Viewed completions remain above running work.
    # Stable ties mean selecting a project cannot bump it within its class.
    return sorted(workspaces, key=lambda space: RANKS.get(space.get("agent_status"), 4))


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
        wanted = ordered(spaces)
        ids = [space["workspace_id"] for space in wanted]
        if ids == [space["workspace_id"] for space in spaces]:
            return False
        request(socket_path, "workspace.move_block", {
            "workspace_ids": ids, "before_workspace_id": None,
        })
        print("Status lights: " + ", ".join(
            str(space.get("label", space["workspace_id"])) + " (" +
            str(space.get("agent_status", "unknown")) + ")" for space in wanted
            if RANKS.get(space.get("agent_status"), 4) < 3), flush=True)
        return True


if __name__ == "__main__":
    try:
        run(os.environ, now="--now" in sys.argv or "--sweep" in sys.argv)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print("Status lights: " + str(error), file=sys.stderr)
        sys.exit(1)
