# Herdr attention-first order — OMW-HERDR-ORDER

Updated 2026-09-26: needs attention → unseen completed → viewed completed/idle → working → unknown.
Same-state order stays stable; opening a completed project keeps it above running work.
The native idle state includes agents ready for input as well as viewed completions.

`status_lights.py` reads current workspace state under a per-session lock and
moves the list atomically. Existing status/focus/create/close hooks trigger it.
The Recent Spaces plugin must remain disabled.

Gus additionally runs the SSH bridge for `vic:` projects. See
[bridge, verification and rollback](../herdr-ssh-status/README.md).

Validation: `python3 -m unittest test_status_lights -v` (10 tests).
The previous sorter is backed up at
`../herdr-ssh-status/verification/status_lights.before.py`.
