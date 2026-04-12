# Hosted Hermes Terminal Vision (Source of Truth)

## Immutable product vision

Interactive hosted Hermes is **not** a local proxy, replay client, or structured run renderer.

Interactive hosted Hermes is a **real persistent terminal session** running on the host inside a dedicated tmux-backed container/session substrate.

When the user runs `hermes` in hosted mode:
- Hermes ensures the gateway is running.
- The gateway ensures a real hosted Hermes terminal session exists.
- The local terminal **attaches** to that real hosted session.
- If the local terminal window closes, the hosted Hermes session keeps running.
- Reopening and reattaching connects to the same live session, not a replay.

## Non-negotiable invariants

1. **The real interactive Hermes process runs host-side, not client-side.**
2. **Closing the local terminal must not stop the hosted Hermes process.**
3. **Reattach means attach to the same live session, not reconstruct history locally.**
4. **The hosted session must be a real terminal, with real scrollback and real in-flight output.**
5. **Gateway is the control plane.** It creates, lists, and closes hosted sessions.
6. **tmux is the persistence/terminal substrate for hosted interactive sessions.**
7. **Any older structured `/v1/runs` hosted proxy path is not the source of truth for interactive hosted CLI.**

## Immediate implementation target

### Runtime shape
- Each hosted interactive Hermes session maps to one tmux session on a dedicated Hermes-managed tmux socket.
- Each tmux session runs a normal direct Hermes interactive CLI process with hosted mode disabled inside the child.
- The child Hermes process gets a deterministic session ID from the gateway.

### Attach model
- Local `hermes` in hosted mode does not boot a local interactive CLI.
- It asks the gateway to ensure a hosted tmux session exists, then attaches to it.
- `--resume` / `-c` reattach to an existing hosted session when possible.

### Source of truth
- For interactive hosted CLI, the tmux-hosted Hermes child is the runtime source of truth.
- Gateway tracks and exposes session metadata, but does not fake the terminal interaction itself.

## Explicit non-goals for this phase
- No multi-agent blackboard/shared mind work yet.
- No browser attach implementation yet.
- No hybrid structured replay pretending to be the hosted terminal.
- No local mirrored-history hacks.

## Concrete behavioral contract
1. `hermes` in interactive hosted mode must reuse the current live tmux-backed session by default.
2. If there is no current live tmux-backed session, `hermes` creates one.
3. Terminal/window/SSH disconnects detach only the client; the tmux-hosted Hermes process remains alive.
4. Explicit session-close paths must terminate the hosted tmux session.
5. The in-terminal session switcher should operate on live hosted tmux sessions when running inside the hosted child.
6. The hosted gateway is allowed only as a thin control plane for create/list/close metadata and attach discovery — not as the interactive runtime substrate.

## Likely files / surfaces
- `hermes_cli/main.py` — default interactive launch path
- `gateway/hosted_tmux.py` — real tmux-hosted session source of truth
- `cli.py` — in-session switching behavior for hosted tmux children
- `gateway/platforms/api_server.py` — thin create/list/close endpoints for hosted tmux
- `tests/gateway/test_hosted_tmux.py`
- `tests/hermes_cli/test_cmd_chat_hosted_tmux.py`
- `tests/cli/test_cli_init.py`

## Validation plan
- Unit-test tmux manager command generation and default-current-session reuse.
- Unit-test CLI hosted-child switching behavior (`Ctrl+B`, `/new`, `/resume`).
- Unit-test gateway terminal-session endpoints.
- Run targeted pytest suites for the touched files.
- Run a practical tmux-level smoke check on this machine if tmux is available.

## Ordered implementation steps
1. Make tmux manager remember and reuse the current live hosted session by default.
2. Ensure newly launched hosted children know they are running inside the hosted tmux substrate.
3. Make hosted-child session switching operate on live tmux sessions rather than mutating local replay state.
4. Keep explicit close semantics pointed at killing the tmux session itself.
5. Prove the behavior with tests and a real tmux smoke run.

## Definition of done for this phase
- Running `hermes` in hosted mode creates or attaches to a tmux-backed hosted Hermes session.
- Running `hermes` again while that session is alive reattaches to the same live tmux-backed session by default.
- Closing the terminal window detaches only the client, not the hosted Hermes process.
- Reopening and resuming reattaches to the still-running hosted session.
- The in-session switcher operates on live hosted tmux sessions when running inside the hosted child.
- The implementation is grounded in real tmux-hosted Hermes processes, not event replay.
