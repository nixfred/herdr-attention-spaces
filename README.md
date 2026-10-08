# Herdr attention-first order — OMW-HERDR-ORDER

## LOCKED (Fred, 2026-10-08). Do not change without him.

> "projects that need attn OR just finished running should sort to the top.
> Attn needed at the top and just finished sort just under attn needed.
> Lock that in, we have had to fix this several times."

1. **Needs attention** (blocked) at the very top, newest first.
2. **Just finished** (done or idle) directly under it, **most recent finish
   first**, ranked by Herdr's own `completion_seq` from `agent.list`. Viewing a
   finished project does not move it. A finished project with no completion
   number sits at the bottom of that group.
3. **Working**, then **unknown**, each in their existing order. Focus alone
   never promotes anything.

`test_status_lights.LockedContractTest` pins every rule above. If you change the
sorter and one of those tests fails, the change is wrong, not the test.

The bug this fixed: the sort was stable within a status, so a project that went
working → finished kept its old position *below* every older completion and
landed at the bottom of the finished group instead of the top.

## How it works

`status_lights.py` reads `workspace.list` and `agent.list` under a per-session
lock and moves the whole list in one `workspace.move_block`. Status, focus,
create and close hooks trigger it; the `sweep` action runs it on demand
(`herdr plugin action invoke pi.attention-spaces.sweep`). Recent Spaces must
stay disabled.

Gus additionally runs the SSH bridge for `vic:` projects. See
[bridge, verification and rollback](../herdr-ssh-status/README.md).

Vic runs the same `status_lights.py` under its older plugin ID
`pi.attention-first` in `~/Projects/herdr-attention-first`. Update both hosts
together and compare sha256.

Validation: `python3 -m unittest test_status_lights -v` (19 tests).
