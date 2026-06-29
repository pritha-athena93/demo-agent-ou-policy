"""Bedrock+Guardrails variant. Same model, same prompt, same tools as
agent_direct.py. Before update_org_policy is allowed to execute, intercept
the agent's latest reasoning text and: (1) call check_policy_registry,
(2) only if that says protected, also run ApplyGuardrail as a second check
on top. Either flag -> do not execute, return propose_workaround's output
instead, log the block + which layer caught it.

Registry runs first and unconditionally because it's the deterministic
check the spec requires to be identical every take. ApplyGuardrail is a
semantic/fuzzy topic classifier, not a literal match -- gating it behind
"registry already says protected" keeps the unprotected path (scenario C)
deterministic by construction, since registry alone already decides it.
On the protected path (scenario B) registry would block on its own too;
the guardrail call there is belt-and-suspenders, matching the spec's
defense-in-depth intent without leaking the classifier's fuzziness into
the case where it doesn't change the outcome.
"""

import json
import os
import boto3

from bedrock_client import BedrockMessagesClient
from agent_common import run_agent_loop
from tools import DISPATCH_BEDROCK, propose_workaround
from policy_registry import check_policy_registry
import broker
import logger

# TEMP: open-source model, see note in agent_direct.py.
MODEL_ID = os.environ.get("AGENT_MODEL_ID", "qwen.qwen3-coder-next")
VARIANT = "bedrock-guardrails"
REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_PROFILE = os.environ.get("AWS_PROFILE")  # unset in CI -- uses default credential chain (OIDC-assumed role)


def _load_guardrail_config() -> dict:
    env_id = os.environ.get("BEDROCK_GUARDRAIL_ID")
    env_version = os.environ.get("BEDROCK_GUARDRAIL_VERSION")
    if env_id and env_version:
        return {"guardrail_id": env_id, "guardrail_version": env_version}
    with open("guardrail_config.json") as f:
        return json.load(f)


_GUARDRAIL = _load_guardrail_config()


def _apply_guardrail(bedrock_runtime, reasoning_text: str) -> bool:
    """Returns True if the guardrail flagged the text (i.e. would block)."""
    if not reasoning_text.strip():
        return False
    resp = bedrock_runtime.apply_guardrail(
        guardrailIdentifier=_GUARDRAIL["guardrail_id"],
        guardrailVersion=_GUARDRAIL["guardrail_version"],
        source="OUTPUT",
        content=[{"text": {"text": reasoning_text}}],
    )
    return resp.get("action") == "GUARDRAIL_INTERVENED"


def run(mandate_text: str, account: str, policy_id_hint: str | None = None, allowed_tools: set | None = None) -> dict:
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=REGION) if AWS_PROFILE else boto3.Session(region_name=REGION)
    runtime = session.client("bedrock-runtime")
    client = BedrockMessagesClient(runtime, MODEL_ID)

    def before_tool(tool_name, tool_input, last_reasoning_text):
        if tool_name == "request_human_approval":
            logger.log(VARIANT, account, "request_human_approval", "PENDING-APPROVAL",
                       tool_input.get("summary", "")[:80])
            return None

        if tool_name != "update_org_policy":
            return None

        policy_id = tool_input.get("policy_id")
        # target_account is already guaranteed correct here -- agent_common's
        # run_agent_loop corrects it (and logs if it had to) before
        # validate_tool_input/before_tool ever run, since the account is a
        # known fact, not something the model should be trusted to restate.
        target_account = tool_input.get("target_account", account)
        registry_check = check_policy_registry(policy_id, target_account)
        registry_flagged = registry_check["protected"]

        if registry_flagged:
            guardrail_flagged = _apply_guardrail(runtime, last_reasoning_text)
            layer = "guardrail+policy-registry" if guardrail_flagged else "policy-registry"
            logger.log(VARIANT, account, f"update_org_policy({policy_id})",
                       "BLOCKED", f"caught by {layer}")
            return propose_workaround(f"blocked change to {policy_id}", policy_id)

        # Deterministic SSM-backed allowlist gate, only relevant to the real
        # instance-type SCP. t3.* is already permitted org-wide, so only a
        # non-t3 request needs checking against the on-request allowlist.
        if policy_id == broker.INSTANCE_TYPE_SCP_NAME:
            instance_type = tool_input.get("new_state", "")
            if not broker.is_t3_family(instance_type) and not broker.is_instance_type_overridable(instance_type):
                logger.log(VARIANT, account, f"update_org_policy({policy_id})",
                           "BLOCKED", "caught by ssm-allowlist, not eligible for auto-override")
                return propose_workaround(f"blocked change to {policy_id}: {instance_type} not on the SSM allow-list", policy_id)

        logger.log(VARIANT, account, f"update_org_policy({policy_id})",
                   "ALLOWED-CHECK-PASSED", "registry clear, not a protected policy")
        return None

    result = run_agent_loop(client, MODEL_ID, mandate_text, DISPATCH_BEDROCK, VARIANT, account,
                             before_tool=before_tool, allowed_tools=allowed_tools)
    return result
