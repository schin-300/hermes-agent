# Hosted Session Runtime Architecture Plan

> **For Hermes:** Use the `architecture-migration-eval-rails` skill before changing runtime ownership semantics.

**Goal:** Move Hermes to a single hosted session/runtime substrate that can serve many concurrent sessions from one gateway/host, with CLI/API/ACP acting as thin clients over one canonical event model.

**Architecture:** Introduce one in-process session host that owns session lifecycle, run lifecycle, persistence, cancellation, and event emission. All frontends — local CLI, API server, ACP, and future browser/mobile clients — should attach to that host rather than recreating or partially translating agent execution paths.

**Tech Stack:** Existing `AIAgent`, Python in-process event fanout, gateway API server transport, ACP adapter, pytest contract/baseline tests.

---

## Why this change

Current local interactive mode defaults to gateway-backed execution in `hermes_cli/main.py`, routes through `GatewaySessionAgentProxy`, then re-creates a fresh `AIAgent` inside `gateway/platforms/api_server.py`.

That means the stack today is effectively:

`CLI -> GatewaySessionAgentProxy -> /v1/runs -> APIServerAdapter._create_agent() -> AIAgent -> reduced SSE event set -> proxy -> CLI renderer`

Known fidelity loss in the current path:
- `gateway/platforms/api_server.py` only forwards a subset of events
- `_thinking` and `subagent_progress` are explicitly dropped
- `cli.py` mostly renders gateway tool progress as the currently active spinner/tool, not the richer direct-mode display

The right long-term destination is **not** “keep adding more proxy rewrites forever.”
The right destination is:

- one hosted runtime
- many sessions on that runtime
- one canonical event schema
- many transports/views on top

---

## Desired end state

### Core runtime model
- One **SessionHost** owns many hosted sessions.
- Each hosted session owns:
  - session id
  - conversation history
  - runtime/model/toolset overrides
  - active run state
  - attach/detach state
  - persistence metadata
- A session may have zero or more attached clients.
- All execution happens host-side.

### Event model
Every client consumes the same canonical event vocabulary.

Initial target event families:
- `session.created`
- `session.attached`
- `session.detached`
- `run.started`
- `message.delta`
- `message.completed`
- `reasoning.delta`
- `reasoning.completed`
- `tool.generating`
- `tool.started`
- `tool.completed`
- `subagent.progress`
- `run.completed`
- `run.failed`
- `run.cancelled`

Notes:
- Final names can change, but **one canonical vocabulary** must exist.
- Local/direct mode and hosted/gateway mode must not invent separate realities.

### Transport model
- **Local CLI:** attach directly to the session host in-process for best fidelity and lowest overhead.
- **API server:** serialize canonical events to SSE/HTTP for remote clients.
- **ACP:** adapt canonical events to ACP updates.
- **Messaging platforms:** subscribe to session/run state and render only what the platform can show.

---

## Non-goals
- Do not redesign the whole browser UI in this migration.
- Do not force internal runtime semantics to become ACP-specific.
- Do not remove the current gateway/API transport first.
- Do not big-bang rewrite the agent loop.

---

## Whole-system build order

This is one end-state architecture with one build path. The list below is the implementation order, not a request to stop after every bullet and re-decide the architecture.

### 1. Freeze the contract, then keep building

**Build first:**
- `tests/fixtures/hosted_sessions/README.md`
- `tests/fixtures/hosted_sessions/basic_tool_run.json`
- `tests/fixtures/hosted_sessions/reasoning_and_subagent.json`
- `tests/gateway/test_hosted_session_event_contract.py`
- `tests/cli/test_hosted_session_contract.py`

**Why:**
- we need proof of the current lossy bridge shape
- we need one explicit target vocabulary before the runtime changes begin
- after that, the implementation should keep moving toward the final substrate instead of pausing to renegotiate architecture

### 2. Introduce the canonical event model

**Build:**
- `agent/session_events.py`

**Purpose:**
- define the real event vocabulary once
- stop letting transports decide which events are "real"
- give CLI, gateway, ACP, and future clients one shared language

**Minimum contents:**
- canonical event names
- structured payload helpers / event record type
- validation helpers for event type membership

### 3. Build the real hosted runtime owner

**Build:**
- `agent/session_host.py`
- optionally `agent/session_registry.py` if the host/registry split is cleaner

**Purpose:**
- own many hosted sessions in one process
- own run lifecycle, cancellation, attach/detach, persistence hooks, and event fanout
- wrap `AIAgent` instead of replacing it

**Important rule:**
- the host owns truth
- `AIAgent` remains the execution engine
- transports do not own session semantics anymore

### 4. Move gateway/API onto the host

**Modify:**
- `gateway/platforms/api_server.py`
- `gateway/run.py`

**Goal:**
- keep the current endpoints
- make them thin serializers over host-owned sessions and canonical events
- remove the current reduced event subset as the source of truth

### 5. Move local CLI onto the same host directly

**Modify:**
- `cli.py`
- `hermes_cli/main.py`
- `hermes_cli/gateway_session_client.py`

**Goal:**
- local interactive CLI should attach directly to the in-process hosted session runtime
- stop using the localhost `/v1/runs` proxy hop for local interactive mode
- preserve hosted-session semantics while restoring direct-fidelity rendering

### 6. Move ACP and other clients onto the same substrate

**Modify:**
- `acp_adapter/events.py`
- `acp_adapter/server.py`
- any gateway client adapters that special-case progress/event semantics today

**Goal:**
- ACP becomes another adapter over the canonical event stream
- it may choose what to render, but it should not redefine the underlying runtime model

### 7. Delete the old rewrite-heavy path

**Clean up:**
- `hermes_cli/gateway_session_client.py`
- `gateway/platforms/api_server.py`
- `cli.py`
- compatibility shims/tests that only existed for the lossy bridge

**Goal:**
- remove code whose only job was translating one partial reality into another
- keep only the compatibility edges genuinely needed by external clients

---

## Build philosophy for this work

The architecture is already chosen:
- one hosted runtime
- many sessions
- one canonical event stream
- many transports/views

From here, implementation should be:
- plan the whole system once
- build toward the final shape directly
- if it breaks, fix the bugs
- do not keep re-scoping the substrate every few files

Tests are here to keep the branch honest while we build, not to force a stage-gated product process.

---

## Recommended canonical naming

These names are suggestions, not mandates:

- `SessionHost`
- `HostedSession`
- `RunHandle`
- `SessionEvent`
- `SessionEventSink`
- `SessionAttachment`

The important part is the separation of concerns:
- host owns truth
- transports adapt truth
- clients render truth

---

## Files most likely touched during implementation

### Core runtime
- `run_agent.py`
- `hermes_state.py`
- `agent/session_events.py` *(new)*
- `agent/session_host.py` *(new)*

### CLI
- `cli.py`
- `hermes_cli/main.py`
- `hermes_cli/gateway_session_client.py`

### Gateway/API
- `gateway/platforms/api_server.py`
- `gateway/run.py`

### ACP
- `acp_adapter/events.py`
- `acp_adapter/server.py`

### Tests
- `tests/gateway/test_api_server.py`
- `tests/gateway/test_hosted_session_event_contract.py` *(new)*
- `tests/cli/test_hosted_session_contract.py` *(new)*
- `tests/acp/test_events.py`
- `tests/agent/test_subagent_progress.py`
- `tests/fixtures/hosted_sessions/*` *(new)*

---

## Validation commands

Use the repo venv for all validation.

```bash
source venv/bin/activate
python -m pytest tests/gateway/test_api_server.py -q
python -m pytest tests/acp/test_events.py tests/agent/test_subagent_progress.py -q
python -m pytest tests/ -q
```

During Stage 0 / Stage 1, also add focused runs for new contract tests:

```bash
source venv/bin/activate
python -m pytest tests/gateway/test_hosted_session_event_contract.py -q
python -m pytest tests/cli/test_hosted_session_contract.py -q
```

---

## Immediate next step

Implement **Stage 0 only** first:
1. add fixture directory
2. add contract/baseline tests
3. keep branch green
4. only then begin the runtime extraction

This keeps the migration grounded and prevents a substrate rewrite by vibes.
