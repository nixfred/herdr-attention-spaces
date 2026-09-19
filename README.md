# Attention Spaces for Herdr

Moves a Space up the list the moment its agent needs Fred. Blocked Spaces sit at the very top, done Spaces right below them:
`blocked` (Claude is sitting on an approval or question dialog) or `done`
(background work finished while the Space was not being looked at). Every
other Space keeps its relative order. Nothing moves for `working` or
`unknown`.

A Space you are **watching** counts too. Herdr only calls an agent `done`
when it finishes while its tab is unseen; finishing in the Space you are
looking at reports plain `idle`, so the Space you were waiting on was the one
Space that never rose. A pane that goes `working` -> `idle` is now promoted
into the done tier as well.

The event carries no previous status, and Herdr also reports `idle` when you
open a finished Space (marking it seen), which looks identical. So the last
status of each pane is kept in the plugin state dir: only a pane that was
`working` at its previous event counts as having just finished. Opening a
finished Space still moves nothing, and if the agent has started working again
by the time the hook runs, nothing moves either.

The plugin uses Herdr's `pane.agent_status_changed` hook and the
`workspace.move` API. Before it touches order it re-reads the live status,
so a stale event (you looked at the Space before the hook ran, turning `done`
into `idle`) does nothing. The Space status is an aggregate; when a blocked
pane sits beside a working one the pane's own status is checked through
`agent.list`. It does not read terminal contents, send keystrokes, restart
Herdr or poll. Order is shared by every client attached to the same session.

Requires Python 3 on Linux and Herdr 0.8.2 or newer. Source is machine-local;
it is not registered with a remote marketplace or source repository.

```sh
herdr plugin link ~/Projects/herdr-attention-spaces
herdr plugin action invoke pi.attention-spaces.sweep       # blocked to the top, done below, right now
herdr plugin log list --plugin pi.attention-spaces --limit 10
python3 -m unittest test_attention_spaces                  # 34 unit tests against a fake socket API
```

Turn the watched-Space promotions off, or restrict which statuses count
(default is both), with an `attention.json` in the plugin config dir
(`herdr plugin config-dir pi.attention-spaces`). A `statuses` list without
`done` in it excludes completions too:

```json
{ "promote_focused_completions": false }
```


```json
{"statuses": ["blocked"]}
```

Disable (the current order is kept): `herdr plugin disable pi.attention-spaces`.
Enable again: `herdr plugin enable pi.attention-spaces`. Unregister without
deleting source: `herdr plugin unlink pi.attention-spaces`.

Sibling plugin: `~/Projects/herdr-recent-spaces` (most-recently-used
order, currently disabled). The two can run together; the attention move
simply happens on top of whatever order the other one keeps.

References: [Herdr plugins](https://herdr.dev/docs/plugins/),
[socket API](https://herdr.dev/docs/socket-api/). Status meanings, event
shape and request shapes were checked against the installed binary's
`herdr --skill` and `herdr api schema --json` (protocol 20).

## Priority correction — 2026-09-18

Every reorder now checks all agent panes using their explicit workspace IDs.
A blocked pane takes priority even when its workspace aggregate says working,
and a completion in a sibling pane cannot demote that blocked workspace.
Finished-while-watched transitions (working → idle) also enter the finished
tier. Selecting a space alone still does not promote it; Recent Spaces remains
disabled in the saved registry.

Validation: 34 fake-API unit tests pass, including four regression tests that
fail before this correction. Live ordering was not inspected or swept because
the editing agent was outside Herdr. The linked event script is read on each
invocation, so the correction applies on subsequent attention events.
