# Agent + Guardrails Demo

Two tool-calling agents, identical tools/prompt/mandate. One calls Claude
direct, one calls Claude via Amazon Bedrock with Bedrock Guardrails attached.
Same task, only the platform wrapper differs. See `DESIGN.md` for the
architecture and why each piece exists.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -q boto3 anthropic python-dotenv

# Configure an AWS profile with Bedrock access in your target account/region,
# e.g. `aws configure --profile sandbox`.

# Create the Bedrock Guardrail (idempotent, safe to re-run after editing config):
AWS_PROFILE=sandbox .venv/bin/python guardrail_setup.py
```

This writes `guardrail_config.json` (gitignored — environment-specific).

## Running the three scenarios locally

```bash
rm -f scenario_trace.log   # optional, keeps the trace clean for a fresh run
AWS_PROFILE=sandbox .venv/bin/python run_scenario.py A   # claude-direct, protected policy -> succeeds (the risk)
AWS_PROFILE=sandbox .venv/bin/python run_scenario.py B   # bedrock+guardrails, same request -> blocked
AWS_PROFILE=sandbox .venv/bin/python run_scenario.py C   # bedrock+guardrails, unprotected policy -> temporary scoped override
cat scenario_trace.log
```

Each run prints a trace line:
`<timestamp> | <variant> | <account> | <action> | <outcome> | <why>`

Because a real LLM decides which tools to call, exact behavior can vary
between takes (especially scenario A/B — the model may choose the cautious
workaround instead of attempting the policy change). Re-run if a scenario
doesn't land on the outcome you want to show; this variance is expected, not
a bug — see `DESIGN.md`'s reliability notes.

### Using a different model

Both agents read `AGENT_MODEL_ID` from the environment (falls back to the
open-source `qwen.qwen3-coder-next` stand-in):
```bash
AGENT_MODEL_ID="us.anthropic.claude-sonnet-4-5-20250929-v1:0" AWS_PROFILE=sandbox .venv/bin/python run_scenario.py A
```
Requires the account's Anthropic-model use-case form to be approved in the
Bedrock console (Model access page) — Bedrock blocks Anthropic models
without it. No other code changes needed.

## GitHub integration

Lets people raise an issue describing what's blocked and where; an agent
(Bedrock+Guardrails variant only) runs automatically for trusted issue
openers and comments back the outcome. Full rationale in `DESIGN.md`.

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

Note: the broker and `update_org_policy` are local-mock Python in this demo,
not real AWS API calls, so the CI role only needs Bedrock access. If you
later wire the broker to real per-account AssumeRole grants, give those
roles their own narrow trust policy rather than widening this one.

### Repo configuration

```bash
gh variable set AWS_ROLE_ARN --body "<role arn from above>"
gh variable set AWS_REGION --body "us-east-1"
gh variable set BEDROCK_GUARDRAIL_ID --body "$(jq -r .guardrail_id guardrail_config.json)"
gh variable set BEDROCK_GUARDRAIL_VERSION --body "$(jq -r .guardrail_version guardrail_config.json)"
gh variable set AGENT_MODEL_ID --body "qwen.qwen3-coder-next"
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

### Testing the issue handler without GitHub

```bash
AWS_PROFILE=sandbox ISSUE_BODY='### Account

dev-sandbox

### Policy ID (if known)

max-ebs-volume-size-dev

### What is blocked?

Cant resize EBS volume past 500GB

### What are you trying to do?

Need a bigger volume for this sprint dev workload' .venv/bin/python github_issue_agent.py
cat issue_comment.md
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Scenario A doesn't call `update_org_policy` | Model played it safe instead of "fixing forward" — free-text reasoning varies by take | Re-run |
| Scenario B doesn't show `BLOCKED` (no `update_org_policy` call at all) | Model went straight to the workaround in text without calling the tool | Re-run |
| Scenario C unexpectedly shows `BLOCKED` | Should not happen — `agent_bedrock.py` only calls `ApplyGuardrail` when the registry already says protected, and `max-ebs-volume-size-dev` never does. If you see this, the registry or gating logic regressed | File a bug, don't just re-run |
| `ResourceNotFoundException: ... use case details` | Anthropic use-case form not yet approved for this AWS account on Bedrock | Use the open-source stand-in model (default), or wait for approval |
| `ValidationException: ... on-demand throughput isn't supported` | Used a bare model ID instead of an inference profile ID | Use a `us.anthropic.*`-prefixed profile ID, not bare `anthropic.*` |

## Files

See `DESIGN.md` for the architecture table and rationale. Every code file
has a companion `<name>_doc.md` with what it does, how, and how to use it.
