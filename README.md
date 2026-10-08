# Herdr attention-first order — OMW-HERDR-ORDER

## LOCKED (Fred, 2026-10-08). Do not change without him.

> "projects that need attn OR just finished running should sort to the top.
> Attn needed at the top and just finished sort just under attn needed.
> Lock that in, we have had to fix this several times."
> Same night, on the next screenshot: "The green should be above the idle
> projects correct?"

The list reads top to bottom in **light order**, the same priority Herdr uses
for its own lights (`workspace/aggregate.rs`):

1. **Red, needs attention** (blocked), newest first.
2. **Teal check, just finished** (done: finished and not looked at yet), most
   recent finish first by Herdr's `completion_seq` from `agent.list`.
3. **Green, working**, in existing order.
4. **Hollow ring, idle** (finished and already seen, or waiting), most recent
   finish first; no completion number sits last.
5. **Dim, no agent**, in existing order.

Focus never promotes anything. Looking at a finished project turns teal into a
hollow ring, so it drops below green: once you have seen it, it is no longer
"just finished".

`test_status_lights.LockedContractTest` pins every rule above. If you change the
sorter and one of those tests fails, the change is wrong, not the test.

History of the drift: 09-19 Fred asked for solid lights above hollow; a 09-26
Codex round put viewed idle above working; 10-08 restored light order (Fred's
second identical complaint) and added newest-first within red, teal and hollow.
The 09-26 engine had also never been committed until 10-08.

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

Validation: `python3 -m unittest test_status_lights -v` (20 tests).
