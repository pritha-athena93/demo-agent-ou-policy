"""JIT access broker. Deterministic. Stands between any agent and the real write.

The agent-ops role has no standing permission to call update_org_policy anywhere.
This broker is the only path to a short-lived, scoped credential. Even if the
broker's own logic above this line has a bug, the role's trust-policy condition
(account-ID match) is the real backstop -- enforced again here as a hard assert
to mirror what IAM would refuse regardless of what the broker decided.

demo-prod-core is a real AWS Organizations member account (see
REAL_AWS_INTEGRATION.md). The other three account names are the original
local-mock accounts used by run_scenario.py's A-E scenarios and never touch
real AWS. Account IDs and SCP IDs for the real account are looked up by name
at call time -- never hardcoded -- so they survive the org being torn down
and recreated.

Set BROKER_DRY_RUN=1 to do the full real-AWS lookup/describe/diff for
grant_instance_type_exception/grant_tag_exception without the final
update_policy call -- useful for a live demo against demo-prod-core where a
glitch shouldn't be able to leave a real org policy in a bad state. The
returned dict's `dry_run` key reflects which mode produced it.
"""

import datetime
import json
import os
import re
import uuid

import boto3

from policy_registry import check_policy_registry

VALID_ACCOUNTS = {"dev-sandbox", "dev-shared", "prod-core", "demo-prod-core"}
MAX_DURATION_SECONDS = 3600

REAL_ACCOUNT_NAME = "demo-prod-core"
INSTANCE_TYPE_SCP_NAME = "allow-only-t3-instance-types"
TAG_SCP_NAME = "require-resource-tags"
ACCOUNT_EXCEPTION_SID_SUFFIX = "AccountException"

# Cost-allocation tags the require-resource-tags SCP actually enforces (see
# policy_registry.py's description). The tool schema tells the model
# new_state should be the exact tag name, but on repeat occasions (e.g.
# issue #11) it has instead passed a descriptive sentence ("Owner tag not
# required during..."), which fails an exact Sid match. The Sid lookup below
# is the real enforcement boundary, not the model's phrasing, so resolve a
# known tag name out of arbitrary text here rather than trusting the model
# to format it.
KNOWN_TAG_NAMES = ["Owner", "Department"]

AWS_PROFILE = os.environ.get("AWS_PROFILE")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# When set, grant_instance_type_exception/grant_tag_exception still do the
# full real-AWS lookup/describe/diff (so the broker's decision logic is
# exercised exactly as normal) but skip the final update_policy call --
# returns what would have been written instead of mutating the live SCP.
# For demo safety: a glitch on stage shouldn't be able to leave a real org
# policy in a bad state. Off by default -- 1/true/yes (case-insensitive) to
# enable.
BROKER_DRY_RUN = os.environ.get("BROKER_DRY_RUN", "").strip().lower() in ("1", "true", "yes")

_exceptions: dict = {}
_org_client = None


class BrokerDenied(Exception):
    pass


def _organizations_client():
    global _org_client
    if _org_client is None:
        session = boto3.Session(profile_name=AWS_PROFILE) if AWS_PROFILE else boto3.Session()
        _org_client = session.client("organizations", region_name=AWS_REGION)
    return _org_client


def get_account_id(account_name: str) -> str:
    """Looks up the real AWS account ID by name every call -- no hardcoded
    account number anywhere in this codebase. Fails closed if not found."""
    client = _organizations_client()
    paginator = client.get_paginator("list_accounts")
    for page in paginator.paginate():
        for acct in page["Accounts"]:
            if acct["Name"] == account_name:
                return acct["Id"]
    raise BrokerDenied(f"no AWS account named '{account_name}' found in the organization")


def _find_policy_by_name(policy_name: str) -> dict:
    client = _organizations_client()
    paginator = client.get_paginator("list_policies")
    for page in paginator.paginate(Filter="SERVICE_CONTROL_POLICY"):
        for policy in page["Policies"]:
            if policy["Name"] == policy_name:
                return policy
    raise BrokerDenied(f"no SCP named '{policy_name}' found in the organization")


def is_real_scp_tagged_protected(policy_name: str) -> bool:
    """Real AWS tag check, independent of policy_registry.py's local data.

    policy_registry.py's REGISTRY is local Python -- for the real
    demo-prod-core SCPs, nothing ties its "protected" field to the actual
    AWS resource; the two could drift (someone could detach/recreate the
    SCP in the console with the same name and a different protection
    posture, and this codebase would never know). This queries the real
    SCP's own AWS tags (Key=Protected) as an independently-verifiable
    source of truth for the real-AWS path specifically -- see
    agent_bedrock.py's before_tool, which checks this in addition to the
    local registry for target_account == REAL_ACCOUNT_NAME. Fails closed:
    treats "tag missing" or "policy not found" the same as protected,
    rather than silently treating an AWS error as "not protected"."""
    try:
        policy = _find_policy_by_name(policy_name)
        client = _organizations_client()
        tags = client.list_tags_for_resource(ResourceId=policy["Id"])["Tags"]
    except BrokerDenied:
        return True
    tag_value = next((t["Value"] for t in tags if t["Key"] == "Protected"), "")
    return tag_value.strip().lower() == "true"


def get_allowed_instance_types_from_ssm() -> list:
    session = boto3.Session(profile_name=AWS_PROFILE) if AWS_PROFILE else boto3.Session()
    ssm = session.client("ssm", region_name=AWS_REGION)
    resp = ssm.get_parameter(Name="/demo/policy/allowed-instance-types-on-request")
    return json.loads(resp["Parameter"]["Value"])


def is_t3_family(instance_type: str) -> bool:
    return instance_type.startswith("t3.")


def is_instance_type_overridable(instance_type: str) -> bool:
    """Deterministic, SSM-backed -- never an LLM judgment call.

    Entries ending in ".*" match an entire family by prefix (e.g. "t4g.*"
    covers t4g.nano through t4g.16xlarge, including any new size AWS adds
    later) -- exact-list membership alone can't express "all of a family"
    without enumerating every current size and going stale when AWS adds
    more."""
    for entry in get_allowed_instance_types_from_ssm():
        if entry.endswith(".*"):
            if instance_type.startswith(entry[:-1]):
                return True
        elif instance_type == entry:
            return True
    return False


_INSTANCE_TYPE_PATTERN = re.compile(r"\b[a-z][0-9]+[a-z]*\.[0-9]*[a-z]+\b", re.IGNORECASE)


def find_known_instance_type(text: str) -> str | None:
    """Extracts an EC2-instance-type-shaped token (e.g. 't4g.small',
    'm5.4xlarge') out of arbitrary text, or None if none appears. Used to
    resolve new_state from the original issue/reply text directly rather
    than trusting the model's restated copy of it -- see
    find_known_tag_name's docstring for why that copy can't be trusted."""
    match = _INSTANCE_TYPE_PATTERN.search(text)
    return match.group(0).lower() if match else None


def find_known_tag_name(text: str) -> str | None:
    """Extracts a known tag name (Owner/Department) out of arbitrary text,
    or None if neither appears. This is the generalized form of
    _resolve_tag_name, usable on the *original* issue/reply text (which
    reliably states the real tag name, e.g. "without an Owner tag") rather
    than on the model's new_state argument, which has been observed
    mangling it three different ways (a full sentence, the literal string
    'none', and -- separately -- the wrong target_account entirely). Same
    fix shape as target_account in agent_common.py: stop trusting the
    model's restatement of a fact the code can already pin down itself."""
    for tag in KNOWN_TAG_NAMES:
        if re.search(rf"\b{re.escape(tag)}\b", text, re.IGNORECASE):
            return tag
    return None


def grant_instance_type_exception(target_account: str, instance_type: str) -> dict:
    """Real AWS UpdatePolicy call. Scopes the widened instance-type allow-list
    to target_account only via an aws:PrincipalAccount condition, so other
    accounts under the same SCP stay restricted to t3-only. Idempotent --
    re-running for an already-approved type is a no-op AWS-side."""
    client = _organizations_client()
    account_id = get_account_id(target_account)
    policy = _find_policy_by_name(INSTANCE_TYPE_SCP_NAME)
    policy_id = policy["Id"]
    content = json.loads(client.describe_policy(PolicyId=policy_id)["Policy"]["Content"])

    statements = content["Statement"]
    # Sid must be alphanumeric only (no hyphens) per IAM policy grammar --
    # account_id is already digits-only, safe to use directly.
    exception_sid = f"Account{account_id}{ACCOUNT_EXCEPTION_SID_SUFFIX}"
    base_statement = next((s for s in statements if ACCOUNT_EXCEPTION_SID_SUFFIX not in s["Sid"]), None)
    if base_statement is None:
        # A schema-shape surprise on the real policy (e.g. every statement
        # already carries the exception suffix, or the policy is empty)
        # should fail closed with a readable error, not propagate a raw
        # StopIteration from the bare next(...) this replaced.
        raise BrokerDenied(
            f"no base statement found in SCP '{policy_id}' without the "
            f"'{ACCOUNT_EXCEPTION_SID_SUFFIX}' suffix -- policy shape changed?"
        )

    base_statement.setdefault("Condition", {}).setdefault("StringNotEquals", {})["aws:PrincipalAccount"] = account_id

    exception_statement = next((s for s in statements if s["Sid"] == exception_sid), None)
    if exception_statement is None:
        exception_statement = {
            "Sid": exception_sid,
            "Effect": "Deny",
            "Action": base_statement["Action"],
            "Resource": base_statement["Resource"],
            "Condition": {
                "StringNotLike": {"ec2:InstanceType": ["t3.*"]},
                "StringEquals": {"aws:PrincipalAccount": account_id},
            },
        }
        statements.append(exception_statement)

    allowed_patterns = exception_statement["Condition"]["StringNotLike"]["ec2:InstanceType"]
    if instance_type not in allowed_patterns:
        allowed_patterns.append(instance_type)

    if not BROKER_DRY_RUN:
        client.update_policy(PolicyId=policy_id, Content=json.dumps(content))
    return {
        "policy_id": policy_id, "account_id": account_id, "allowed_patterns": allowed_patterns,
        "dry_run": BROKER_DRY_RUN,
    }


def _resolve_tag_name(raw: str) -> str:
    """Extract a known tag name (exact Sid casing) out of free text. Returns
    `raw` unchanged if no known tag name appears in it, so the caller's
    not-found error still reports what was actually passed in."""
    if raw in KNOWN_TAG_NAMES:
        return raw
    for tag in KNOWN_TAG_NAMES:
        if re.search(rf"\b{re.escape(tag)}\b", raw, re.IGNORECASE):
            return tag
    return raw


def grant_tag_exception(target_account: str, tag_name: str) -> dict:
    """Real AWS UpdatePolicy call. Scopes the dropped tag requirement to
    target_account only, leaving the requirement enforced for every other
    account under the same SCP."""
    client = _organizations_client()
    account_id = get_account_id(target_account)
    policy = _find_policy_by_name(TAG_SCP_NAME)
    policy_id = policy["Id"]
    content = json.loads(client.describe_policy(PolicyId=policy_id)["Policy"]["Content"])

    resolved_tag = _resolve_tag_name(tag_name)
    target_sid = f"DenyRunInstancesWithout{resolved_tag}Tag"
    statement = next((s for s in content["Statement"] if s["Sid"] == target_sid), None)
    if statement is None:
        raise BrokerDenied(f"no tag requirement statement found for tag '{tag_name}'")

    statement.setdefault("Condition", {}).setdefault("StringNotEquals", {})["aws:PrincipalAccount"] = account_id
    if not BROKER_DRY_RUN:
        client.update_policy(PolicyId=policy_id, Content=json.dumps(content))
    return {
        "policy_id": policy_id, "account_id": account_id, "exempted_tag": resolved_tag,
        "dry_run": BROKER_DRY_RUN,
    }


def _assert_account_boundary(target_account: str) -> None:
    if target_account not in VALID_ACCOUNTS:
        raise BrokerDenied(f"trust-policy condition rejected unknown account: {target_account}")


def request_scoped_credential(policy_id: str, target_account: str, duration_seconds: int | None) -> dict:
    _assert_account_boundary(target_account)

    check = check_policy_registry(policy_id, target_account)
    if check["protected"]:
        raise BrokerDenied(f"policy '{policy_id}' is protected, no grant issued")

    capped_duration = min(duration_seconds, MAX_DURATION_SECONDS) if duration_seconds else MAX_DURATION_SECONDS
    credential_id = str(uuid.uuid4())
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=capped_duration)

    grant = {
        "credential_id": credential_id,
        "scoped_to_policy": policy_id,
        "scoped_to_account": target_account,
        "expires_at": expires_at.isoformat(),
        "permanent": duration_seconds is None,
    }

    if duration_seconds is not None:
        _exceptions[credential_id] = grant

    return grant


def is_exception_active(credential_id: str) -> bool:
    grant = _exceptions.get(credential_id)
    if grant is None:
        return False
    expires_at = datetime.datetime.fromisoformat(grant["expires_at"])
    return datetime.datetime.now(datetime.timezone.utc) < expires_at
