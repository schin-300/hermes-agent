# Hosted session migration fixtures

These fixtures freeze the intended vocabulary and scenario coverage for the hosted-session runtime migration.

They are deliberately lightweight in Stage 0:
- they document the current bridge event subset
- they document the target canonical event vocabulary
- they give contract tests a stable source of truth before runtime rewrites start

## Fields
- `id`: stable scenario id
- `description`: human-readable scenario
- `current_bridge_events`: events available in the current API-server bridge
- `missing_from_current_bridge`: important events intentionally unavailable today
- `target_events`: desired canonical events for the hosted-session substrate

Later stages can extend these fixtures with full transcripts, attach/detach flows, and persistence expectations.
