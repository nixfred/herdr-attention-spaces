#!/usr/bin/env python3
"""Herdr event hook: move a space to the top when its agent needs attention.

Herdr reports `blocked` when an agent shows an approval or question UI and
`done` when unseen background work finishes. Both mean Fred has to look at
that space. Blocked spaces go to the very top, done spaces right below them.
No polling, no pane input.

`done` only happens while the tab is UNSEEN. A space Fred is watching goes
straight to `idle` when its agent finishes, so the space he is looking at was
the one space that never rose — it sat wherever it was while he waited on it
(Fred, 2026-09-18: "Why didnt this session jump to the top of herdr when it
was complete?"). A finished agent is finished whether or not anyone watched,
so `working` -> `idle` is promoted into the done tier as well.

The event carries no previous status, and `done` -> `idle` (Fred focused a
finished space, marking it seen) looks identical to a completion. So the last
status of every pane is kept in the plugin state dir: only a pane that was
WORKING at its previous event counts as having just finished.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import uuid

DEFAULT_STATUSES = ("blocked", "done")
TIER_ORDER = ("blocked", "done")
# What a finished-while-watched pane is treated as, and what it must have been.
COMPLETED_STATUS = "done"
MAX_REMEMBERED_PANES = 200
WORKING_STATUS = "working"
IDLE_STATUS = "idle"
EVENT_NAMES = ("pane_agent_status_changed", "pane.agent_status_changed")
MAX_REPLY = 4 * 1024 * 1024


def request(socket_path, method, params):
    request_id = "attention-spaces:" + uuid.uuid4().hex
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


def config(config_dir):
    """Optional attention.json in the plugin config dir.

    {"statuses": ["blocked", "done"], "promote_focused_completions": true}
    """
    if not config_dir:
        return {}
    path = Path(config_dir) / "attention.json"
    if not path.is_file():
        return {}
    settings = json.loads(path.read_text())
    return settings if isinstance(settings, dict) else {}


def attention_statuses(settings):
    statuses = settings.get("statuses")
    if statuses is None:
        return set(DEFAULT_STATUSES)
    if (not isinstance(statuses, list) or not statuses
            or not all(isinstance(status, str) for status in statuses)):
        raise ValueError("attention.json: statuses must be a non-empty list of strings")
    return set(statuses)


def promote_completions(settings):
    """Whether a pane finishing while Fred watches counts. On unless turned off."""
    value = settings.get("promote_focused_completions", True)
    if not isinstance(value, bool):
        raise ValueError("attention.json: promote_focused_completions must be true or false")
    return value


def read_last_statuses(path):
    """Last agent status seen per pane, so working -> idle can be recognised."""
    try:
        stored = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {key: value for key, value in stored.items()
            if isinstance(key, str) and isinstance(value, str)} if isinstance(stored, dict) else {}


def write_last_statuses(path, statuses, live_pane_ids):
    """Persist the pane statuses, dropping panes that no longer exist."""
    kept = {pane: status for pane, status in statuses.items()
            if live_pane_ids is None or pane in live_pane_ids}
    try:
        path.write_text(json.dumps(kept))
    except OSError:
        pass


def label(workspace):
    return workspace.get("label") or workspace["workspace_id"]


def agents_by_pane(socket_path):
    return {agent["pane_id"]: agent["agent_status"]
            for agent in request(socket_path, "agent.list", {})["agents"]}


def pane_status(socket_path, pane_id):
    return agents_by_pane(socket_path).get(pane_id)


def tier(status):
    """Blocked (waiting on Fred) sits above done (finished, unseen)."""
    return TIER_ORDER.index(status) if status in TIER_ORDER else len(TIER_ORDER)


def arrange(socket_path, workspaces, statuses, first=None):
    """Put every attention space at the top: blocked first, then done.

    Within a tier the relative order is kept, except `first` (the space whose
    event fired) leads its tier. Everything else keeps its order below.
    Returns the labels that moved.
    """
    status_of = {space["workspace_id"]: space["agent_status"] for space in workspaces}
    # Workspace aggregation can hide a blocked pane behind a working pane.
    # Check every workspace, not only the one whose event triggered this run.
    for agent in request(socket_path, "agent.list", {})["agents"]:
        workspace_id = agent.get("workspace_id")
        status = agent["agent_status"]
        if (workspace_id in status_of and status in statuses
                and tier(status) < tier(status_of[workspace_id])):
            status_of[workspace_id] = status
    if first is not None:
        # A completion in one pane must never demote its blocked sibling.
        if tier(first[1]) < tier(status_of[first[0]]):
            status_of[first[0]] = first[1]
    wanted = [space for space in workspaces if status_of[space["workspace_id"]] in statuses]
    if first is not None:
        wanted.sort(key=lambda space: space["workspace_id"] != first[0])
    wanted.sort(key=lambda space: tier(status_of[space["workspace_id"]]))
    order = [space["workspace_id"] for space in workspaces]
    moved = []
    for index, space in enumerate(wanted):
        if order[index] == space["workspace_id"]:
            continue
        request(socket_path, "workspace.move", {"workspace_id": space["workspace_id"], "insert_index": index})
        order.remove(space["workspace_id"])
        order.insert(index, space["workspace_id"])
        moved.append(label(space) + " (" + status_of[space["workspace_id"]] + ")")
    return moved


def promote_completion(socket_path, workspace_id, pane_id, statuses):
    """A pane that just stopped working, in a space Fred is watching.

    Herdr never calls this `done` because the tab was seen, so the live status
    is `idle` and the normal re-check would drop it. Still re-read: if the
    agent has started working again since the event, there is nothing to show.
    """
    if pane_id is not None and pane_status(socket_path, pane_id) != IDLE_STATUS:
        return False
    workspaces = request(socket_path, "workspace.list", {})["workspaces"]
    if not any(space["workspace_id"] == workspace_id for space in workspaces):
        return False
    moved = arrange(socket_path, workspaces, set(statuses) | {COMPLETED_STATUS},
                    first=(workspace_id, COMPLETED_STATUS))
    if moved:
        print("Moved up: " + ", ".join(moved), flush=True)
    return bool(moved)


def promote(socket_path, workspace_id, pane_id, statuses):
    workspaces = request(socket_path, "workspace.list", {})["workspaces"]
    target = next((space for space in workspaces if space["workspace_id"] == workspace_id), None)
    if target is None:
        return False
    # The event can be stale by the time this hook runs (Fred may already have
    # looked, which turns `done` into `idle`). Re-check before touching order.
    # The workspace status is an aggregate; a blocked pane beside a working one
    # only shows on the pane itself, so fall back to the pane's own status.
    status = target["agent_status"]
    if status not in statuses:
        status = pane_status(socket_path, pane_id) if pane_id is not None else None
        if status not in statuses:
            return False
    moved = arrange(socket_path, workspaces, statuses, first=(workspace_id, status))
    if moved:
        print("Moved up: " + ", ".join(moved), flush=True)
    return bool(moved)


def sweep(socket_path, statuses):
    """Move every space that currently needs attention to the top, blocked above done."""
    workspaces = request(socket_path, "workspace.list", {})["workspaces"]
    moved = arrange(socket_path, workspaces, statuses)
    if moved:
        print("Moved up: " + ", ".join(moved), flush=True)
    return len(moved)


def parse_event(environ):
    """Return (workspace_id, pane_id, status) for a status event, else None."""
    event = json.loads(environ["HERDR_PLUGIN_EVENT_JSON"])
    data = event.get("data") or {}
    # Plugin EventEnvelope uses snake_case in Herdr 0.8.2; manifest names use dots.
    names = (event.get("event"), environ.get("HERDR_PLUGIN_EVENT"), data.get("type"))
    if not any(name in EVENT_NAMES for name in names):
        return None
    status = data.get("agent_status")
    if not isinstance(status, str) or not status:
        return None
    workspace_id = data.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise ValueError("Missing event workspace ID")
    pane_id = data.get("pane_id")
    return workspace_id, pane_id if isinstance(pane_id, str) and pane_id else None, status


def run(environ, do_sweep=False):
    socket_path = environ["HERDR_SOCKET_PATH"]
    settings = config(environ.get("HERDR_PLUGIN_CONFIG_DIR"))
    statuses = attention_statuses(settings)
    target = None
    if not do_sweep:
        target = parse_event(environ)
        if target is None:
            return False

    # Herdr shares plugin state across sessions. Serialize list+move per
    # session socket so two hooks firing together cannot interleave moves.
    state_dir = Path(environ["HERDR_PLUGIN_STATE_DIR"])
    state_dir.mkdir(parents=True, exist_ok=True)
    session_key = hashlib.sha256(socket_path.encode()).hexdigest()[:24]
    with (state_dir / (session_key + ".lock")).open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if do_sweep:
                return sweep(socket_path, statuses) > 0
            workspace_id, pane_id, status = target
            # Remember this pane's status before acting: the next event needs it
            # to tell "just finished" from "Fred opened a finished space".
            history_path = state_dir / (session_key + ".status.json")
            history = read_last_statuses(history_path)
            previous = history.get(pane_id) if pane_id else None
            if pane_id:
                history[pane_id] = status
                live = None
                if len(history) > MAX_REMEMBERED_PANES:
                    try:
                        live = set(agents_by_pane(socket_path)) | {pane_id}
                    except (OSError, RuntimeError):
                        live = None
                write_last_statuses(history_path, history, live)
            if status in statuses:
                return promote(socket_path, workspace_id, pane_id, statuses)
            if (status == IDLE_STATUS and previous == WORKING_STATUS
                    and COMPLETED_STATUS in statuses and promote_completions(settings)):
                return promote_completion(socket_path, workspace_id, pane_id, statuses)
            return False
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


if __name__ == "__main__":
    try:
        run(os.environ, do_sweep="--sweep" in sys.argv)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print("Attention Spaces: " + str(error), file=sys.stderr)
        sys.exit(1)
