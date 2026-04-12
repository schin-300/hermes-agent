# Hosted gateway thin-client cutover

## Goal
Make hosted CLI sessions real gateway-owned sessions rather than local-history proxies.

## Definition of done
- Hosted turns execute from gateway-owned session history, not client-resubmitted history.
- Hosted CLI can fetch gateway session snapshots/history as source of truth.
- Switching/resuming a hosted session can reattach to an already-running hosted run stream.
- Ctrl+B/session switching uses gateway live sessions and preserves active-run continuity.
- Focused hosted CLI/gateway tests pass.

## Non-goals
- Rewriting the entire local CLI UI into a remote terminal multiplexer.
- Cross-gateway persistence beyond the running gateway process.

## Files
- `agent/session_host.py`
- `gateway/platforms/api_server.py`
- `hermes_cli/hosted_session_client.py`
- `cli.py`
- `tests/agent/test_session_host.py`
- `tests/gateway/test_hosted_session_api.py`
- `tests/cli/test_hosted_session_client.py`
- `tests/cli/test_cli_init.py`

## Plan
1. Add gateway session snapshot/state APIs from `SessionHost`.
2. Make `/v1/runs` use gateway-owned session history when the client does not explicitly seed history.
3. Add hosted-client methods for snapshot fetch + attach-to-existing-run streaming.
4. Cut hosted CLI resume/switch logic over to gateway snapshot/reattach instead of local compatibility state.
5. Add regression tests for thin-client history ownership and active-run reattach.
6. Run focused hosted validation.
