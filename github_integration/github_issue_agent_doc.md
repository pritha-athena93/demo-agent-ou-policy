# github_issue_agent.py

## What it does
Drives the GitHub-issue-form HITL flow: round 1 (assessment + proposed workaround only) and round 2 (final decision: `update_org_policy` or `request_human_approval`). Posts the comment, sets `needs_hitl` workflow output.

## How it works
- `summarize()` builds the comment header from ground truth, not log-line substrings alone: `BLOCKED` -> blocked; last `policy_write_attempts` entry's `executed` flag (from `agent_common.py`'s `_prepend_verified_outcome`, sourced from the tool result's own `executed` field) -> "temporary override granted"; else a `PENDING-APPROVAL`/`pending-approval` match -> routed to human; else no change attempted.
  - Previously checked the `ALLOWED-CHECK-PASSED` log line ahead of `pending-approval`, which only meant "passed the registry/guardrail gate to attempt the write," not "the broker actually executed it." A denied write that got escalated to `request_human_approval` could still print "granted." Fixed by deciding "granted" off `policy_write_attempts[-1]["executed"]` instead.
- Round 2 forces an escalation if the model ends without calling `update_org_policy` or `request_human_approval` (`reached_terminal` check) -- a required outcome can't depend on the model remembering to produce it.

## How to use it
Entry point: `main()`, invoked by the GitHub Actions workflow with `ISSUE_BODY`/`ISSUE_NUMBER`/`COMMENT_BODY` env vars.

## Dependencies
`agent_bedrock.py` (the agent run), `logger.py` (capture), `tools.py` (fallback `request_human_approval` call).
