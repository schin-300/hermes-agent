# Gateway Session Worker Processes Migration Plan

> For Hermes: use architecture-migration-eval-rails. Freeze behavior first, then migrate the runtime substrate in thin slices.

Goal
- Make gateway-backed Hermes sessions use real OS-level worker processes instead of in-process agent tasks, so an active session is genuinely isolated and can be supervised/managed as its own worker.

Why this plan exists
- Right now the gateway is the long-lived background process, but `/v1/runs` mostly executes agents as in-process asyncio tasks + executor work inside that gateway.
- That is workable for heartbeat/status plumbing, but it is not the clean "each session is its own worker process" architecture.
- We want the clean version: one gateway/front-door process per profile, plus explicit worker processes for active sessions.

Non-goals
- Rewriting unrelated gateway/platform logic.
- Replacing the existing blocked-wait heartbeat UX.
- Shipping PTY attach/detach UX improvements before the core worker substrate is solid.
- Migrating every surface at once. Start with gateway-backed CLI/API sessions first.

Definition of done
- Active gateway-backed sessions are backed by real worker processes on the host.
- The gateway owns only routing/supervision, not the live conversation loop for those sessions.
- Clarify, cancel, blocked-wait heartbeat, delegate heartbeat, and final response streaming all continue to work.
- Worker lifecycle is explicit: spawn, stream, clarify reply, cancel, exit, cleanup.
- A killed/stuck worker does not poison sibling sessions.
- Focused pytest and full targeted gateway/CLI suite pass.

Current reality (truth anchor)
- Background gateway process per profile: yes.
- Individual gateway-backed session as its own process: no.
- Current code path:
  - `hermes_cli/gateway.py` starts the gateway with `subprocess.Popen(...)`
  - `gateway/platforms/api_server.py` handles `/v1/runs`
  - each run is tracked in in-memory maps like `_run_streams`, `_run_agents`, `_run_clarify_queues`
  - the actual agent turn runs via `asyncio.create_task(...)` + `run_in_executor(...)`

ASCII: current vs target

Current

  +--------------------+
  | gateway process    |
  | (per profile)      |
  +--------------------+
            |
            +--> run A (async task + threadpool work)
            |
            +--> run B (async task + threadpool work)
            |
            +--> run C (async task + threadpool work)

Target

  +--------------------+
  | gateway process    |
  | (per profile)      |
  +--------------------+
            |
            +--> session worker proc A
            |
            +--> session worker proc B
            |
            +--> session worker proc C

Structured data flow target

  CLI client
     |
     | HTTP + SSE
     v
  gateway API server
     |
     | spawn/supervise worker
     v
  session worker process
     |
     +--> model/tool loop
     +--> blocked wait state
     +--> delegate child state

Worker IPC sketch

  parent (gateway)                  child (session worker)
  ----------------                  ----------------------
  spawn worker  ------------------> start agent
  send turn payload  -------------> run turn
  read JSONL events <------------- message.delta / tool.started / agent.activity / clarify.request / run.completed
  send clarify reply -------------> unblock clarify callback
  send cancel --------------------> interrupt agent / terminate if needed
  reap exit code <---------------- process exits

Recommended transport for v1
- Use JSONL over stdio for worker IPC, not terminal scraping.
- The worker should print one JSON object per event line.
- Parent process owns framing, backpressure, and cleanup.
- Do not use pretty console output as the protocol.

Why not PTY-first
- PTY is great for attachable human-facing shells.
- It is bad as the primary machine protocol for reliable structured events.
- We can still add PTY-backed attach later if we want a real interactive worker terminal.
- First get the worker substrate clean with JSONL/stdin/stdout.

Likely files
- `docs/plans/2026-04-10-gateway-session-worker-processes.md`
- `gateway/platforms/api_server.py`
- `gateway/session_worker_protocol.py` (new)
- `gateway/session_worker_process.py` (new)
- `gateway/session_worker_manager.py` (new)
- `hermes_cli/gateway_session_client.py`
- `tools/clarify_tool.py`
- `run_agent.py`
- `tests/gateway/test_api_server.py`
- `tests/hermes_cli/test_gateway_cli_sessions.py`
- `tests/cli/test_gateway_session_agent.py`
- `tests/gateway/test_session_worker_protocol.py` (new)
- `tests/gateway/test_session_worker_manager.py` (new)

Stage plan

## Stage 0: eval rails
1. Add this plan doc.
2. Add protocol/contract tests for worker event vocabulary.
3. Add manager tests for spawn/cleanup/cancel/clarify routing.
4. Preserve current user-visible expectations already covered by:
   - `tests/cli/test_gateway_session_agent.py`
   - `tests/gateway/test_api_server.py`
   - `tests/gateway/test_blocked_wait_proxy.py`
   - `tests/hermes_cli/test_gateway_cli_sessions.py`
5. Commit rails before deeper runtime changes.

## Stage 1: worker protocol module
1. Create `gateway/session_worker_protocol.py`.
2. Define canonical event names and validation helpers.
3. Include event builders for:
   - `message.delta`
   - `tool.started`
   - `tool.completed`
   - `reasoning.available`
   - `subagent.heartbeat`
   - `subagent.warning`
   - `agent.activity`
   - `clarify.request`
   - `run.completed`
   - `run.failed`
   - `run.cancelled`
4. Add tests that validate required keys and terminal events.

## Stage 2: one-shot worker process manager
1. Create `gateway/session_worker_manager.py`.
2. Spawn a child process with sanitized env and explicit working directory.
3. Parent sends a single turn payload to child stdin.
4. Parent consumes child JSONL stdout and forwards it into existing run SSE queues.
5. Parent can send clarify replies and cancel requests.
6. Add tests for:
   - spawn success
   - stdout event relay
   - clarify response routing
   - cancel path
   - orphan cleanup

## Stage 3: worker process entrypoint
1. Create `gateway/session_worker_process.py`.
2. Read one JSON payload from stdin.
3. Construct `AIAgent` in child process.
4. Hook `stream_delta_callback`, `tool_progress_callback`, and `clarify_callback` to emit JSONL events.
5. Return terminal event and exit code.
6. Add focused tests using a fake/stub agent path where possible.

## Stage 4: wire `/v1/runs` onto the manager
1. Replace in-process `_run_agents` turn execution in `gateway/platforms/api_server.py` with the new manager.
2. Keep existing HTTP/SSE contract stable.
3. Keep current blocked-wait UI behavior stable from the CLI client's perspective.
4. Validate clarify/cancel/heartbeat paths again.

## Stage 5: promote from per-run worker to per-session worker
This is the actual clean endgame.

1. Key worker processes by `session_id`, not `run_id`.
2. If a worker for that session already exists, submit the next user turn to it instead of spawning a new child.
3. Maintain session-local conversation state inside the worker process, not only in the gateway parent.
4. Keep persistence checkpoints in the session DB so crash recovery still works.
5. Add worker idle timeout / reap policy.
6. Add explicit attach/detach semantics if needed.

This stage is where we can honestly say:
- yes, gateway-backed active sessions are their own background workers.

## Stage 6: helper/profile sidecars
1. Let blocked-wait helper / reviewer roles reuse the same worker manager.
2. Support named profile launch presets cleanly.
3. Keep helper workers separate from the main session worker.

Validation commands
- Focused worker/protocol tests:
  - `uv run --extra dev pytest -q tests/gateway/test_session_worker_protocol.py tests/gateway/test_session_worker_manager.py -o addopts=''`
- Existing gateway-backed session coverage:
  - `uv run --extra dev pytest -q tests/cli/test_gateway_session_agent.py tests/gateway/test_api_server.py tests/gateway/test_blocked_wait_proxy.py tests/hermes_cli/test_gateway_cli_sessions.py tests/cli/test_cli_clarify_ui.py tests/gateway/test_gateway_clarify.py tests/tools/test_delegate_watchdog.py tests/hermes_cli/test_gateway_service.py tests/gateway/test_status_command.py tests/gateway/test_approve_deny_commands.py tests/tools/test_clarify_tool.py tests/tools/test_delegate.py tests/gateway/test_session_race_guard.py tests/gateway/test_unknown_command.py -o addopts=''`
- Syntax:
  - `python3 -m py_compile gateway/session_worker_protocol.py gateway/session_worker_manager.py gateway/session_worker_process.py gateway/platforms/api_server.py hermes_cli/gateway_session_client.py`

Commit discipline
- Commit after Stage 0 rails.
- Commit after protocol module.
- Commit after worker manager.
- Commit after entrypoint.
- Commit after `/v1/runs` switchover.
- Commit after persistent per-session worker promotion.

Immediate next slice
- Stage 0/1 only:
  1. add this plan
  2. add worker protocol contract module + tests
  3. commit before touching the runtime substrate
