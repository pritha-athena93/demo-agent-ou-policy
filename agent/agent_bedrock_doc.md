# agent_bedrock.py

## What it does
Bedrock+Guardrails agent variant. Same tools as `agent_direct.py`, but `update_org_policy` calls are intercepted before reaching the broker: registry-protected check, then guardrail check, then (for the instance-type SCP) an SSM-allowlist check.

## How it works
- On a flagged/blocked policy write: logs `BLOCKED`, calls `propose_workaround(<reason>, policy_id)` instead of executing.
- On a clean check: logs `ALLOWED-CHECK-PASSED` (this only means "passed the gate to attempt the write," not "write succeeded" — the actual broker call can still fail or get denied downstream).
- `propose_workaround` now requires `policy_id` (see `tools_doc.md`) so the suggested workaround matches the actual policy, not a hardcoded SSM/public-IP string for every case.

## How to use it
Invoked via `agent_bedrock.run(mandate, account, allowed_tools=...)` from `github_issue_agent.py`.

## Dependencies
`tools.py` (schemas/dispatch), `broker.py`, `policy_registry.py`, `agent_common.py` (tool loop).
