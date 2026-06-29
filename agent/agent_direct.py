"""model-direct variant. Calls the same Bedrock model as the guardrails
variant, no guardrail attached. update_org_policy executes immediately when
the model calls it -- no intermediate check, no broker, no guardrail. This
is the gap the demo shows.

Named "model-direct," not "Claude-direct": MODEL_ID currently defaults to an
open-source model (see the note below), and nothing should claim Claude is
running unless it actually is. If AGENT_MODEL_ID is set to a us.anthropic.*
inference profile, this variant does call real Claude -- the variant's
behavior (no enforcement layer) is identical either way; only the model
identity changes.
"""

import os
import boto3

from bedrock_client import BedrockMessagesClient
from agent_common import run_agent_loop
from tools import DISPATCH_DIRECT
import logger

# TEMP: open-source model, no Anthropic Bedrock access approved yet in
# sandbox account. Swap back to an anthropic.* / us.anthropic.* model ID
# once the use-case form is approved -- agent loop/tool code is unchanged.
MODEL_ID = os.environ.get("AGENT_MODEL_ID", "qwen.qwen3-coder-next")
VARIANT = "model-direct"
REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_PROFILE = os.environ.get("AWS_PROFILE")  # unset in CI -- uses default credential chain


def run(mandate_text: str, account: str) -> dict:
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=REGION) if AWS_PROFILE else boto3.Session(region_name=REGION)
    runtime = session.client("bedrock-runtime")
    client = BedrockMessagesClient(runtime, MODEL_ID)

    def before_tool(tool_name, tool_input, last_reasoning_text):
        if tool_name == "update_org_policy":
            logger.log(VARIANT, account, f"update_org_policy({tool_input.get('policy_id')})",
                       "ALLOWED", "no intermediate check on this path")
        return None

    result = run_agent_loop(client, MODEL_ID, mandate_text, DISPATCH_DIRECT, VARIANT, account,
                             before_tool=before_tool)
    return result
