# broker.py

## What it does
Deterministic JIT access broker -- the only path from an agent to a real `UpdatePolicy` call against the demo-prod-core SCPs (or a local-mock grant for the other accounts). Account-boundary and protected-policy checks happen here, not in the model.

## How it works
- `grant_instance_type_exception`: adds an instance-type pattern to an account-scoped exception statement on the t3-only SCP.
- `grant_tag_exception(target_account, tag_name)`: looks up the SCP statement by exact Sid `DenyRunInstancesWithout{tag_name}Tag` and scopes its requirement off for `target_account`.
  - `_resolve_tag_name(raw)`: the model is told (via the tool schema) to pass a bare tag name, but has repeatedly passed a full sentence instead (e.g. "Owner tag not required during the test window"), which fails the exact Sid match and gets denied. This extracts a known tag (`KNOWN_TAG_NAMES = ["Owner", "Department"]`) out of arbitrary text via a word-boundary, case-insensitive search, so the grant still succeeds when the model's phrasing is messy. Falls through unchanged if no known tag appears, so the resulting `BrokerDenied` error still reports the actual unmatched input.
- `request_scoped_credential`: account-boundary + protected-policy check, then issues a capped-duration credential.

## How to use it
Called from `tools.py`'s `update_org_policy_brokered`, never called directly by the model (the model only ever sees the `update_org_policy` tool).

## Dependencies
`policy_registry.py` (protected-policy check), `boto3`/AWS Organizations (real account only).
