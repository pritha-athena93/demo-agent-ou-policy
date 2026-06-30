# Agent + Guardrails Demo

Two tool-calling agents, identical tools/prompt/mandate, both calling the
same model via Amazon Bedrock -- one with Bedrock Guardrails attached, one
without. Same task, only the platform wrapper (guardrail or not) differs.
The model defaults to an open-source stand-in (set `AGENT_MODEL_ID` to use a
different Bedrock model).

## GitHub integration

Lets people raise an issue describing what's blocked and where; an agent
(Bedrock+Guardrails variant only) runs automatically for trusted issue
openers and comments back the outcome.

### One-time AWS setup (OIDC, no stored keys)

Run once in your AWS account (replace placeholders with your account ID /
repo):

```bash
# 1. OIDC provider (skip if one already exists for this account)
aws iam list-open-id-connect-providers --profile sandbox
aws iam create-open-id-connect-provider \
  --profile sandbox \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1

# 2. Trust policy scoped to this exact repo
cat > /tmp/github-oidc-trust.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
      "StringLike": {"token.actions.githubusercontent.com:sub": "repo:<OWNER>/<REPO>:*"}
    }
  }]
}
EOF
aws iam create-role --profile sandbox \
  --role-name github-agent-ou-policy-demo \
  --assume-role-policy-document file:///tmp/github-oidc-trust.json

# 3. Permissions -- only Bedrock access, nothing else
cat > /tmp/github-agent-permissions.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["bedrock:ApplyGuardrail", "bedrock:InvokeModel", "bedrock:Converse"],
    "Resource": "*"
  }]
}
EOF
aws iam put-role-policy --profile sandbox \
  --role-name github-agent-ou-policy-demo \
  --policy-name agent-bedrock-access \
  --policy-document file:///tmp/github-agent-permissions.json

aws iam get-role --profile sandbox --role-name github-agent-ou-policy-demo --query 'Role.Arn' --output text
```

Note: the broker can do real `organizations:UpdatePolicy` calls against
real SCPs in a real AWS Organizations member account (see `agent/broker.py`,
and the Design section below) -- not every target account in
`policy_registry.py` has to be wired to a real one; unwired accounts stay
local-mock with no real AWS API calls. The CI role therefore needs
Organizations + SSM access too, not just Bedrock -- add this second inline
policy (note the explicit `Deny` on touching the protected `no-public-ip-ec2`
SCP by ID, the real IAM-level backstop matching the broker's own
protected-policy check):

```bash
cat > /tmp/github-agent-org-permissions.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowOrgReadAndOverrideWrites",
      "Effect": "Allow",
      "Action": [
        "organizations:DescribeOrganization", "organizations:ListRoots",
        "organizations:ListAccounts", "organizations:ListPolicies",
        "organizations:ListPoliciesForTarget", "organizations:DescribePolicy",
        "organizations:UpdatePolicy", "organizations:ListTagsForResource",
        "ssm:GetParameter", "ssm:GetParameters"
      ],
      "Resource": "*"
    },
    {
      "Sid": "DenyTouchingTheProtectedPublicIpSCP",
      "Effect": "Deny",
      "Action": ["organizations:DetachPolicy", "organizations:DeletePolicy", "organizations:UpdatePolicy"],
      "Resource": "arn:aws:organizations::<ACCOUNT_ID>:policy/<ORG_ID>/service_control_policy/<no-public-ip-ec2 policy ID>"
    }
  ]
}
EOF
aws iam put-role-policy --profile sandbox \
  --role-name github-agent-ou-policy-demo \
  --policy-name agent-org-permissions \
  --policy-document file:///tmp/github-agent-org-permissions.json
```

Also tag the real `no-public-ip-ec2` SCP itself as protected -- this is an
independently-verifiable source of truth in the AWS console (Organizations >
Policies > SCP > Tags), separate from `policy_registry.py`'s local data.
`agent_bedrock.py` checks this tag in addition to the local registry for the
real account (`broker.is_real_scp_tagged_protected`), so the two can't
silently drift without the AWS-side check catching it:

```bash
aws organizations tag-resource --profile sandbox \
  --resource-id <no-public-ip-ec2 policy ID> \
  --tags Key=Protected,Value=true
```

If you later wire more local-mock accounts' broker path to real per-account
AssumeRole grants too, give those roles their own narrow trust policy rather
than widening this one.

### Repo configuration

```bash
gh variable set AWS_ROLE_ARN --body "<role arn from above>"
gh variable set AWS_REGION --body "us-east-1"
gh variable set BEDROCK_GUARDRAIL_ID --body "$(jq -r .guardrail_id guardrail_config.json)"
gh variable set BEDROCK_GUARDRAIL_VERSION --body "$(jq -r .guardrail_version guardrail_config.json)"
gh variable set AGENT_MODEL_ID --body "qwen.qwen3-coder-next"
# Set to "1" before a live demo to exercise the real-AWS broker path without
# it mutating live SCP JSON.
gh variable set BROKER_DRY_RUN --body "1"
```

### Using it

1. Open an issue using the **Policy block / access request** template
   (fields: account, policy ID if known, what's blocked, what you're trying
   to do).
2. If you're a maintainer/collaborator, the `agent-respond` workflow runs
   automatically and comments back the outcome.
3. If the opener isn't a collaborator, the issue gets labeled
   `needs-maintainer-approval` instead. A maintainer can trigger the agent by
   adding the `approved-run` label, or commenting `/run-agent` on the issue.
4. Any maintainer/collaborator can re-run the agent on an existing issue at
   any time by commenting `/run-agent` (e.g. after editing the issue, or to
   get a second take given LLM-phrasing variance). Comments from
   non-collaborators or bots are ignored.
5. Check the Actions tab for the run, and the issue comments for the result.

## Design

### Thesis

Config files and prompts are not enforcement. Only infra-level controls
(IAM, policy-as-code) are. Two agents, identical tools, identical system
prompt, identical mandate text -- only the platform wrapper differs. The
demo proves enforcement lives outside the model, not inside a prompt.

### Code structure

| File | Role |
|---|---|
| `agent/policy_registry.py` | Deterministic `check_policy_registry(policy_id, target_account)`. Seeded with protected/unprotected policies, fails closed (unknown IDs treated as protected). |
| `agent/tools.py` | Shared `SYSTEM_PROMPT`, `TOOL_SCHEMAS`, and the 4 tool implementations (`check_policy_registry`, `propose_workaround`, `update_org_policy`, `request_human_approval`). Two `update_org_policy` variants: `_raw` (direct, no check) and `_brokered` (routes through `broker.py`). Also owns `validate_tool_input` -- structural/allowlist validation of the model's tool-call arguments, run before any business logic. |
| `agent/broker.py` | JIT access broker -- the real enforcement boundary. Checks the registry/SSM allow-list before issuing any credential or making a real AWS write; account-boundary assertion as a hard backstop independent of broker logic correctness. Where wired to a real AWS Organizations account, does real `organizations:UpdatePolicy` calls against actual SCPs, scoped per-account via `aws:PrincipalAccount` conditions. |
| `agent/agent_common.py` | The shared tool-calling loop both variants drive. The LLM decides which tool to call and in what order -- nothing here hardcodes a sequence. Also force-corrects known facts (e.g. `target_account`, `new_state`) the model has been observed mangling, and prepends a deterministic verified-outcome banner ahead of the model's own narration. |
| `agent/bedrock_client.py` | Adapter using the Bedrock Converse API, so the loop works against any model provider with no code changes. |
| `agent/agent_direct.py` | **model-direct** variant -- same model, no guardrail attached, no intermediate check before `update_org_policy` executes. This is the gap the demo illustrates. |
| `agent/agent_bedrock.py` | **Bedrock+Guardrails** variant. Before `update_org_policy` runs: deterministic registry check first; only if that says protected, also calls `ApplyGuardrail` on the agent's last reasoning text as a second layer. Either flag → block, return `propose_workaround` output instead, log which layer caught it. |
| `agent/logger.py` | One-line-per-step plain-text trace, not JSON. |
| `infra/guardrail_setup.py` | One-time setup: creates/updates the Bedrock Guardrail (denied topic + word filters tied to `no-public-ip-ec2`) in the AWS account. Idempotent. |
| `github_integration/github_issue_agent.py` | GitHub-issue entry point: parses an issue form, drives a two-round flow (round 1: assessment + proposed workaround only, no write tools reachable; round 2: the requester's reply must end in either a real policy update or a human-in-the-loop escalation), and writes back a comment with the outcome. |
| `conftest.py` | Puts `agent/` and `github_integration/` on `sys.path` so tests can import their modules by bare name, same as the entry-point scripts do. |
| `tests/` | Pure-Python unit tests -- no AWS creds or network calls needed. |

### The two agent variants

**model-direct** (`agent/agent_direct.py`) -- `update_org_policy` executes
immediately when the model calls it. No broker, no guardrail, no registry
check at the tool layer. Nothing infra-level stops it, regardless of what
the shared system prompt says.

**Bedrock+Guardrails** (`agent/agent_bedrock.py`) -- same model, same
prompt, same tools. Before `update_org_policy` executes:
1. `check_policy_registry(policy_id, target_account)` runs first,
   unconditionally -- the deterministic check.
2. For an account wired to a real SCP, the real SCP's own AWS tag
   (`Protected`) is checked too, independent of the local registry, so the
   two sources of truth can't silently drift.
3. If protected by either: `ApplyGuardrail` also runs on the agent's last
   reasoning text as an additional layer, then the call is blocked
   regardless of the guardrail's verdict. Logs `BLOCKED` plus which
   layer(s) caught it.
4. If not protected: the brokered write runs (a real AWS write where wired
   to a real account, a local-mock grant otherwise).

The system prompt is intentionally neutral -- it does not instruct the
agent to route prod/permanent changes through human approval. The only
thing that actually blocks unsafe actions is the broker/registry/guardrail
code path in the Bedrock variant, never prompt text shared by both
variants.

### JIT access broker

`agent/broker.py` sits in front of every brokered `update_org_policy` call:
- Runs the registry/SSM-allow-list check first. Denied → no credential
  issued, no AWS write made.
- Allowed → issues a scoped grant (local-mock accounts) or makes a real,
  account-scoped `organizations:UpdatePolicy` call (accounts wired to a real
  AWS Organizations member account). Duration is capped at
  `MAX_DURATION_SECONDS` regardless of what was requested.
- `_assert_account_boundary` hard-fails on any account outside the known
  set -- models the IAM role's trust-policy condition, the backstop that
  holds even if the broker's own logic above it has a bug.
- `BROKER_DRY_RUN=1` runs the full real-AWS lookup/decide/diff logic
  without the final write -- for exercising the real account safely (e.g.
  before a live demo).

### The deterministic-vs-LLM boundary

The model decides *which tool to call and when*; it never decides *whether
an action is allowed*. Every point where the model's literal output would
otherwise be trusted as fact -- which account it's targeting, which exact
tag or instance type to grant, whether a write actually executed -- is
instead pinned to a value the code already knows or extracts deterministically
from the original request text, and the model's copy is overridden if it
disagrees. This is the same principle applied repeatedly throughout the
codebase, not a one-off fix: the prompt doesn't enforce anything; the code
does.

### Two-round GitHub issue flow

Round 1 is assessment-only -- the model can read the registry and propose a
workaround, but the tool-calling loop's `allowed_tools` gate makes
`update_org_policy`/`request_human_approval` unreachable no matter what the
model decides. Round 2 (the requester's reply) must end in a terminal
outcome: either a real policy update attempt or a human-in-the-loop
escalation, with a deterministic fallback that auto-escalates if the model
just talks without calling either tool. Round counting is done by scanning
the bot's own past comments for a marker string, not a label or state file.
